"""``COND_NEGATE`` — wrap a conditional expression in ``!(...)``.

Verible-only operator. Walks ``kIfHeader`` and ``kConditionExpression``
sites (shared with :mod:`._cond_const`) and rewrites each condition
expression ``C`` to ``!(C)``. The wrapping parens are essential —
without them, a condition like ``a & b`` would become ``!a & b``,
which SV parses as ``(!a) & b`` due to precedence.

Mutation predictions: ``perturbs_signals`` is left empty (the condition
expression's signal cone is the consumer's responsibility to compute
via cone-of-influence); ``perturbs_liveness`` is ``False`` (the branch
that runs flips, but the design still drives signals on every clock).
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site
from rtl_buddy_xeno.operators._cond_helpers import (
    byte_to_line_col,
    find_condition_sites,
)


def _predict(kind: str, line: int) -> Prediction:
    label = "if-condition" if kind == "if" else "ternary condition"
    return Prediction(
        rationale=(
            f"negated {label} at line {line} by wrapping in `!(...)`; "
            "the alternate branch now runs, so any property comparing "
            "the design's output against the original control flow "
            "should detect the change"
        ),
        perturbs_liveness=False,
    )


def _splice_negate(sv: str, start: int, end: int) -> str:
    data = sv.encode("utf-8")
    original_bytes = data[start:end]
    replacement = b"!(" + original_bytes + b")"
    return (data[:start] + replacement + data[end:]).decode("utf-8")


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = find_condition_sites(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        start, end, kind = sites[idx]
        line, _ = byte_to_line_col(sv, start)
        original_text = sv.encode("utf-8")[start:end].decode("utf-8", errors="replace")
        yield Mutant(
            sv=_splice_negate(sv, start, end),
            diff_summary=f"line {line}: negate {kind} cond `{original_text}` -> `!({original_text})`",
            seed=start,
            prediction=_predict(kind, line),
            kind=MutationKind.COND_NEGATE,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for start, end, kind in find_condition_sites(sv):
        line, column = byte_to_line_col(sv, start)
        snippet = sv.encode("utf-8")[start:end].decode("utf-8", errors="replace")
        yield Site(
            kind=MutationKind.COND_NEGATE,
            line=line,
            column=column,
            snippet=snippet,
            prediction=_predict(kind, line),
        )


operator = _mutants
candidates = _candidates
