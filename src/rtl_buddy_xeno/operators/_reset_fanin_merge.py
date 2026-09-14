"""``RESET_FANIN_MERGE`` — combine two resets into one fan-in wire.

The reset-side structural operator ``RESET_POLARITY_FLIP`` is not:
that one flips the *edge* on an existing reset, while this one changes
the reset's *fan-in* — two distinct reset sources gated into one wire
that then drives a flop's async reset. That is exactly the RDC-005
(multi-source reset fan-in) shape, which rtl-buddy-cdc#230's coverage
report found locked to whichever corpus parents already carried it.

.. code-block:: systemverilog

    // parent
    input  logic global_rst_n, local_rst_n;
    always_ff @(posedge clk or negedge global_rst_n)
        if (!global_rst_n) q <= 1'b0;
        else               q <= d;

    // mutant
    input  logic global_rst_n, local_rst_n;
    wire global_rst_n_local_rst_n_xeno_merge_1 = global_rst_n & local_rst_n;
    always_ff @(posedge clk or negedge global_rst_n_local_rst_n_xeno_merge_1)
        if (!global_rst_n_local_rst_n_xeno_merge_1) q <= 1'b0;
        else                                        q <= d;

**Parser layer: Verible CST**, for structure only — block spans,
sensitivity-list edges, per-module scoping and precise leaf rewrites.
*Which* signals are resets is the name heuristic in
:mod:`._reset_names` (xeno#15 option 1), shared with
``CLOCK_POLARITY_SWAP`` so the two can't disagree. No pyslang.

**Every occurrence is rewritten.** The rewrite replaces *all*
``SymbolIdentifier`` leaves naming the used reset inside the block —
the sensitivity edge, the ``if (!rst_n)`` polarity check, any other
reference — not just the edge. Rewriting the edge alone leaves the
if-body testing the old signal, which is the Yosys-rejected shape
(``ERROR: Async reset … yields non-constant value``) that xeno#12's
reset-edge skip exists to avoid on ``CLOCK_POLARITY_SWAP``.

**Gate choice.** ``&`` for an active-low reset (``negedge`` edge:
either source asserting low pulls the merge low), ``|`` for an
active-high one (``posedge``: either source asserting high pulls it
high). A mixed-polarity pair yields no site at all — no single gate
combines an active-low and an active-high reset correctly, and
emitting one anyway would produce a mutant that is merely wrong
rather than interestingly mutated.

**Scoping.** The reset pool is rebuilt per ``kModuleDeclaration``
subtree *from that module's own declarations only* (ports, variables,
nets — never a named-port label or a bare reference), so a merge can
only ever name a signal the module actually declares; anything else
would splice a wire referencing an undeclared identifier and the
mutant wouldn't elaborate.

**Block flavours.** ``always`` is accepted alongside ``always_ff`` on
purpose: Verilog-2001 corpora spell their reset flops ``always
@(posedge clk or negedge rst_n)`` and this operator only needs the
reset edge, not the ``_ff`` keyword.

Issue: xeno#15. Discovery: rtl-buddy-cdc#230's coverage gap on
RDC-005 (3 mutant cases).
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterator
from dataclasses import dataclass

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site
from rtl_buddy_xeno.operators import _chain_helpers as _chain
from rtl_buddy_xeno.operators._reset_names import is_active_low, is_reset_name

_INFIX = "xeno_merge"

# Gate per edge polarity of the reset the flop already uses.
_GATE: dict[str, str] = {"negedge": "&", "posedge": "|"}

# Seed packing (see :attr:`_MergeSite.seed`): the block offset is
# shifted left past two pool-index fields so that two pairs landing on
# the same block still get distinct seeds.
_SEED_BLOCK_STRIDE = 4096
_SEED_INDEX_STRIDE = 64


@dataclass(frozen=True)
class _MergeSite:
    """One (pair, flop) merge opportunity.

    ``used`` is the reset the flop's sensitivity list already carries,
    ``other`` the one being merged in, ``gate`` the operator joining
    them, and ``leaf_spans`` the byte spans of every
    ``SymbolIdentifier`` leaf naming ``used`` inside the block (source
    order — the rewrite walks them backwards).

    ``first_index`` / ``second_index`` are the positions of the pair's
    two members in the module's reset pool; they exist only so the
    seed can distinguish two pairs that resolve to the same block.
    """

    used: str
    other: str
    gate: str
    block_start: int
    block_end: int
    first_index: int
    second_index: int
    leaf_spans: tuple[tuple[int, int], ...]

    @property
    def seed(self) -> int:
        """Deterministic, per-parent-and-operator unique site seed.

        Packs all three values that define the site —
        ``block_start * 4096 + first_index * 64 + second_index`` — so
        two pairs whose first applicable block is the *same* block
        (``block_start`` alone is not unique: pairs ``(a, b)`` and
        ``(a, c)`` routinely resolve to the same flop) still differ.
        The strides are generous for the field they cover: a reset pool
        is the reset-named *declarations* of one module, a handful of
        names in practice, so 64 slots per index never aliases on real
        parents. A module with >64 reset-named declarations would
        collide two seeds, which costs nothing beyond a shared RNG
        stream. Stable across runs: every input is a byte offset or a
        pool position, both derived from the source text alone.
        """
        return (
            self.block_start * _SEED_BLOCK_STRIDE
            + self.first_index * _SEED_INDEX_STRIDE
            + self.second_index
        )


def _direct_children(node: dict, tag: str) -> list[dict]:
    """Direct children of ``node`` whose ``tag`` matches."""
    return [
        child
        for child in (node.get("children", []) or [])
        if isinstance(child, dict) and child.get("tag") == tag
    ]


def _first_identifier_leaf(node: dict) -> tuple[int, str] | None:
    """``(start byte, text)`` of the lowest-offset identifier under ``node``."""
    leaves = sorted(_cst.walk_tokens(node, "SymbolIdentifier"))
    if not leaves:
        return None
    start, _end, text = leaves[0]
    return start, text


def _ansi_port_names(node: dict) -> list[tuple[int, str]]:
    """Declared names of a ``kPortDeclaration`` (ANSI header port).

    Shape: ``kPortDeclaration(input, kDataType(...), kUnqualifiedId(
    SymbolIdentifier), kUnpackedDimensions)``. Only the *direct*
    ``kUnqualifiedId`` children are declarators — a user-defined port
    type (``input my_pkg::rst_t rst_n``) puts its own
    ``kUnqualifiedId`` inside ``kDataType``.
    """
    found = []
    for child in _direct_children(node, "kUnqualifiedId"):
        leaf = _first_identifier_leaf(child)
        if leaf is not None:
            found.append(leaf)
    return found


def _nonansi_port_names(node: dict) -> list[tuple[int, str]]:
    """Declared names of a ``kModulePortDeclaration`` (non-ANSI ``input x;``).

    Shape: ``kModulePortDeclaration(input, kDataType(...),
    kIdentifierList(kIdentifierUnpackedDimensions(kUnqualifiedId(
    SymbolIdentifier), kUnpackedDimensions), ...))``. The header's own
    ``kPort`` / ``kPortReference`` entries are deliberately *not* read:
    every non-ANSI port is re-declared here, and the header form also
    covers ``module m (.rst_n(internal))``, where ``rst_n`` names a
    port face that the body cannot reference.
    """
    found = []
    for id_list in _direct_children(node, "kIdentifierList"):
        for declarator in _direct_children(id_list, "kIdentifierUnpackedDimensions"):
            for child in _direct_children(declarator, "kUnqualifiedId"):
                leaf = _first_identifier_leaf(child)
                if leaf is not None:
                    found.append(leaf)
    return found


def _variable_names(node: dict) -> list[tuple[int, str]]:
    """Declared names of a ``kDataDeclaration`` (``logic rst_n;``).

    ``kDataDeclaration`` is also the tag Verible gives a *module
    instantiation* (``child u (.local_rst_n(foo));``), where the
    declarator is a ``kGateInstance`` and the named-port labels are
    plain ``SymbolIdentifier`` leaves inside it. Reading only
    ``kRegisterVariable`` declarators keeps instance labels, instance
    names and port faces out of the pool.
    """
    found = []
    for declarator in _cst.walk_subtrees(node, "kRegisterVariable"):
        leaf = _first_identifier_leaf(declarator)
        if leaf is not None:
            found.append(leaf)
    return found


def _net_names(node: dict) -> list[tuple[int, str]]:
    """Declared names of a ``kNetDeclaration`` (``wire rst_n = ...;``).

    The declarator is ``kNetVariable``; taking its first identifier
    leaves any right-hand-side references out of the pool.
    """
    found = []
    for declarator in _cst.walk_subtrees(node, "kNetVariable"):
        leaf = _first_identifier_leaf(declarator)
        if leaf is not None:
            found.append(leaf)
    return found


# CST node tag -> "declared names of this declaration" extractor. These
# four tags are the module-scope declaration forms; anything else in a
# module subtree is a reference, a port face or a type name and must
# never reach the pool.
_DECLARATION_READERS = {
    "kPortDeclaration": _ansi_port_names,
    "kModulePortDeclaration": _nonansi_port_names,
    "kDataDeclaration": _variable_names,
    "kNetDeclaration": _net_names,
}


def _reset_pool(module_node: dict) -> list[str]:
    """Reset-named identifiers *declared* in a module, source order.

    Only module-scope declarations count — ANSI and non-ANSI port
    declarations, variable declarations and net declarations. A name
    that merely *occurs* in the module (a named-port label such as
    ``.local_rst_n(foo)``, a package or hierarchical member, any bare
    reference) is not a signal this module can drive a wire from: the
    emitted ``wire … = a & b;`` would reference an undeclared
    identifier and the mutant wouldn't elaborate.
    """
    declared: list[tuple[int, str]] = []
    for tag, reader in _DECLARATION_READERS.items():
        for node in _cst.walk_subtrees(module_node, tag):
            declared.extend(reader(node))
    pool: list[str] = []
    seen: set[str] = set()
    for _start, text in sorted(declared):
        if text in seen or not is_reset_name(text):
            continue
        seen.add(text)
        pool.append(text)
    return pool


def _edge_signals(always_node: dict) -> list[tuple[str, str]]:
    """``(edge token, signal name)`` per edged sensitivity-list entry.

    Source order. A ``kEventExpression`` whose first child is not a
    ``posedge`` / ``negedge`` leaf is a level-sensitive entry (plain
    ``always @(a or b)``) and carries no edge, so it is skipped.
    """
    edges: list[tuple[int, str, str]] = []
    for ev in _cst.walk_subtrees(always_node, "kEventExpression"):
        children = [c for c in (ev.get("children", []) or []) if isinstance(c, dict)]
        if len(children) < 2 or children[0].get("tag") not in _GATE:
            continue
        name = _chain.first_identifier(children[1])
        if not name:
            continue
        try:
            start, _end = _cst.node_span(ev)
        except ValueError:
            continue
        edges.append((start, str(children[0]["tag"]), name))
    edges.sort()
    return [(edge, name) for _start, edge, name in edges]


def _always_blocks(module_node: dict) -> list[tuple[int, int, dict]]:
    """``(start, end, node)`` per ``kAlwaysStatement``, source order.

    ``kAlwaysStatement`` covers ``always``, ``always_ff`` and
    ``always_comb`` alike; the flavours are separated by the edge
    requirement in :func:`_edge_signals`, not by the tag. Plain
    ``always`` is accepted deliberately, not by omission: a
    Verilog-2001 reset flop is spelled ``always @(posedge clk or
    negedge rst_n)`` and carries exactly the reset edge this operator
    rewrites, so excluding it would blind the operator to every
    pre-SystemVerilog corpus parent.
    """
    blocks: list[tuple[int, int, dict]] = []
    seen: set[tuple[int, int]] = set()
    for node in _cst.walk_subtrees(module_node, "kAlwaysStatement"):
        try:
            span = _cst.node_span(node)
        except ValueError:
            continue
        if span in seen:
            continue
        seen.add(span)
        blocks.append((span[0], span[1], node))
    blocks.sort(key=lambda item: (item[0], item[1]))
    return blocks


def _leaf_spans(always_node: dict, name: str) -> tuple[tuple[int, int], ...]:
    """Byte spans of every ``SymbolIdentifier`` leaf reading ``name``."""
    return tuple(
        (start, end)
        for start, end, text in sorted(
            _cst.walk_tokens(always_node, "SymbolIdentifier")
        )
        if text == name
    )


def _site_for_pair(
    blocks: list[tuple[int, int, dict]],
    first: str,
    second: str,
    first_index: int,
    second_index: int,
) -> _MergeSite | None:
    """First flop (source order) whose reset edge names ``first`` or ``second``.

    The pair yields exactly one site — or none, when no edged block in
    the module resets off either member. ``first_index`` /
    ``second_index`` are the pair's pool positions, carried through
    only to keep the site's seed unique (see :attr:`_MergeSite.seed`).
    """
    for block_start, block_end, node in blocks:
        for edge, signal in _edge_signals(node):
            if signal not in (first, second):
                continue
            other = second if signal == first else first
            spans = _leaf_spans(node, signal)
            if not spans:
                continue
            return _MergeSite(
                used=signal,
                other=other,
                gate=_GATE[edge],
                block_start=block_start,
                block_end=block_end,
                first_index=first_index,
                second_index=second_index,
                leaf_spans=spans,
            )
    return None


def _find_sites(sv: str) -> list[_MergeSite]:
    """Every merge site in ``sv``, pair order within module order."""
    path = _chain.sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[_MergeSite] = []
    for module_node in _cst.walk_subtrees(cst_root, "kModuleDeclaration"):
        pool = _reset_pool(module_node)
        if len(pool) < 2:
            continue
        blocks = _always_blocks(module_node)
        if not blocks:
            continue
        for (i, first), (j, second) in itertools.combinations(enumerate(pool), 2):
            if is_active_low(first) != is_active_low(second):
                continue
            site = _site_for_pair(blocks, first, second, i, j)
            if site is not None:
                sites.append(site)
    return sites


_ATTR_OPEN = b"(*"
_ATTR_CLOSE = b"*)"
_WHITESPACE = (b" ", b"\t", b"\n", b"\r")


def _insertion_point(data: bytes, block_start: int) -> int:
    """``block_start``, walked back over attribute instances bound to the block.

    ``(* keep *) always_ff @(...)`` parses as one module item, but the
    ``kAlwaysStatement`` span starts at the ``always`` keyword —
    Verible's CST carries no node for the attribute at all, so the
    span cannot be widened from the tree. Splicing the wire at
    ``block_start`` would land it *between* the attribute and the
    block, silently re-binding ``(* keep *)`` to the synthesised wire
    and leaving the flop unannotated. So the walk-back is textual:
    while the source immediately before the insertion point (modulo
    whitespace) closes an attribute, move the point to that
    attribute's ``(*``. The loop repeats for stacked attributes.

    Guards against a false positive on a comment that merely ends in
    ``*)``: the candidate attribute body must contain no comment
    delimiter, and no ``//`` may precede the ``(*`` on its own line.
    Any doubt returns the unadjusted offset, which is only ever the
    pre-existing behaviour.
    """
    point = block_start
    while True:
        cursor = point
        while cursor > 0 and data[cursor - 1 : cursor] in _WHITESPACE:
            cursor -= 1
        if not data[:cursor].endswith(_ATTR_CLOSE):
            return point
        open_at = data.rfind(_ATTR_OPEN, 0, cursor - len(_ATTR_CLOSE))
        if open_at < 0:
            return point
        body = data[open_at:cursor]
        if b"//" in body or b"/*" in body or b"*/" in body:
            return point
        line_start = data.rfind(b"\n", 0, open_at) + 1
        if b"//" in data[line_start:open_at]:
            return point
        point = open_at


def _insert_before_block(sv: str, block_start: int, line: str) -> str:
    """Splice ``line`` in on its own source line ahead of the block.

    The insertion point is :func:`_insertion_point`, which is
    ``block_start`` unless the block carries attribute instances — the
    line has to go ahead of those, not between them and the block.
    Indentation is read off the front of the insertion point (the run
    of spaces / tabs immediately preceding it) and re-emitted both
    before the inserted line and after its newline, so what follows
    keeps the indentation it had. Same idea as ``insert_after_block``
    in :mod:`._chain_helpers`, mirrored to the other side of the block;
    it lives here because this is its only consumer.
    """
    data = sv.encode("utf-8")
    insert_at = _insertion_point(data, block_start)
    indent_start = insert_at
    while indent_start > 0 and data[indent_start - 1 : indent_start] in (b" ", b"\t"):
        indent_start -= 1
    indent = data[indent_start:insert_at].decode("utf-8")
    addition = f"{line}\n{indent}".encode()
    return (data[:insert_at] + addition + data[insert_at:]).decode("utf-8")


def _apply(sv: str, site: _MergeSite, new_name: str) -> str:
    """Rewrite every ``used`` leaf in the block, then declare the wire.

    Highest offset first so each splice leaves the lower offsets
    valid; the declaration lands last because it sits at
    ``block_start``, below every leaf it must not shift.
    """
    mutated = sv
    for start, end in sorted(site.leaf_spans, reverse=True):
        mutated = _chain.replace_span(mutated, start, end, new_name)
    declaration = f"wire {new_name} = {site.used} {site.gate} {site.other};"
    return _insert_before_block(mutated, site.block_start, declaration)


def _predict(used: str, other: str, new_name: str, line: int) -> Prediction:
    """Conservative prediction — no positive CDC-rule claim.

    RDC-005 (multi-source reset fan-in) is the rule this operator is
    built to exercise, but it fires only when the fan-in lands on a
    flop that is part of an otherwise clean reset distribution. The
    Verible CST view sees one ``always_ff`` and two reset-named
    identifiers in the enclosing module; it never walks the reset
    distribution the flop sits in, so the operator emits without
    verifying that context and ``cdc_rules_added`` stays empty. The
    rationale names RDC-005 so a downstream consumer still sees
    operator intent, and rtl-buddy-cdc#221's coverage report measures
    the actual fire.

    ``perturbs_signals`` is a positive prediction: both merged resets
    and the synthesised wire are provably in the perturbed cone.
    """
    return Prediction(
        rationale=(
            f"combined two reset signals (`{used}` & `{other}`) into a single "
            f"fan-in wire `{new_name}` driving the async reset of the flop at "
            f"line {line}. RDC-005 (multi-source reset fan-in) fires when the "
            "fan-in lands on a flop that's part of a clean reset distribution "
            "— the operator emits without verifying that downstream context, "
            "so cdc_rules_added stays empty. The rationale names RDC-005 so a "
            "downstream consumer sees operator intent"
        ),
        perturbs_signals=frozenset({used, other, new_name}),
        perturbs_liveness=False,
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_sites(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        site = sites[idx]
        new_name = _chain.fresh_identifier(sv, f"{site.used}_{site.other}", _INFIX)
        line, _ = _chain.byte_to_line_col(sv, site.block_start)
        yield Mutant(
            sv=_apply(sv, site, new_name),
            diff_summary=(
                f"line {line}: merge resets `{site.used}` & `{site.other}` into "
                f"`{new_name}` for the flop at line {line}"
            ),
            seed=site.seed,
            prediction=_predict(site.used, site.other, new_name, line),
            kind=MutationKind.RESET_FANIN_MERGE,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for site in sorted(_find_sites(sv), key=lambda s: (s.block_start, s.other)):
        line, column = _chain.byte_to_line_col(sv, site.block_start)
        new_name = _chain.fresh_identifier(sv, f"{site.used}_{site.other}", _INFIX)
        yield Site(
            kind=MutationKind.RESET_FANIN_MERGE,
            line=line,
            column=column,
            snippet=f"reset `{site.used}` -> `{site.used}` {site.gate} `{site.other}`",
            prediction=_predict(site.used, site.other, new_name, line),
        )


operator = _mutants
candidates = _candidates
