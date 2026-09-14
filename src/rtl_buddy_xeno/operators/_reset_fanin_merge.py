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
subtree, so a reset named in one module is never merged into another
module's block — relevant on any multi-module corpus parent, where a
cross-module merge would reference an out-of-scope identifier and the
mutant wouldn't elaborate.

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


@dataclass(frozen=True)
class _MergeSite:
    """One (pair, flop) merge opportunity.

    ``used`` is the reset the flop's sensitivity list already carries,
    ``other`` the one being merged in, ``gate`` the operator joining
    them, and ``leaf_spans`` the byte spans of every
    ``SymbolIdentifier`` leaf naming ``used`` inside the block (source
    order — the rewrite walks them backwards).
    """

    used: str
    other: str
    gate: str
    block_start: int
    block_end: int
    leaf_spans: tuple[tuple[int, int], ...]


def _reset_pool(module_node: dict) -> list[str]:
    """Reset-named identifiers in a module, first-occurrence order.

    Every ``SymbolIdentifier`` leaf in the module subtree is a
    candidate — ports, locals, references — deliberately, because a
    reset that is only ever *used* (never declared in this module's
    port list, e.g. a package-level or hierarchically-driven one) is
    still a legitimate fan-in source.
    """
    pool: list[str] = []
    seen: set[str] = set()
    for _start, _end, text in sorted(_cst.walk_tokens(module_node, "SymbolIdentifier")):
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
    ``always_comb`` alike; the latter two are separated by the edge
    requirement in :func:`_edge_signals`, not by the tag.
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
    blocks: list[tuple[int, int, dict]], first: str, second: str
) -> _MergeSite | None:
    """First flop (source order) whose reset edge names ``first`` or ``second``.

    The pair yields exactly one site — or none, when no edged block in
    the module resets off either member.
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
        for first, second in itertools.combinations(pool, 2):
            if is_active_low(first) != is_active_low(second):
                continue
            site = _site_for_pair(blocks, first, second)
            if site is not None:
                sites.append(site)
    return sites


def _insert_before_block(sv: str, block_start: int, line: str) -> str:
    """Splice ``line`` in on its own source line ahead of the block.

    Indentation is read off the front of the block (the run of spaces
    / tabs immediately preceding ``block_start``) and re-emitted both
    before the inserted line and after its newline, so the block keeps
    the indentation it had. Same idea as ``insert_after_block`` in
    :mod:`._chain_helpers`, mirrored to the other side of the block;
    it lives here because this is its only consumer.
    """
    data = sv.encode("utf-8")
    indent_start = block_start
    while indent_start > 0 and data[indent_start - 1 : indent_start] in (b" ", b"\t"):
        indent_start -= 1
    indent = data[indent_start:block_start].decode("utf-8")
    addition = f"{line}\n{indent}".encode()
    return (data[:block_start] + addition + data[block_start:]).decode("utf-8")


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
            seed=site.block_start,
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
