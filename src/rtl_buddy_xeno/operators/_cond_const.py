"""``COND_CONST`` — replace a conditional expression with ``1`` or ``0``.

Verible-only operator. Same site discovery as :mod:`._cond_negate`,
but each site yields **two** mutants: one with the condition replaced
by the literal ``1`` (always-true) and one with ``0`` (always-false).
The pair exercises both branches of an ``if`` / ``? :`` independently,
which a single negation can't.

The literals are unsized (``1`` / ``0``) rather than ``1'b1`` /
``1'b0`` because the SV expression grammar accepts both in any
condition position; the unsized form is shorter and matches what
human authors typically write in conditions.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site
from rtl_buddy_xeno.operators._cond_helpers import (
    byte_to_line_col,
    find_condition_sites,
)


def _predict(kind: str, line: int, value: str) -> Prediction:
    label = "if-condition" if kind == "if" else "ternary condition"
    if value == "1":
        outcome = "always-taken branch"
    else:
        outcome = "always-skipped branch"
    return Prediction(
        rationale=(
            f"forced {label} at line {line} to constant `{value}` "
            f"({outcome}); any property whose guard depends on the "
            "original condition should detect the change"
        ),
        perturbs_liveness=False,
    )


def _splice_const(sv: str, start: int, end: int, value: str) -> str:
    data = sv.encode("utf-8")
    return (data[:start] + value.encode("utf-8") + data[end:]).decode("utf-8")


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = find_condition_sites(sv)
    if not sites:
        return
    # Expand each site into (site_idx, value); shuffle the joint order so
    # the consumer sees both flavours interleaved rather than all-1s
    # then all-0s.
    pairs = [(i, v) for i in range(len(sites)) for v in ("1", "0")]
    rng.shuffle(pairs)
    for idx, value in pairs:
        start, end, kind = sites[idx]
        line, _ = byte_to_line_col(sv, start)
        original_text = sv.encode("utf-8")[start:end].decode("utf-8", errors="replace")
        yield Mutant(
            sv=_splice_const(sv, start, end, value),
            diff_summary=(
                f"line {line}: force {kind} cond `{original_text}` -> `{value}`"
            ),
            seed=start * 10 + int(value),
            prediction=_predict(kind, line, value),
            kind=MutationKind.COND_CONST,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for start, end, kind in find_condition_sites(sv):
        line, column = byte_to_line_col(sv, start)
        snippet = sv.encode("utf-8")[start:end].decode("utf-8", errors="replace")
        # Single candidate per site; the operator emits two mutants per site
        # (constant=1 and constant=0). The Site abstraction is about
        # *locations*, not the operator's branching factor.
        yield Site(
            kind=MutationKind.COND_CONST,
            line=line,
            column=column,
            snippet=snippet,
            prediction=_predict(kind, line, "1"),
        )


operator = _mutants
candidates = _candidates
