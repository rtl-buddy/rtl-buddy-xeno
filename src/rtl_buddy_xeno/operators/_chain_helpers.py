"""Shared sync-chain primitives for the chain-shaped CDC operators.

Same spirit as :mod:`._cond_helpers`: the operators that reason about
a *sync chain* all need the same site-discovery surface, so the walker
lives here once and every consumer sees an identical candidate set.

Consumers:

- :mod:`._sync_chain_depth_perturb` — deletes a stage (chain depth -1).
- :mod:`._chain_stage_insert` — synthesises a stage (chain depth +1).
- :mod:`._comb_between_stages` — interposes a comb cell on a chain edge
  (chain depth unchanged, chain *purity* broken).

Everything here sits in the **Verible CST** parser layer (see the
no-straddle rule in :mod:`rtl_buddy_xeno.operators`): site discovery
via :mod:`rtl_buddy_xeno.cst`, emission via byte-splices on the
original source string. No pyslang.

Two stage *shapes* are recognised (xeno#34).

**A — the reset-free stage** (the shape ``SYNC_CHAIN_DEPTH_PERTURB``
shipped with):

1. The ``always_ff`` block's sensitivity list has exactly one edge
   token (``posedge`` / ``negedge``) on a single signal — the clock.
2. The block contains exactly one statement: a non-blocking assignment
   ``LHS <= RHS;`` (no ``if``, ``case``, loops, blocking assignments).
3. The LHS is a bare identifier (not a bit-select, not a hierarchical
   reference).

**B — the async-reset stage** (``begin``/``end``-wrapped or bare):

.. code-block:: systemverilog

    always_ff @(posedge dst_clk or negedge rst_n)
        if (!rst_n) sync_meta <= 1'b0;
        else        sync_meta <= src_q;

1. The sensitivity list has exactly **two** edge-led event
   expressions.
2. The body — after unwrapping at most one ``begin``/``end`` — is
   exactly one ``if``/``else``; each branch is exactly one
   non-blocking assignment, and both assign the *same bare
   identifier*. A third statement anywhere disqualifies the block.
3. The ``if`` branch assigns a **constant**: an expression that names
   no identifier at all and contains at least one number literal.
   That admits ``1'b0``, ``'0``, ``8'h00`` and replications of a
   literal (``{8{1'b0}}``), and deliberately rejects
   parameter-width spellings such as ``{WIDTH{1'b0}}`` — a name is a
   name and the recogniser stays inside the CST.
4. Exactly one of the two sensitivity identifiers appears in the
   ``if`` condition; that edge is the **reset**, the other is the
   **clock**. Neither (``if (en) ...``) or both (``if (!rst_n &&
   clk)``) is ambiguous and disqualifies the block.
5. Either the reset identifier satisfies
   :func:`._reset_names.is_reset_name`, **or** the condition is
   exactly ``<rst>`` / ``!<rst>`` / ``~<rst>``. Either signal alone
   suffices: the name heuristic catches ``if (soft_clear)`` on an
   ``rst_n``-named edge, and the bare-polarity form catches a
   vendor-spelled reset (``por_ni``) the name table does not list.

Shape B is what rtl-buddy-cdc's fuzz corpus writes for 20 of its 35
canonical parents, so without it ``CHAIN_STAGE_INSERT`` and
``COMB_BETWEEN_STAGES`` find almost no sites there (xeno#34).
``SYNC_CHAIN_DEPTH_PERTURB`` keeps the *shape A only* site set — see
:func:`find_stage_spans`.

A recognised stage additionally carries the two facts the two
*insertion* operators need and the deletion operator does not: the
``kModuleDeclaration`` it lives in (:func:`find_downstream_reader`
never leaves that subtree) and, when it can be recovered, the stage
LHS's declared data type (:func:`declared_type`).
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno.operators import _reset_names

__all__ = [
    "ReaderRef",
    "ResetStageShape",
    "SyncStage",
    "byte_to_line_col",
    "declared_type",
    "find_downstream_reader",
    "find_stage_spans",
    "find_sync_stages",
    "first_identifier",
    "fresh_identifier",
    "insert_after_block",
    "is_single_clock_sensitivity",
    "replace_span",
    "reset_bearing_stage",
    "single_nonblocking",
    "sv_to_tempfile",
]


# --- source plumbing ---------------------------------------------------------


def sv_to_tempfile(sv: str) -> Path:
    """Materialise ``sv`` under a content-hashed temp path.

    The CST facade parses files, not strings, and view's cache keys on
    content — so the same source text always lands on the same path and
    re-parses are cache hits.

    The publish is atomic: the bytes go to a unique sibling name (pid +
    random suffix) and are then :func:`os.replace`'d onto the target.
    Writing the shared, content-derived path in place would let a
    concurrent reader — another thread or process mutating the same
    source — open a half-written file, and Verible would report a
    syntax error on source that is perfectly valid.
    """
    digest = hashlib.sha256(sv.encode("utf-8")).hexdigest()[:16]
    tmpdir = Path(tempfile.gettempdir()) / "rtl-buddy-xeno"
    tmpdir.mkdir(parents=True, exist_ok=True)
    target = tmpdir / f"sv-{digest}.sv"
    try:
        if target.read_text(encoding="utf-8") == sv:
            return target
    except OSError:
        pass
    staging = tmpdir / f".sv-{digest}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    try:
        staging.write_text(sv, encoding="utf-8")
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def byte_to_line_col(sv: str, byte_offset: int) -> tuple[int, int]:
    """Convert a UTF-8 byte offset into 1-based ``(line, column)``."""
    head = sv.encode("utf-8")[:byte_offset]
    line = head.count(b"\n") + 1
    last_newline = head.rfind(b"\n")
    column = (byte_offset - last_newline) if last_newline >= 0 else byte_offset + 1
    return line, column


def replace_span(sv: str, start: int, end: int, text: str) -> str:
    """Replace the byte range ``[start, end)`` of ``sv`` with ``text``.

    Byte-offset arithmetic (not str indices) because every offset in
    this module comes from Verible's CST, which counts bytes.
    """
    data = sv.encode("utf-8")
    return (data[:start] + text.encode("utf-8") + data[end:]).decode("utf-8")


def insert_after_block(
    sv: str,
    block_start: int,
    block_end: int,
    lines: list[str],
) -> str:
    """Splice ``lines`` in right after the block spanning ``[start, end)``.

    Each entry of ``lines`` lands on its own source line, indented to
    match the block's own leading indentation (captured by looking back
    from ``block_start`` to the previous newline). The block's trailing
    newline is left where it is, so the result is:

    .. code-block:: text

        <indent>always_ff @(posedge clk) q <= d;<inserted lines>
        <rest of file>

    ``block_start`` is a parameter (rather than being derived from
    ``block_end``) purely because the indent can only be read from the
    *front* of the block.
    """
    if not lines:
        return sv
    data = sv.encode("utf-8")
    indent_start = block_start
    while indent_start > 0 and data[indent_start - 1 : indent_start] in (b" ", b"\t"):
        indent_start -= 1
    indent = data[indent_start:block_start].decode("utf-8")
    addition = "".join(f"\n{indent}{line}" for line in lines)
    return (data[:block_end] + addition.encode("utf-8") + data[block_end:]).decode(
        "utf-8"
    )


def fresh_identifier(sv: str, base: str, infix: str) -> str:
    """Return ``f"{base}_{infix}_{n}"`` for the smallest unused ``n >= 1``.

    "Unused" means the candidate name does not occur in ``sv`` as a
    whole word. An existing trailing ``_{infix}_<digits>`` is stripped
    off ``base`` first, so re-applying an operator to its own output
    yields ``q_xeno_stage_1`` then ``q_xeno_stage_2`` rather than
    ``q_xeno_stage_1_xeno_stage_1``.
    """
    stem = re.sub(rf"_{re.escape(infix)}_\d+$", "", base)
    n = 1
    while re.search(rf"\b{re.escape(f'{stem}_{infix}_{n}')}\b", sv):
        n += 1
    return f"{stem}_{infix}_{n}"


# --- CST shape recognisers ---------------------------------------------------


def _direct_children(node: dict) -> list[dict]:
    """Immediate ``dict`` children of ``node`` (Verible pads with nulls)."""
    return [c for c in (node.get("children", []) or []) if isinstance(c, dict)]


def _first_direct(node: dict, tag: str) -> dict | None:
    """First immediate child of ``node`` tagged ``tag``, or ``None``."""
    for child in _direct_children(node):
        if child.get("tag") == tag:
            return child
    return None


def _first_identifier_leaf(node: Any) -> dict | None:
    """Return the first ``SymbolIdentifier`` leaf node under ``node``."""
    if isinstance(node, dict):
        if node.get("tag") == "SymbolIdentifier" and node.get("text"):
            return node
        for child in node.get("children", []) or []:
            leaf = _first_identifier_leaf(child)
            if leaf is not None:
                return leaf
    elif isinstance(node, list):
        for child in node:
            leaf = _first_identifier_leaf(child)
            if leaf is not None:
                return leaf
    return None


def first_identifier(node: Any) -> str | None:
    """Return the text of the first ``SymbolIdentifier`` leaf under ``node``."""
    leaf = _first_identifier_leaf(node)
    return str(leaf["text"]) if leaf is not None else None


def is_single_clock_sensitivity(always_ff_node: dict) -> bool:
    """Return True iff the always_ff has exactly one ``posedge``/``negedge`` edge.

    Walks the event control to count edge tokens. A sync-chain stage
    has one clock edge and no async reset; if we see two edges,
    it's likely a clock+reset pair and we skip.
    """
    event_controls = _cst.walk_subtrees(always_ff_node, "kEventExpressionList")
    if not event_controls:
        # Fallback: a single kEventExpression with no list wrapper.
        evs = _cst.walk_subtrees(always_ff_node, "kEventExpression")
        return len(evs) == 1
    edge_count = 0
    for ev in _cst.walk_subtrees(always_ff_node, "kEventExpression"):
        children = [c for c in (ev.get("children", []) or []) if isinstance(c, dict)]
        if children and children[0].get("tag") in ("posedge", "negedge"):
            edge_count += 1
    return edge_count == 1


def single_nonblocking(always_ff_node: dict) -> tuple[str, int, int] | None:
    """If the always_ff body is exactly one non-blocking assignment
    ``LHS <= RHS;``, return ``(lhs_name, block_start, block_end)``.

    The block start/end span the entire ``always_ff @(...) STMT;``
    construct so a deletion can splice it cleanly out and an insertion
    can append right behind it.
    """
    nb_assigns = _cst.walk_subtrees(always_ff_node, "kNonblockingAssignmentStatement")
    if len(nb_assigns) != 1:
        return None
    # Reject if the body has any other statement-shape: if, case,
    # blocking-assignment, etc. Walking these tags inside the block
    # and finding any of them disqualifies the block.
    disqualifying = (
        "kConditionalStatement",
        "kCaseStatement",
        "kBlockingAssignmentStatement",
        "kForLoopStatement",
        "kWhileLoopStatement",
    )
    for tag in disqualifying:
        if _cst.walk_subtrees(always_ff_node, tag):
            return None
    nb = nb_assigns[0]
    lhs_name = first_identifier(nb)
    if lhs_name is None:
        return None
    try:
        block_start, block_end = _cst.node_span(always_ff_node)
    except ValueError:
        return None
    return lhs_name, block_start, block_end


def _leads_with_edge(event_expression: dict) -> bool:
    """True iff a ``kEventExpression``'s first child is an edge token."""
    children = [
        c for c in (event_expression.get("children", []) or []) if isinstance(c, dict)
    ]
    return bool(children) and children[0].get("tag") in ("posedge", "negedge")


