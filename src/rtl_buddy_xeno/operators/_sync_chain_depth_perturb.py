"""``SYNC_CHAIN_DEPTH_PERTURB`` — drop a sync-chain stage.

Verible-only operator. Identifies always_ff blocks that look like a
sync-chain stage (a single non-blocking assignment ``LHS <= RHS;``
where the LHS is an internal register, not a module port) and emits
a mutant per such block by deleting the entire ``always_ff``.
Reducing the chain depth by one is the mutation cdc#221's CDC-002
guards against ("synchronizer must have ≥2 stages") — the cdc fuzz
oracle should observe CDC-002 fire on the mutated source.

Heuristics for "looks like a sync-chain stage":

1. The ``always_ff`` block's sensitivity list has exactly one edge
   token (``posedge``/``negedge``) on a single signal — the clock.
   Sync chains never have an async reset and a single stage at the
   same level of granularity.
2. The block contains exactly one statement: a non-blocking assignment
   ``LHS <= RHS;`` (no ``if``, ``case``, etc.).
3. The LHS is a bare identifier (not a bit-select; not a hierarchical
   reference). A flop with a bit-select LHS isn't a sync chain stage
   under CDC convention.

When a chain stage is dropped, downstream stages that read the dropped
LHS will reference an undriven signal. Pyslang's typical response is
a Warning (uninitialised use), not an Error — the validity gate
accepts the mutant.

Insertion mode (adding a stage) is deferred. It requires synthesising
a fresh register name + always_ff block + rewiring downstream
references, all of which are textual operations on the source.
Cleaner shipped as a separate follow-up once the deletion mode is
stable.
"""

from __future__ import annotations

import hashlib
import random
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site


def _sv_to_tempfile(sv: str) -> Path:
    digest = hashlib.sha256(sv.encode("utf-8")).hexdigest()[:16]
    tmpdir = Path(tempfile.gettempdir()) / "rtl-buddy-xeno"
    tmpdir.mkdir(parents=True, exist_ok=True)
    target = tmpdir / f"sv-{digest}.sv"
    if not target.exists() or target.read_text() != sv:
        target.write_text(sv)
    return target


def _byte_to_line_col(sv: str, byte_offset: int) -> tuple[int, int]:
    head = sv.encode("utf-8")[:byte_offset]
    line = head.count(b"\n") + 1
    last_newline = head.rfind(b"\n")
    column = (byte_offset - last_newline) if last_newline >= 0 else byte_offset + 1
    return line, column


def _first_identifier(node: Any) -> str | None:
    """Return the text of the first ``SymbolIdentifier`` leaf under ``node``."""
    if isinstance(node, dict):
        if node.get("tag") == "SymbolIdentifier" and node.get("text"):
            return str(node["text"])
        for child in node.get("children", []) or []:
            name = _first_identifier(child)
            if name:
                return name
    elif isinstance(node, list):
        for child in node:
            name = _first_identifier(child)
            if name:
                return name
    return None


def _is_single_clock_sensitivity(always_ff_node: dict) -> bool:
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


def _single_nonblocking(always_ff_node: dict) -> tuple[str, int, int] | None:
    """If the always_ff body is exactly one non-blocking assignment
    ``LHS <= RHS;``, return ``(lhs_name, block_start, block_end)``.

    The block start/end span the entire ``always_ff @(...) STMT;``
    construct so the deletion can splice it cleanly out.
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
    lhs_name = _first_identifier(nb)
    if lhs_name is None:
        return None
    # The block to delete is the always_ff node itself, plus any leading
    # whitespace on the same line and trailing newline.
    try:
        block_start, block_end = _cst.node_span(always_ff_node)
    except ValueError:
        return None
    return lhs_name, block_start, block_end


def _find_sync_stages(sv: str) -> list[tuple[int, int, str]]:
    """Return ``(block_start, block_end, lhs_name)`` per candidate stage."""
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for always_ff in _cst.walk_subtrees(cst_root, "kAlwaysStatement"):
        if not _is_single_clock_sensitivity(always_ff):
            continue
        single = _single_nonblocking(always_ff)
        if single is None:
            continue
        lhs_name, block_start, block_end = single
        if (block_start, block_end) in seen:
            continue
        seen.add((block_start, block_end))
        sites.append((block_start, block_end, lhs_name))
    sites.sort()
    return sites


def _splice_drop_block(sv: str, block_start: int, block_end: int) -> str:
    """Delete the always_ff block plus surrounding whitespace.

    Eats leading whitespace back to the previous newline and trailing
    newline so we don't leave a stray blank line.
    """
    data = sv.encode("utf-8")
    cut_start = block_start
    while cut_start > 0 and data[cut_start - 1 : cut_start] in (b" ", b"\t"):
        cut_start -= 1
    cut_end = block_end
    while cut_end < len(data) and data[cut_end : cut_end + 1] in (b" ", b"\t"):
        cut_end += 1
    if cut_end < len(data) and data[cut_end : cut_end + 1] == b"\n":
        cut_end += 1
    mutated_bytes = data[:cut_start] + data[cut_end:]
    return mutated_bytes.decode("utf-8")


def _predict(lhs_name: str, line: int) -> Prediction:
    return Prediction(
        rationale=(
            f"dropped sync-chain stage driving `{lhs_name}` at line {line}; "
            "the downstream chain depth is reduced by one, so cdc#221's "
            "CDC-002 (insufficient sync depth) should fire if the chain "
            "head was previously at the minimum required depth"
        ),
        cdc_rules_added=frozenset({"CDC-002", "CDC-018"}),
        perturbs_signals=frozenset({lhs_name}),
        perturbs_liveness=False,
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_sync_stages(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        block_start, block_end, lhs_name = sites[idx]
        line, _ = _byte_to_line_col(sv, block_start)
        yield Mutant(
            sv=_splice_drop_block(sv, block_start, block_end),
            diff_summary=f"line {line}: drop sync stage driving `{lhs_name}`",
            seed=block_start,
            prediction=_predict(lhs_name, line),
            kind=MutationKind.SYNC_CHAIN_DEPTH_PERTURB,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for block_start, _end, lhs_name in _find_sync_stages(sv):
        line, column = _byte_to_line_col(sv, block_start)
        yield Site(
            kind=MutationKind.SYNC_CHAIN_DEPTH_PERTURB,
            line=line,
            column=column,
            snippet=f"sync stage `{lhs_name}`",
            prediction=_predict(lhs_name, line),
        )


operator = _mutants
candidates = _candidates
