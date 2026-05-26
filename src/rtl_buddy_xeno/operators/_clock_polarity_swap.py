"""``CLOCK_POLARITY_SWAP`` — token rewrite, parser-free.

Flip a single ``posedge`` ↔ ``negedge`` token. Exercises CDC-006
(opposite-edge sync): the parent template typically has a sync chain
on ``posedge clk_dst``; flipping the first stage to ``negedge clk_dst``
should make CDC-006 fire on that chain.

This operator ships in the no-extras install (no ``[verible]`` or
``[slang]`` required). The token swap is a regex rewrite because
``posedge`` / ``negedge`` aren't ambiguous in legal SV positions — a
CST walk would be over-engineering for the simplest operator we ship.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site

_POLARITY_TOKEN = re.compile(r"\b(posedge|negedge)\b")


def _predict(line: int, original: str, replacement: str) -> Prediction:
    return Prediction(
        rationale=(
            f"clock-polarity swap on a sync-chain stage at line {line} "
            f"({original} → {replacement}) creates an opposite-edge "
            "crossing; CDC-006 should fire"
        ),
        cdc_rules_added=frozenset({"CDC-006"}),
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    positions = [m.start() for m in _POLARITY_TOKEN.finditer(sv)]
    if not positions:
        return
    order = list(range(len(positions)))
    rng.shuffle(order)
    for idx in order:
        pos = positions[idx]
        match = _POLARITY_TOKEN.match(sv, pos)
        assert match is not None
        original = match.group(1)
        replacement = "negedge" if original == "posedge" else "posedge"
        mutated = sv[: match.start()] + replacement + sv[match.end() :]
        line = sv.count("\n", 0, pos) + 1
        yield Mutant(
            sv=mutated,
            diff_summary=f"line {line}: {original} -> {replacement}",
            seed=pos,
            prediction=_predict(line, original, replacement),
            kind=MutationKind.CLOCK_POLARITY_SWAP,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for match in _POLARITY_TOKEN.finditer(sv):
        pos = match.start()
        original = match.group(1)
        replacement = "negedge" if original == "posedge" else "posedge"
        line = sv.count("\n", 0, pos) + 1
        # column: byte offset within the line, 1-indexed
        last_newline = sv.rfind("\n", 0, pos)
        column = pos - last_newline if last_newline >= 0 else pos + 1
        yield Site(
            kind=MutationKind.CLOCK_POLARITY_SWAP,
            line=line,
            column=column,
            snippet=original,
            prediction=_predict(line, original, replacement),
        )


operator = _mutants
candidates = _candidates