def _node_text(sv: str, node: dict) -> str | None:
    """Source text under ``node``, or ``None`` when it has no byte span."""
    try:
        start, end = _cst.node_span(node)
    except ValueError:
        return None
    return sv.encode("utf-8")[start:end].decode("utf-8")


def _normalise(text: str) -> str:
    """Collapse every run of whitespace to one space and strip the ends."""
    return " ".join(text.split())


def _clock_edge_text(sv: str, always_ff_node: dict) -> str:
    """Literal sensitivity text of the stage's clock, e.g. ``posedge clk``.

    Copied verbatim out of the source by the single ``kEventExpression``'s
    byte span, so whatever the author wrote (spacing, clock expression)
    survives into a synthesised sibling flop. Returns ``""`` when the
    event expression can't be pinned down unambiguously — callers that
    need to *emit* a clock (as opposed to merely deleting a block) must
    treat an empty string as "skip this stage", and
    :func:`find_downstream_reader` never finds a reader for such a
    stage (it has no clock to match the reader's against).
    """
    evs = _cst.walk_subtrees(always_ff_node, "kEventExpression")
    edged = [ev for ev in evs if _leads_with_edge(ev)]
    chosen = edged[0] if len(edged) == 1 else (evs[0] if len(evs) == 1 else None)
    if chosen is None:
        return ""
    text = _node_text(sv, chosen)
    return text.strip() if text is not None else ""


