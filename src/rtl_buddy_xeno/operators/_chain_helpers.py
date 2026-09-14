"""Shared sync-chain primitives for the chain-shaped CDC operators.

Same spirit as :mod:`._cond_helpers`: the operators that reason about
a *sync chain* all need the same site-discovery surface, so the walker
lives here once and every consumer sees an identical candidate set.

Consumers:

- :mod:`._sync_chain_depth_perturb` — deletes a stage (chain depth -1).
- :mod:`._chain_stage_insert` — synthesises a stage (chain depth +1).

Everything here sits in the **Verible CST** parser layer (see the
no-straddle rule in :mod:`rtl_buddy_xeno.operators`): site discovery
via :mod:`rtl_buddy_xeno.cst`, emission via byte-splices on the
original source string. No pyslang.

The "looks like a sync-chain stage" heuristic (unchanged from the
shape ``SYNC_CHAIN_DEPTH_PERTURB`` shipped with):

1. The ``always_ff`` block's sensitivity list has exactly one edge
   token (``posedge`` / ``negedge``) on a single signal — the clock.
   Sync chains never carry an async reset on the stage itself.
2. The block contains exactly one statement: a non-blocking assignment
   ``LHS <= RHS;`` (no ``if``, ``case``, loops, blocking assignments).
3. The LHS is a bare identifier (not a bit-select, not a hierarchical
   reference).
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rtl_buddy_xeno import cst as _cst

__all__ = [
    "ReaderRef",
    "SyncStage",
    "byte_to_line_col",
    "find_downstream_reader",
    "find_stage_spans",
    "find_sync_stages",
    "first_identifier",
    "fresh_identifier",
    "insert_after_block",
    "is_single_clock_sensitivity",
    "replace_span",
    "single_nonblocking",
    "sv_to_tempfile",
]


# --- source plumbing ---------------------------------------------------------


def sv_to_tempfile(sv: str) -> Path:
    """Materialise ``sv`` under a content-hashed temp path.

    The CST facade parses files, not strings, and view's cache keys on
    content — so the same source text always lands on the same path and
    re-parses are cache hits.
    """
    digest = hashlib.sha256(sv.encode("utf-8")).hexdigest()[:16]
    tmpdir = Path(tempfile.gettempdir()) / "rtl-buddy-xeno"
    tmpdir.mkdir(parents=True, exist_ok=True)
    target = tmpdir / f"sv-{digest}.sv"
    if not target.exists() or target.read_text() != sv:
        target.write_text(sv)
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


def first_identifier(node: Any) -> str | None:
    """Return the text of the first ``SymbolIdentifier`` leaf under ``node``."""
    if isinstance(node, dict):
        if node.get("tag") == "SymbolIdentifier" and node.get("text"):
            return str(node["text"])
        for child in node.get("children", []) or []:
            name = first_identifier(child)
            if name:
                return name
    elif isinstance(node, list):
        for child in node:
            name = first_identifier(child)
            if name:
                return name
    return None


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


def _clock_edge_text(sv: str, always_ff_node: dict) -> str:
    """Literal sensitivity text of the stage's clock, e.g. ``posedge clk``.

    Copied verbatim out of the source by the single ``kEventExpression``'s
    byte span, so whatever the author wrote (spacing, clock expression)
    survives into a synthesised sibling flop. Returns ``""`` when the
    event expression can't be pinned down unambiguously — callers that
    need to *emit* a clock (as opposed to merely deleting a block) must
    treat an empty string as "skip this stage".
    """
    evs = _cst.walk_subtrees(always_ff_node, "kEventExpression")
    edged = [ev for ev in evs if _leads_with_edge(ev)]
    chosen = edged[0] if len(edged) == 1 else (evs[0] if len(evs) == 1 else None)
    if chosen is None:
        return ""
    try:
        start, end = _cst.node_span(chosen)
    except ValueError:
        return ""
    return sv.encode("utf-8")[start:end].decode("utf-8").strip()


# --- stage / reader discovery ------------------------------------------------


@dataclass(frozen=True)
class SyncStage:
    """One recognised sync-chain stage.

    ``block_start`` / ``block_end`` are the byte span of the whole
    ``always_ff @(...) LHS <= RHS;`` construct, ``lhs_name`` the stage's
    Q name, ``clock_edge_text`` the literal sensitivity text (e.g.
    ``posedge dst_clk``, empty when it couldn't be derived) and ``node``
    the ``kAlwaysStatement`` CST subtree the stage was recognised from.
    """

    block_start: int
    block_end: int
    lhs_name: str
    clock_edge_text: str
    node: dict


@dataclass(frozen=True)
class ReaderRef:
    """A downstream reader's reference to a stage's Q.

    ``start`` / ``end`` are the byte span of the bare
    ``SymbolIdentifier`` leaf on the reader's RHS (what an insertion
    operator rewrites); ``node`` is the reader's ``kAlwaysStatement``.
    """

    start: int
    end: int
    node: dict


def find_sync_stages(sv: str) -> tuple[list[SyncStage], dict]:
    """Return ``(stages in source order, parsed CST root)``.

    The CST root comes back with the stages so callers that also need
    to walk the tree — e.g. to follow the chain forward with
    :func:`find_downstream_reader` — don't parse ``sv`` twice.
    """
    path = sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    stages: list[SyncStage] = []
    seen: set[tuple[int, int]] = set()
    for always_ff in _cst.walk_subtrees(cst_root, "kAlwaysStatement"):
        if not is_single_clock_sensitivity(always_ff):
            continue
        single = single_nonblocking(always_ff)
        if single is None:
            continue
        lhs_name, block_start, block_end = single
        if (block_start, block_end) in seen:
            continue
        seen.add((block_start, block_end))
        stages.append(
            SyncStage(
                block_start=block_start,
                block_end=block_end,
                lhs_name=lhs_name,
                clock_edge_text=_clock_edge_text(sv, always_ff),
                node=always_ff,
            )
        )
    stages.sort(key=lambda s: (s.block_start, s.block_end))
    return stages, cst_root


def find_stage_spans(sv: str) -> list[tuple[int, int, str]]:
    """``(block_start, block_end, lhs_name)`` per stage — the legacy shape.

    Thin wrapper over :func:`find_sync_stages` kept for
    ``SYNC_CHAIN_DEPTH_PERTURB``, which only ever needed the span and
    the Q name.
    """
    stages, _root = find_sync_stages(sv)
    return [(s.block_start, s.block_end, s.lhs_name) for s in stages]


def _rhs_children(nb_assign: dict) -> list[dict]:
    """Children of a ``kNonblockingAssignmentStatement`` after the ``<=``.

    Verible lays the node out as
    ``[kLPValue, '<=' leaf, kExpression, ';' leaf]``, so everything past
    the ``<=`` is the right-hand side. Restricting to those children is
    what keeps a reader search off the LHS (a stage's own Q assignment
    must never count as a read of itself).
    """
    children = [c for c in (nb_assign.get("children", []) or []) if isinstance(c, dict)]
    for index, child in enumerate(children):
        if child.get("tag") == "<=":
            return children[index + 1 :]
    return []


def _bare_identifier_leaves(node: Any, out: list[dict]) -> None:
    """Collect ``SymbolIdentifier`` leaves that are *not* part of a dotted ref.

    Verible spells ``sub.x`` as a ``kReference`` holding a ``kLocalRoot``
    (``sub``) plus a ``kHierarchyExtension`` (``.x``). Neither half is a
    plain local signal name, so the whole reference is skipped: rewiring
    a hierarchical reference is out of scope for a chain operator.
    """
    if isinstance(node, dict):
        tag = node.get("tag")
        if tag == "kHierarchyExtension":
            return
        if tag == "kReference" and any(
            isinstance(c, dict) and c.get("tag") == "kHierarchyExtension"
            for c in (node.get("children", []) or [])
        ):
            return
        if tag == "SymbolIdentifier" and "start" in node:
            out.append(node)
            return
        for child in node.get("children", []) or []:
            _bare_identifier_leaves(child, out)
    elif isinstance(node, list):
        for child in node:
            _bare_identifier_leaves(child, out)


def find_downstream_reader(cst_root: dict, stage: SyncStage) -> ReaderRef | None:
    """First ``always_ff`` (source order) that reads ``stage.lhs_name``.

    "Reads" means: a ``kNonblockingAssignmentStatement`` inside some
    *other* ``kAlwaysStatement`` whose right-hand side carries a bare
    ``SymbolIdentifier`` leaf whose text equals the stage's Q name.
    The reader block itself may have any shape (reset branches, case
    statements, multiple edges) — only the *stage* has to be a
    single-statement single-clock flop.

    Returns ``None`` when nothing downstream consumes the stage's Q via
    a flop — e.g. a chain tail feeding a continuous ``assign`` or an
    output port.
    """
    spanned: list[tuple[tuple[int, int], dict]] = []
    for always_node in _cst.walk_subtrees(cst_root, "kAlwaysStatement"):
        try:
            spanned.append((_cst.node_span(always_node), always_node))
        except ValueError:
            continue
    spanned.sort(key=lambda item: item[0])
    for span, always_node in spanned:
        if span == (stage.block_start, stage.block_end):
            continue
        matches: list[dict] = []
        for nb in _cst.walk_subtrees(always_node, "kNonblockingAssignmentStatement"):
            leaves: list[dict] = []
            for rhs_child in _rhs_children(nb):
                _bare_identifier_leaves(rhs_child, leaves)
            matches.extend(
                leaf for leaf in leaves if str(leaf.get("text", "")) == stage.lhs_name
            )
        if not matches:
            continue
        leaf = min(matches, key=lambda n: int(n["start"]))
        return ReaderRef(
            start=int(leaf["start"]), end=int(leaf["end"]), node=always_node
        )
    return None
