"""Mutator API and bundled mutation operators.

Design reference: rtl-buddy-cdc#221 (Stage 3, Layer B). Public surface:

    Mutator.from_sv(path).generate(kinds=[...], count=N) -> Iterator[Mutant]

Each `Mutant` carries an `expected_change` declaring the finding-set delta
its parent template's analyzer pass should exhibit (rules that begin firing,
rules that stop). The fuzz consumer treats disagreement between predicted
and observed delta as either a gap candidate or a buggy operator — both
actionable.

Parser-choice rationale: see README "Parser choice". v0 is text-based; the
`[slang]` extra is reserved for the structural operators flagged TODO.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class MutationKind(StrEnum):
    """Mutation operators enumerated in rtl-buddy-cdc#221 Layer B."""

    CLOCK_POLARITY_SWAP = "clock_polarity_swap"
    SYNC_CHAIN_DEPTH_PERTURB = "sync_chain_depth_perturb"
    BIT_EXTRACT_PERMUTE = "bit_extract_permute"
    ATTRIBUTE_TOGGLE = "attribute_toggle"
    RESET_POLARITY_FLIP = "reset_polarity_flip"


@dataclass(frozen=True)
class ExpectedChange:
    """Predicted finding-set delta for a mutant vs. its parent template.

    `rules_added` are rule IDs (e.g. "CDC-006") expected to begin firing.
    `rules_removed` are rule IDs expected to stop firing. `rationale` is a
    short human-readable note explaining the prediction; it lives in the
    mutant so disagreement reports stay self-contained.
    """

    rules_added: frozenset[str] = field(default_factory=frozenset)
    rules_removed: frozenset[str] = field(default_factory=frozenset)
    rationale: str = ""


@dataclass(frozen=True)
class Mutant:
    """A single mutated SV source plus its provenance."""

    sv: str
    diff_summary: str
    seed: int
    expected_change: ExpectedChange
    kind: MutationKind


# A mutation operator: given parent SV and a deterministic RNG, yield mutants.
# Yielding zero mutants is legal (operator inapplicable to this source).
_Operator = "callable taking (str, random.Random) and yielding Mutant"


_POLARITY_TOKEN = re.compile(r"\b(posedge|negedge)\b")


def _clock_polarity_swap(sv: str, rng: random.Random) -> Iterator[Mutant]:
    """Flip a single `posedge` ↔ `negedge` token.

    Exercises CDC-006 (opposite-edge sync): the parent template typically
    has a sync chain on `posedge clk_dst`; flipping the first stage to
    `negedge clk_dst` should make CDC-006 fire on that chain.

    The operator yields one mutant per polarity token found, in source
    order. The seed parameter records the textual position of the swap so
    a consumer can reproduce a specific mutant from `(parent, seed)`.
    """
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
            expected_change=ExpectedChange(
                rules_added=frozenset({"CDC-006"}),
                rationale=(
                    "clock-polarity swap on a sync-chain stage creates an "
                    "opposite-edge crossing; CDC-006 should fire"
                ),
            ),
            kind=MutationKind.CLOCK_POLARITY_SWAP,
        )


def _todo_operator(kind: MutationKind, issue: str) -> "object":
    def _stub(sv: str, rng: random.Random) -> Iterator[Mutant]:
        raise NotImplementedError(
            f"{kind.value}: stubbed, see {issue}. "
            "Will graduate to a pyslang-backed structural rewrite."
        )
        yield  # pragma: no cover  -- make this a generator

    return _stub


_OPERATORS: dict[MutationKind, object] = {
    MutationKind.CLOCK_POLARITY_SWAP: _clock_polarity_swap,
    # TODO(rtl-buddy-cdc#221 Layer B): structural operators below.
    # These require AST-level reasoning (sync-chain identification,
    # bus indexing, attribute placement) and will graduate to a
    # pyslang-backed rewrite — see README "Parser choice".
    MutationKind.SYNC_CHAIN_DEPTH_PERTURB: _todo_operator(
        MutationKind.SYNC_CHAIN_DEPTH_PERTURB,
        "rtl-buddy-cdc#221",
    ),
    MutationKind.BIT_EXTRACT_PERMUTE: _todo_operator(
        MutationKind.BIT_EXTRACT_PERMUTE,
        "rtl-buddy-cdc#221",
    ),
    MutationKind.ATTRIBUTE_TOGGLE: _todo_operator(
        MutationKind.ATTRIBUTE_TOGGLE,
        "rtl-buddy-cdc#221",
    ),
    MutationKind.RESET_POLARITY_FLIP: _todo_operator(
        MutationKind.RESET_POLARITY_FLIP,
        "rtl-buddy-cdc#221",
    ),
}


IMPLEMENTED_KINDS: frozenset[MutationKind] = frozenset(
    {MutationKind.CLOCK_POLARITY_SWAP}
)


@dataclass(frozen=True)
class Mutator:
    """Stateless mutator bound to a parent SV source.

    Construction is via `Mutator.from_sv(path_or_text)`; the resulting
    object exposes `generate(kinds, count, seed)` returning an iterator
    of `Mutant` objects. Order across kinds is deterministic given a seed.
    """

    parent_sv: str
    source: str  # path string or "<inline>" — for diagnostics only

    @classmethod
    def from_sv(cls, path_or_text: Path | str) -> Mutator:
        """Load parent SV from a filesystem path, or accept literal text.

        A `Path` is always treated as a path. A `str` is treated as inline
        SV if it contains a newline or any character that's invalid in a
        POSIX path component; otherwise it's looked up on disk. Inline
        mode is the test ergonomic; the fuzz harness will always pass a
        `Path`.
        """
        if isinstance(path_or_text, Path):
            return cls(parent_sv=path_or_text.read_text(), source=str(path_or_text))
        looks_like_text = "\n" in path_or_text or "\x00" in path_or_text
        if not looks_like_text:
            candidate = Path(path_or_text)
            try:
                exists = candidate.is_file()
            except OSError:
                exists = False
            if exists:
                return cls(parent_sv=candidate.read_text(), source=str(candidate))
        return cls(parent_sv=path_or_text, source="<inline>")

    def generate(
        self,
        kinds: Sequence[MutationKind],
        count: int,
        seed: int = 0,
    ) -> Iterator[Mutant]:
        """Yield up to `count` mutants drawn from the requested kinds.

        Iteration is deterministic given `seed`: kinds are processed in
        the order supplied; within a kind, operator-internal randomness is
        seeded from `(seed, kind)`. Yielded mutants stop at `count` even
        if more are available. If an operator is unimplemented (a stub
        from the TODO table), this method raises `NotImplementedError`
        when that kind is reached — explicit rather than silent skip.
        """
        if count <= 0:
            return
        emitted = 0
        for kind in kinds:
            op = _OPERATORS[kind]
            rng = random.Random(f"{seed}:{kind.value}")
            for mutant in op(self.parent_sv, rng):  # type: ignore[operator]
                if emitted >= count:
                    return
                yield mutant
                emitted += 1