# --- async-reset stage shape (xeno#34) ---------------------------------------


@dataclass(frozen=True)
class ResetStageShape:
    """The extra facts a *shape B* (async-reset) stage carries.

    ``sensitivity_text`` is the whole ``@(...)`` interior verbatim
    (``posedge dst_clk or negedge rst_n``), ``clock_edge_text`` the
    clock edge alone (``posedge dst_clk``) so the reader's same-clock
    check keeps comparing clocks and never resets, ``reset_cond_text``
    the ``if (...)`` condition verbatim (``!rst_n``) and
    ``reset_const_text`` the reset branch's right-hand side verbatim
    (``1'b0``). An operator that synthesises a sibling flop re-emits
    all three unchanged, so the new stage carries exactly the parent's
    reset.
    """

    lhs_name: str
    block_start: int
    block_end: int
    sensitivity_text: str
    clock_edge_text: str
    reset_cond_text: str
    reset_const_text: str


def _event_control(always_ff_node: dict) -> dict | None:
    """The ``kEventControl`` an ``always_ff`` opens with, or ``None``.

    Scoped lookup (immediate children only) so an intra-assignment
    event control buried in the body can never be mistaken for the
    block's sensitivity list.
    """
    timing = _first_direct(always_ff_node, "kProceduralTimingControlStatement")
    if timing is None:
        return None
    return _first_direct(timing, "kEventControl")


