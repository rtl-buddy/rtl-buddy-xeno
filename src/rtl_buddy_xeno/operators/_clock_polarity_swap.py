"""``CLOCK_POLARITY_SWAP`` — token rewrite, parser-free.

Flip a single ``posedge`` ↔ ``negedge`` token sitting on a clock
signal. Exercises CDC-016 (opposite-edge sync) in
``rtl-buddy/rtl-buddy-cdc``: the parent template typically has a
sync chain on ``posedge clk_dst``; flipping one chain stage to
``negedge clk_dst`` produces an adjacent-stage polarity mismatch
the CDC-016 detector flags.

Reset-edge tokens are skipped — flipping ``negedge rst_n`` →
``posedge rst_n`` without rewriting the matching ``if (!rst_n)``
body produces SV Yosys rejects with ``ERROR: Async reset …
yields non-constant value``. The skip is a name heuristic: any
edge whose immediately-following identifier matches the common
reset-name patterns (``rst``, ``reset``, etc., with optional
``_n`` / ``n`` suffix) is treated as a reset edge and left alone.
Conservative — a few real clocks called ``rst`` would be skipped,
but the alternative (Yosys-rejected mutants polluting the corpus)
is the worse failure mode.

This operator ships in the no-extras install (no ``[verible]`` or
``[slang]`` required). The token swap is a regex rewrite because
``posedge`` / ``negedge`` aren't ambiguous in legal SV positions — a
CST walk would be over-engineering for the simplest operator we ship.

History: prior to rtl-buddy-cdc#221's downstream fuzz integration
the operator predicted CDC-006 (glitchy comb source). That mapping
was wrong — CDC-006 is the rule for *comb-driven* sync sources;
the rule for the opposite-edge hazard a polarity flip creates is
CDC-016 (rtl-buddy-cdc#224 row 1). Both the prediction correction
and the reset-edge skip landed together in this revision.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site

_POLARITY_TOKEN = re.compile(r"\b(posedge|negedge)\b")

# Identifier immediately following a polarity token. ``\s+`` requires
# at least one whitespace character — guards against partial matches
# in malformed SV that wouldn't compile.
_FOLLOWING_IDENT = re.compile(r"\s+([A-Za-z_][A-Za-z0-9_]*)")

# Active-low / active-high reset name patterns the heuristic matches
# against the trailing identifier (case-insensitive). The patterns
# cover the canonical idioms used in rtl-buddy-cdc's fuzz corpus
# templates and the wider conventions documented in rtl-buddy-cdc's
# reset-domain helper. The list intentionally stops short of
# vendor-specific names (every chip family has its own ``por_n`` /
# ``hreset`` etc.) — the operator accepts skipping a few real
# clocks named that way rather than enumerating the whole namespace.
_RESET_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(arst|reset|rst)(_?n)?$", re.IGNORECASE),
    re.compile(r"^(raw_rst|raw_reset|global_rst|local_rst)(_?n)?$", re.IGNORECASE),
    re.compile(r"^(porst|presetn|hreset|nreset)$", re.IGNORECASE),
)


def _is_reset_edge(sv: str, end_of_token: int) -> bool:
    """``True`` when the identifier following a polarity token at
    ``end_of_token`` looks like a reset signal name.

    The matched-token end-offset is the byte just after the ``e``
    in ``posedge`` / ``negedge``. The heuristic peeks at the very
    next identifier — for typical SV
    (``always_ff @(... or negedge rst_n)``) that's the reset signal
    directly.
    """
    tail = _FOLLOWING_IDENT.match(sv, end_of_token)
    if tail is None:
        return False
    name = tail.group(1)
    return any(p.match(name) for p in _RESET_NAME_PATTERNS)


def _predict(line: int, original: str, replacement: str) -> Prediction:
    return Prediction(
        rationale=(
            f"clock-polarity swap on a sync-chain stage at line {line} "
            f"({original} → {replacement}) creates an adjacent-stage "
            "polarity mismatch on the destination clock; CDC-016 should "
            "fire when the swapped token is a sync-chain stage. The "
            "operator is structurally context-blind (parser-free), so "
            "swaps on a single standalone flop produce a domain change "
            "rather than a chain polarity mismatch — the downstream "
            "oracle should treat this prediction as directional"
        ),
        cdc_rules_added=frozenset({"CDC-016"}),
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    positions = [
        m.start()
        for m in _POLARITY_TOKEN.finditer(sv)
        if not _is_reset_edge(sv, m.end())
    ]
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
        if _is_reset_edge(sv, match.end()):
            continue
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
