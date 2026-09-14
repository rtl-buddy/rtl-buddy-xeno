"""``SYNC_CHAIN_DEPTH_PERTURB`` — drop a sync-chain stage.

Verible-only operator. Identifies always_ff blocks that look like a
sync-chain stage (a single non-blocking assignment ``LHS <= RHS;``
where the LHS is an internal register, not a module port) and emits
a mutant per such block by deleting the entire ``always_ff``.
Reducing the chain depth by one is the mutation cdc#221's CDC-002
guards against ("synchronizer must have ≥2 stages") — the cdc fuzz
oracle should observe CDC-002 fire on the mutated source.

Heuristics for "looks like a sync-chain stage" live in
:mod:`._chain_helpers` (shared with ``CHAIN_STAGE_INSERT`` so both
directions of the chain mutation see an identical candidate set):

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

Insertion mode — synthesising a fresh register, its ``always_ff`` and
the downstream rewiring, so the chain gets *deeper* rather than
shallower — shipped separately as ``CHAIN_STAGE_INSERT``
(:mod:`._chain_stage_insert`, xeno#13). The two operators share this
module's stage recogniser via :mod:`._chain_helpers`.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site
from rtl_buddy_xeno.operators import _chain_helpers as _chain


def _find_sync_stages(sv: str) -> list[tuple[int, int, str]]:
    """Return ``(block_start, block_end, lhs_name)`` per candidate stage."""
    return _chain.find_stage_spans(sv)


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
    """Conservative prediction.

    The operator drops one stage from what *looks like* a sync chain
    (single-clock always_ff, single LHS<=RHS body). Whether that
    drop actually causes CDC-002 or CDC-018 to fire downstream
    depends on the chain's pre-drop depth (CDC-002 fires when depth
    drops below ``required_depth``, typically 2; CDC-018 fires on
    cascaded sync stages of length ≥4). The Verible CST recogniser
    can't see the surrounding chain shape from one always_ff in
    isolation — so the operator drops the stage but doesn't make a
    positive CDC-rule claim. The rationale records intent; the
    downstream coverage report tracks which rules actually fire
    on the mutated source.

    Prior to the rtl-buddy-cdc#221 fuzz integration the prediction
    declared ``cdc_rules_added = {CDC-002, CDC-018}`` unconditionally,
    which over-claimed on the majority of corpus parents whose chain
    was already shorter than the cascaded-threshold and didn't sit
    at the required-depth boundary. See rtl-buddy-xeno PR commit
    history for the bug-fix transition.
    """
    return Prediction(
        rationale=(
            f"dropped sync-chain stage driving `{lhs_name}` at line {line}; "
            "the downstream chain depth is reduced by one. CDC-002 fires "
            "when the chain head was at the minimum required depth; "
            "CDC-018 fires on cascaded chains. The Verible CST recogniser "
            "sees one always_ff at a time and can't verify either "
            "precondition from a single stage, so this prediction makes "
            "no positive CDC-rule claim (cdc_rules_added stays empty); "
            "the downstream coverage report observes the actual rule fires"
        ),
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
        line, _ = _chain.byte_to_line_col(sv, block_start)
        yield Mutant(
            sv=_splice_drop_block(sv, block_start, block_end),
            diff_summary=f"line {line}: drop sync stage driving `{lhs_name}`",
            seed=block_start,
            prediction=_predict(lhs_name, line),
            kind=MutationKind.SYNC_CHAIN_DEPTH_PERTURB,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for block_start, _end, lhs_name in _find_sync_stages(sv):
        line, column = _chain.byte_to_line_col(sv, block_start)
        yield Site(
            kind=MutationKind.SYNC_CHAIN_DEPTH_PERTURB,
            line=line,
            column=column,
            snippet=f"sync stage `{lhs_name}`",
            prediction=_predict(lhs_name, line),
        )


operator = _mutants
candidates = _candidates