def _guarded_statement(always_ff_node: dict) -> dict | None:
    """The single statement the event control guards, ``begin``/``end`` peeled.

    Verible lays an ``always_ff`` out as ``kAlwaysStatement >
    kProceduralTimingControlStatement > [kEventControl, <statement>]``
    (probed with ``verible-verilog-syntax --printtree``). The
    ``<statement>`` is either the statement itself or a ``kSeqBlock``
    wrapping a ``kBlockItemStatementList``; exactly one statement in
    that list is unwrapped, anything else returns ``None``. That is
    what makes "an ``if``/``else`` plus a third statement" fall out of
    the site set structurally rather than by tag-counting.
    """
    timing = _first_direct(always_ff_node, "kProceduralTimingControlStatement")
    if timing is None:
        return None
    children = _direct_children(timing)
    index = next(
        (i for i, c in enumerate(children) if c.get("tag") == "kEventControl"), None
    )
    if index is None:
        return None
    rest = children[index + 1 :]
    if len(rest) != 1:
        return None
    body = rest[0]
    if body.get("tag") != "kSeqBlock":
        return body
    statements = _first_direct(body, "kBlockItemStatementList")
    if statements is None:
        return None
    inner = _direct_children(statements)
    return inner[0] if len(inner) == 1 else None


def _identifier_leaves(node: Any) -> list[str]:
    """Every ``SymbolIdentifier`` leaf text under ``node``, in tree order."""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("tag") == "SymbolIdentifier" and node.get("text"):
            found.append(str(node["text"]))
        for child in node.get("children", []) or []:
            found.extend(_identifier_leaves(child))
    elif isinstance(node, list):
        for child in node:
            found.extend(_identifier_leaves(child))
    return found


def _is_constant_expression(node: dict) -> bool:
    """True iff ``node`` is a literal constant this module will re-emit.

    Deliberately blunt and conservative: an expression that names *no*
    identifier and holds at least one ``kNumber`` leaf. ``1'b0``,
    ``'0``, ``8'h00`` and ``{8{1'b0}}`` pass; ``{WIDTH{1'b0}}``,
    ``RESET_VALUE`` and anything reading a signal do not, even though
    a parameter is constant too — the recogniser has no elaboration
    and a name it cannot resolve is a name it will not vouch for.
    """
    if _first_identifier_leaf(node) is not None:
        return False
    return bool(_cst.walk_subtrees(node, "kNumber"))


def _bare_lpvalue_name(sv: str, nb_assign: dict) -> str | None:
    """The assignment's LHS when it is a *bare* identifier, else ``None``.

    "Bare" is checked textually: the whole ``kLPValue`` span has to
    normalise to the single identifier it contains, so ``q[3]``,
    ``q.f`` and ``{a, b}`` are all rejected.
    """
    lpvalue = _first_direct(nb_assign, "kLPValue")
    if lpvalue is None:
        return None
    name = first_identifier(lpvalue)
    text = _node_text(sv, lpvalue)
    if name is None or text is None or _normalise(text) != name:
        return None
    return name


def _single_nonblocking_child(branch: dict | None) -> dict | None:
    """The one ``kNonblockingAssignmentStatement`` under an if/else branch."""
    if branch is None:
        return None
    children = _direct_children(branch)
    if len(children) != 1:
        return None
    child = children[0]
    return child if child.get("tag") == "kNonblockingAssignmentStatement" else None


def _sensitivity_text(sv: str, always_ff_node: dict) -> str:
    """The whole ``@(...)`` interior verbatim, or ``""``."""
    event_control = _event_control(always_ff_node)
    if event_control is None:
        return ""
    listing = _first_direct(
        _first_direct(event_control, "kParenGroup") or {}, "kEventExpressionList"
    )
    text = _node_text(sv, listing) if listing is not None else None
    return text.strip() if text is not None else ""


def _reset_condition_ok(reset_name: str, condition_text: str) -> bool:
    """Either signal suffices — a reset *name* or a bare-polarity condition.

    ``is_reset_name`` alone would miss a vendor-spelled reset the name
    table does not list; the bare-polarity form alone would miss
    ``if (soft_clear)`` guarding a ``negedge rst_n`` edge. Accepting
    either keeps both idioms in the corpus visible while still
    refusing a two-edge block whose condition is a general expression
    on a signal that is neither named like a reset nor tested plainly.
    """
    if _reset_names.is_reset_name(reset_name):
        return True
    # ``if (! por_ni)`` is the same condition as ``if (!por_ni)``: SV
    # allows whitespace between a unary operator and its operand, and
    # ``_normalise`` keeps one space there, so close that gap before
    # the exact comparison (review on xeno#35).
    normalised = re.sub(r"^([!~])\s+", r"\1", _normalise(condition_text))
    return normalised in {
        reset_name,
        f"!{reset_name}",
        f"~{reset_name}",
    }


def reset_bearing_stage(sv: str, always_ff_node: dict) -> ResetStageShape | None:
    """Recognise *shape B* — the async-reset sync stage (xeno#34).

    Returns ``None`` for anything that is not exactly the shape the
    module docstring spells out. The caller tries the reset-free shape
    first; the two are mutually exclusive by construction (shape A
    forbids a ``kConditionalStatement``, shape B requires one).
    """
    event_control = _event_control(always_ff_node)
    if event_control is None:
        return None
    edges = [
        ev
        for ev in _cst.walk_subtrees(event_control, "kEventExpression")
        if _leads_with_edge(ev)
    ]
    if len(edges) != 2:
        return None

    conditional = _guarded_statement(always_ff_node)
    if conditional is None or conditional.get("tag") != "kConditionalStatement":
        return None
    if_clause = _first_direct(conditional, "kIfClause")
    else_clause = _first_direct(conditional, "kElseClause")
    if if_clause is None or else_clause is None:
        return None
    header = _first_direct(if_clause, "kIfHeader")
    if header is None:
        return None
    paren = _first_direct(header, "kParenGroup")
    condition = _first_direct(paren, "kExpression") if paren is not None else None
    if condition is None:
        return None

    reset_assign = _single_nonblocking_child(_first_direct(if_clause, "kIfBody"))
    data_assign = _single_nonblocking_child(_first_direct(else_clause, "kElseBody"))
    if reset_assign is None or data_assign is None:
        return None
    lhs_name = _bare_lpvalue_name(sv, reset_assign)
    if lhs_name is None or _bare_lpvalue_name(sv, data_assign) != lhs_name:
        return None

    reset_rhs = _rhs_expression(reset_assign)
    if reset_rhs is None or not _is_constant_expression(reset_rhs):
        return None
    reset_const_text = _node_text(sv, reset_rhs)
    condition_text = _node_text(sv, condition)
    if reset_const_text is None or condition_text is None:
        return None

    condition_names = set(_identifier_leaves(condition))
    matched = [ev for ev in edges if (first_identifier(ev) or "") in condition_names]
    if len(matched) != 1:
        return None
    reset_edge = matched[0]
    clock_edge = edges[0] if edges[1] is reset_edge else edges[1]
    reset_name = first_identifier(reset_edge)
    if reset_name is None:
        return None
    if not _reset_condition_ok(reset_name, condition_text):
        return None

    clock_edge_text = _node_text(sv, clock_edge)
    sensitivity_text = _sensitivity_text(sv, always_ff_node)
    if not clock_edge_text or not sensitivity_text:
        return None
    try:
        block_start, block_end = _cst.node_span(always_ff_node)
    except ValueError:
        return None
    return ResetStageShape(
        lhs_name=lhs_name,
        block_start=block_start,
        block_end=block_end,
        sensitivity_text=sensitivity_text,
        clock_edge_text=clock_edge_text.strip(),
        reset_cond_text=condition_text.strip(),
        reset_const_text=reset_const_text.strip(),
    )


# --- declared-type lookup ----------------------------------------------------

# The data types an insertion operator is willing to copy onto a
# synthesised object: a packed 4-state/2-state vector or scalar, with an
# optional signedness keyword. Everything else — typedef names (enums,
# structs, unions), `int`/`integer`/`byte`, implicit types — is returned
# as None, because a synthesised sibling declared with a *copy* of the
# text is only equivalent to the original for these forms.
_COPYABLE_TYPE_RE = re.compile(
    r"^(logic|reg|bit|wire)(\s+(signed|unsigned))?(\s*\[[^\]]+\])*$"
)

# Declaration shapes searched for the stage LHS, in the order Verible
# spells them (probed with `verible-verilog-syntax --export_json
# --printtree`):
#
#   kDataDeclaration  logic [7:0] a, b;
#     kInstantiationBase > kInstantiationType > kDataType
#     kInstantiationBase > kGateInstanceRegisterVariableList
#                        > kRegisterVariable(SymbolIdentifier,
#                                            kUnpackedDimensions)
#   kNetDeclaration   wire [2:0] na, nb;   /  wire nc = clk;
#     kDataType (the `wire` keyword alone — the packed dimensions hang
#     off a sibling kDataTypeImplicitIdDimensions)
#     kNetVariableDeclarationAssign > kNetVariable | kNetDeclarationAssignment
#   kPortDeclaration  output logic [7:0] q
#     kDataType, kUnqualifiedId(SymbolIdentifier), kUnpackedDimensions
#
# Because the packed dimensions do not always sit inside the kDataType
# node (see kNetDeclaration), the type text is taken as the source
# between the start of the kDataType and the start of the *first*
# declarator name — which is also what makes `logic [7:0] a, b;` work
# for either declarator.
_DECLARATION_TAGS = ("kDataDeclaration", "kNetDeclaration", "kPortDeclaration")

_DECLARATOR_TAGS = (
    "kRegisterVariable",
    "kNetVariable",
    "kNetDeclarationAssignment",
    "kUnqualifiedId",
)


def _declaration_parts(declaration: dict) -> tuple[dict, list[dict]] | None:
    """``(kDataType node, declarator nodes)`` of one declaration, or ``None``.

    Only immediate children are consulted at each level, so the
    ``kUnqualifiedId`` a *user-defined type name* hides inside a
    ``kDataType`` (``input state_t d``) is never mistaken for the
    declarator.
    """
    tag = declaration.get("tag")
    if tag == "kDataDeclaration":
        base = _first_direct(declaration, "kInstantiationBase")
        if base is None:
            return None
        inst_type = _first_direct(base, "kInstantiationType")
        data_type = _first_direct(inst_type, "kDataType") if inst_type else None
        var_list = _first_direct(base, "kGateInstanceRegisterVariableList")
        declarators = (
            [c for c in _direct_children(var_list) if c.get("tag") in _DECLARATOR_TAGS]
            if var_list
            else []
        )
    elif tag == "kNetDeclaration":
        data_type = _first_direct(declaration, "kDataType")
        var_list = _first_direct(declaration, "kNetVariableDeclarationAssign")
        declarators = (
            [c for c in _direct_children(var_list) if c.get("tag") in _DECLARATOR_TAGS]
            if var_list
            else []
        )
    elif tag == "kPortDeclaration":
        data_type = _first_direct(declaration, "kDataType")
        ident = _first_direct(declaration, "kUnqualifiedId")
        declarators = [ident] if ident is not None else []
    else:
        return None
    if data_type is None or not declarators:
        return None
    return data_type, declarators


def _has_unpacked_dimensions(declaration: dict, declarator: dict) -> bool:
    """True iff ``declarator`` carries unpacked (array) dimensions.

    A port spells its unpacked dimensions as a *sibling* of the
    identifier rather than inside it, so the whole declaration is
    consulted for that shape — safe, because a ``kPortDeclaration``
    declares exactly one name.
    """
    scope = declaration if declaration.get("tag") == "kPortDeclaration" else declarator
    for dims in _cst.walk_subtrees(scope, "kUnpackedDimensions"):
        if _cst.walk_subtrees(dims, "kDeclarationDimensions"):
            return True
    return False


def declared_type(sv: str, module_node: dict, name: str) -> str | None:
    """Whitespace-normalised declared data type of ``name`` in ``module_node``.

    Returns e.g. ``"logic [7:0]"``, ``"logic signed [3:0]"``,
    ``"logic"`` — and ``None`` whenever the declaration cannot be
    copied verbatim onto a synthesised sibling object:

    - the name is not declared in this module (a port of an enclosing
      scope, a package import, a hierarchical name);
    - the type is a typedef name (``state_t``, an enum / struct /
      union), an integer-atom type (``int``, ``integer``, ``byte``) or
      anything else outside :data:`_COPYABLE_TYPE_RE`;
    - the declarator carries unpacked dimensions (``logic [3:0] arr
      [0:1]``), where copying only the packed half would silently
      change the object's shape.

    This exists because ``logic [$bits(<lhs>)-1:0] <new>;`` — the
    obvious width-preserving spelling — *erases the type*: an enum, a
    packed struct or an unpacked array turns into a plain packed vector
    and the rewired reader then fails elaboration. A stage whose type
    cannot be copied is simply not a site.
    """
    for tag in _DECLARATION_TAGS:
        for declaration in _cst.walk_subtrees(module_node, tag):
            parts = _declaration_parts(declaration)
            if parts is None:
                continue
            data_type, declarators = parts
            match = next(
                (d for d in declarators if first_identifier(d) == name),
                None,
            )
            if match is None:
                continue
            if _has_unpacked_dimensions(declaration, match):
                return None
            try:
                type_start, _ = _cst.node_span(data_type)
            except ValueError:
                return None
            first_leaf = _first_identifier_leaf(declarators[0])
            if first_leaf is None:
                return None
            text = _normalise(
                sv.encode("utf-8")[type_start : int(first_leaf["start"])].decode(
                    "utf-8"
                )
            )
            return text if _COPYABLE_TYPE_RE.match(text) else None
    return None


# --- stage / reader discovery ------------------------------------------------


@dataclass(frozen=True)
class SyncStage:
    """One recognised sync-chain stage, of either shape.

    ``block_start`` / ``block_end`` are the byte span of the whole
    ``always_ff`` construct, ``lhs_name`` the stage's Q name, ``node``
    the ``kAlwaysStatement`` CST subtree the stage was recognised from,
    ``module_node`` the innermost ``kModuleDeclaration`` containing it
    (``None`` when the stage is not inside a module — an ``interface``
    body, say) and ``lhs_type`` the stage LHS's copyable declared type
    (``None`` when :func:`declared_type` declines; see there).

    The sensitivity trio:

    - ``clock_edge_text`` — the **clock** edge alone (e.g.
      ``posedge dst_clk``), empty when it couldn't be derived. Always
      the clock, never the reset, so :func:`find_downstream_reader`'s
      same-clock check compares clocks and a reset-bearing stage can
      feed a reset-free reader and the reverse.
    - ``sensitivity_text`` — the whole ``@(...)`` interior verbatim
      (``posedge dst_clk or negedge rst_n``). For a reset-free stage
      this is exactly ``clock_edge_text``.
    - ``reset_cond_text`` / ``reset_const_text`` — the ``if (...)``
      condition and the reset branch's right-hand side, verbatim; both
      ``None`` for a reset-free stage. ``reset_cond_text is None`` is
      the canonical "is this shape A?" test.
    """

    block_start: int
    block_end: int
    lhs_name: str
    clock_edge_text: str
    node: dict
    module_node: dict | None
    lhs_type: str | None
    sensitivity_text: str = ""
    reset_cond_text: str | None = None
    reset_const_text: str | None = None


@dataclass(frozen=True)
class ReaderRef:
    """A downstream reader's reference to a stage's Q.

    ``start`` / ``end`` are the byte span of the reader's right-hand
    side, which is by construction exactly the stage's bare Q name (see
    :func:`find_downstream_reader`) — the span an insertion operator
    rewrites. ``node`` is the reader's ``kAlwaysStatement``.
    """

    start: int
    end: int
    node: dict


def _enclosing_module(
    modules: list[tuple[tuple[int, int], dict]], span: tuple[int, int]
) -> dict | None:
    """Innermost module whose byte span contains ``span``."""
    best: tuple[int, dict] | None = None
    for (start, end), module in modules:
        if start <= span[0] and span[1] <= end:
            width = end - start
            if best is None or width < best[0]:
                best = (width, module)
    return best[1] if best is not None else None


def find_sync_stages(sv: str) -> tuple[list[SyncStage], dict]:
    """Return ``(stages in source order, parsed CST root)``.

    The CST root comes back with the stages so callers that also need
    to walk the tree don't parse ``sv`` twice.
    """
    path = sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    modules: list[tuple[tuple[int, int], dict]] = []
    for module in _cst.walk_subtrees(cst_root, "kModuleDeclaration"):
        try:
            modules.append((_cst.node_span(module), module))
        except ValueError:
            continue
    stages: list[SyncStage] = []
    seen: set[tuple[int, int]] = set()
    for always_ff in _cst.walk_subtrees(cst_root, "kAlwaysStatement"):
        # Shape A (reset-free) first, then shape B (async reset). The
        # two are mutually exclusive: A forbids a conditional, B needs
        # one.
        single = (
            single_nonblocking(always_ff)
            if is_single_clock_sensitivity(always_ff)
            else None
        )
        reset_shape = reset_bearing_stage(sv, always_ff) if single is None else None
        if single is not None:
            lhs_name, block_start, block_end = single
            clock_edge_text = _clock_edge_text(sv, always_ff)
            sensitivity_text = clock_edge_text
            reset_cond_text: str | None = None
            reset_const_text: str | None = None
        elif reset_shape is not None:
            lhs_name = reset_shape.lhs_name
            block_start = reset_shape.block_start
            block_end = reset_shape.block_end
            clock_edge_text = reset_shape.clock_edge_text
            sensitivity_text = reset_shape.sensitivity_text
            reset_cond_text = reset_shape.reset_cond_text
            reset_const_text = reset_shape.reset_const_text
        else:
            continue
        if (block_start, block_end) in seen:
            continue
        seen.add((block_start, block_end))
        module_node = _enclosing_module(modules, (block_start, block_end))
        stages.append(
            SyncStage(
                block_start=block_start,
                block_end=block_end,
                lhs_name=lhs_name,
                clock_edge_text=clock_edge_text,
                node=always_ff,
                module_node=module_node,
                lhs_type=(
                    declared_type(sv, module_node, lhs_name)
                    if module_node is not None
                    else None
                ),
                sensitivity_text=sensitivity_text,
                reset_cond_text=reset_cond_text,
                reset_const_text=reset_const_text,
            )
        )
    stages.sort(key=lambda s: (s.block_start, s.block_end))
    return stages, cst_root


def find_stage_spans(sv: str) -> list[tuple[int, int, str]]:
    """``(block_start, block_end, lhs_name)`` per stage — the legacy shape.

    Thin wrapper over :func:`find_sync_stages` kept for
    ``SYNC_CHAIN_DEPTH_PERTURB``, which only ever needed the span and
    the Q name. Deleting a stage needs neither the enclosing module nor
    the declared type, so that operator's candidate set is unchanged by
    the extra fields :class:`SyncStage` now carries.

    **Reset-free stages only.** The async-reset shape (shape B, added
    for the two *insertion* operators in xeno#34) is filtered out here
    on purpose: ``SYNC_CHAIN_DEPTH_PERTURB``'s site set — and the
    pinned counts of its tests — predate the shape, and deleting a
    reset-bearing stage leaves a reset-domain hole that is a different
    mutation from the depth perturbation the operator claims. The
    shape could be adopted later; doing so is a deliberate change to
    that operator's candidate set, not a side effect of this filter
    going away.
    """
    stages, _root = find_sync_stages(sv)
    return [
        (s.block_start, s.block_end, s.lhs_name)
        for s in stages
        if s.reset_cond_text is None
    ]


def _rhs_expression(nb_assign: dict) -> dict | None:
    """The ``kExpression`` right-hand side of a non-blocking assignment.

    Verible lays the node out as
    ``[kLPValue, '<=' leaf, kExpression, ';' leaf]``, so the right-hand
    side is the first ``kExpression`` past the ``<=``. Anchoring on the
    ``<=`` is what keeps a reader search off the LHS (a stage's own Q
    assignment must never count as a read of itself).
    """
    children = _direct_children(nb_assign)
    for index, child in enumerate(children):
        if child.get("tag") == "<=":
            for candidate in children[index + 1 :]:
                if candidate.get("tag") == "kExpression":
                    return candidate
            return None
    return None


def _reads_stage_directly(sv: str, always_node: dict, stage: SyncStage) -> dict | None:
    """First ``kExpression`` in ``always_node`` that *is* the stage's Q.

    "Is", not "mentions": the whole right-hand side has to be the bare
    identifier. ``q2 <= q1 & q1`` and ``q2 <= q1 & en`` are comb logic
    on the way, not a direct flop-to-flop edge, so neither is a chain
    edge an insertion operator may interpose on — for
    ``CHAIN_STAGE_INSERT`` the pair was never a clean synchroniser
    chain, and for ``COMB_BETWEEN_STAGES`` the comb cell CDC-014 looks
    for is already there. Requiring the exact span also means a reader
    has exactly one rewrite site, however many times it names the
    stage.
    """
    for nb in _cst.walk_subtrees(always_node, "kNonblockingAssignmentStatement"):
        rhs = _rhs_expression(nb)
        if rhs is None:
            continue
        text = _node_text(sv, rhs)
        if text is not None and text.strip() == stage.lhs_name:
            return rhs
    return None


def _same_clock(sv: str, always_node: dict, stage: SyncStage) -> bool:
    """True iff ``always_node``'s sensitivity list carries the stage's edge.

    Whitespace-normalised text equality against
    ``stage.clock_edge_text``, so a reset-bearing reader
    ``@(posedge dst_clk or negedge rst_n)`` still matches a stage
    clocked on ``posedge dst_clk`` — one of its event expressions is
    that edge. A stage with no recoverable clock text matches nothing.
    """
    if not stage.clock_edge_text:
        return False
    wanted = _normalise(stage.clock_edge_text)
    for ev in _cst.walk_subtrees(always_node, "kEventExpression"):
        text = _node_text(sv, ev)
        if text is not None and _normalise(text) == wanted:
            return True
    return False


def find_downstream_reader(sv: str, stage: SyncStage) -> ReaderRef | None:
    """First ``always_ff`` (source order) that is a direct reader of the stage.

    A reader is another ``kAlwaysStatement`` **in the stage's own
    module** that

    1. is clocked on the same edge — one of its event expressions
       matches ``stage.clock_edge_text`` verbatim modulo whitespace, so
       a different-clock flop is a crossing, not the next link of this
       chain, and a reset-bearing ``@(posedge clk or negedge rst_n)``
       block still qualifies; and
    2. holds a non-blocking assignment whose entire right-hand side is
       the stage's bare Q name, i.e. a direct flop-to-flop edge.

    Returns ``None`` when nothing downstream consumes the stage's Q that
    way — a chain tail feeding a continuous ``assign`` or an output
    port, a different-clock consumer, or a reader that already puts comb
    logic in the path.

    **Scope.** The walk never leaves ``stage.module_node``, so a
    same-named signal in a *different* module is never mistaken for this
    stage's reader (rewiring it would leave the reader referring to a
    declaration it cannot see). Name reuse across nested generate scopes
    *within* one module is out of scope: this walker is flat and would
    match the first textual reader in the module regardless of which
    generate branch declares the name.
    """
    scope = stage.module_node
    if scope is None:
        return None
    spanned: list[tuple[tuple[int, int], dict]] = []
    for always_node in _cst.walk_subtrees(scope, "kAlwaysStatement"):
        try:
            spanned.append((_cst.node_span(always_node), always_node))
        except ValueError:
            continue
    spanned.sort(key=lambda item: item[0])
    for span, always_node in spanned:
        if span == (stage.block_start, stage.block_end):
            continue
        if not _same_clock(sv, always_node, stage):
            continue
        rhs = _reads_stage_directly(sv, always_node, stage)
        if rhs is None:
            continue
        start, end = _cst.node_span(rhs)
        return ReaderRef(start=start, end=end, node=always_node)
    return None
