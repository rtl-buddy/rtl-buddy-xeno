"""Mutator API surface — operator-agnostic data model + iteration shape.

Public surface ratified in #3 (API design) and #4 (parser layering).
This module owns:

- :class:`MutationKind` — the operator enum (11 kinds; #3 split rb-mut's
  ``cond`` into ``COND_NEGATE`` + ``COND_CONST``).
- :class:`Prediction` — flat-shape oracle prediction. CDC fields
  (``cdc_rules_added`` / ``cdc_rules_removed``) and FPV fields
  (``perturbs_signals`` / ``perturbs_liveness``) co-exist on one type
  so cross-oracle operators (e.g. ``PORT_BINDING_SWAP``) can fill in
  both buckets without a sum-type discriminator.
- :class:`Mutant` — single mutation result; the rename from
  ``ExpectedChange`` → ``Prediction`` lands as ``Mutant.prediction``.
- :class:`Site` — what a mutant *would be* without actually mutating.
  Used by the ``Mutator.candidates`` enumeration primitive for budget
  estimation (rtl_buddy#206's ``rb mut list``).
- :class:`Schedule` — cross-kind scheduling strategy on
  ``Mutator.generate`` (round-robin is the rb-mut idiom; sequential
  stays default for cdc#221's existing tests).
- :class:`Mutator` — the iteration shape.

Operator implementations live under :mod:`rtl_buddy_xeno.operators`.
The registry is lazy-imported so the API module has no import-order
dependency on operator modules (which themselves may pull in the
``[verible]`` or ``[slang]`` extras).
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class MutationKind(StrEnum):
    """Mutation operators across both consumer pools.

    First five are cdc#221 Layer B's structural-CDC operators; remainder
    are rtl_buddy#206's semantic operators. See umbrella #2 for the
    full table including parser-layer assignments and MCY-expressibility.
    """

    # CDC — cdc#221 Layer B
    CLOCK_POLARITY_SWAP = "clock_polarity_swap"
    SYNC_CHAIN_DEPTH_PERTURB = "sync_chain_depth_perturb"
    BIT_EXTRACT_PERMUTE = "bit_extract_permute"
    ATTRIBUTE_TOGGLE = "attribute_toggle"
    RESET_POLARITY_FLIP = "reset_polarity_flip"
    # FPV / semantic — rtl_buddy#206
    ARITH_FLIP = "arith_flip"
    BIT_OP_FLIP = "bit_op_flip"
    COND_NEGATE = "cond_negate"
    COND_CONST = "cond_const"
    ASSIGN_DROP = "assign_drop"
    PORT_BINDING_SWAP = "port_binding_swap"


class Schedule(StrEnum):
    """Cross-kind iteration strategy on :meth:`Mutator.generate`.

    ``SEQUENTIAL`` (default): exhaust each kind in supplied order before
    advancing — preserves cdc#221's existing test expectations.
    ``ROUND_ROBIN``: one mutant per kind, repeat. Preferred idiom for
    budgeted rb-mut runs where a single high-yield kind would otherwise
    hog the budget.
    """

    SEQUENTIAL = "sequential"
    ROUND_ROBIN = "round_robin"


@dataclass(frozen=True)
class Prediction:
    """Operator's prediction of how downstream oracles should react.

    Flat-shape: CDC-oracle and FPV-oracle fields co-exist on one
    dataclass so a single operator that perturbs both surfaces (e.g.
    ``PORT_BINDING_SWAP`` plausibly creates a wrong-domain crossing
    *and* perturbs a signal cone) can fill both buckets without an
    arbitrary discriminator. Empty defaults mean "no prediction in
    this dimension" — distinct from a negative prediction.

    The ``rationale`` field is required non-empty: the diagnostic loop
    where disagreement between predicted and observed becomes
    actionable falls apart when a mutant carries no human-readable
    explanation of *why* the prediction was made.
    """

    rationale: str
    # CDC oracle — cdc#221, cdc#222
    cdc_rules_added: frozenset[str] = field(default_factory=frozenset)
    cdc_rules_removed: frozenset[str] = field(default_factory=frozenset)
    # FPV / sim oracle — rtl_buddy#206
    perturbs_signals: frozenset[str] = field(default_factory=frozenset)
    perturbs_liveness: bool = False

    def __post_init__(self) -> None:
        if not self.rationale:
            raise ValueError(
                "Prediction.rationale must be a non-empty string — every "
                "mutant needs a human-readable explanation of its predicted "
                "behaviour so disagreement reports stay self-contained."
            )


@dataclass(frozen=True)
class Mutant:
    """A single mutated SV source plus its provenance."""

    sv: str
    diff_summary: str
    seed: int
    prediction: Prediction
    kind: MutationKind


@dataclass(frozen=True)
class Site:
    """What a mutant *would be* without actually constructing the SV.

    The enumeration primitive consumed by rb-mut's ``rb mut list``:
    iterate candidate sites for budget estimation, then call
    :meth:`Mutator.generate` to actually produce mutants.

    ``snippet`` is the token / span that would be rewritten — kept
    cheap (token span only, not the surrounding line) so enumerating
    thousands of candidates stays light. Consumers re-read the file
    if they want more context.
    """

    kind: MutationKind
    line: int
    column: int
    snippet: str
    prediction: Prediction


@dataclass(frozen=True)
class Mutator:
    """Stateless mutator bound to a parent SV source.

    Construction is via :meth:`Mutator.from_sv`; the resulting object
    exposes :meth:`generate` returning an iterator of :class:`Mutant`,
    and :meth:`candidates` returning an iterator of :class:`Site` for
    budget estimation.
    """

    parent_sv: str
    source: str  # path string or "<inline>" — for diagnostics only

    @classmethod
    def from_sv(cls, path_or_text: Path | str) -> Mutator:
        """Load parent SV from a filesystem path, or accept literal text.

        A :class:`~pathlib.Path` is always treated as a path. A
        :class:`str` is treated as inline SV if it contains a newline
        or any character invalid in a POSIX path component; otherwise
        it's looked up on disk. Inline mode is the test ergonomic; the
        fuzz harness will always pass a :class:`~pathlib.Path`.
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
        schedule: Schedule = Schedule.SEQUENTIAL,
    ) -> Iterator[Mutant]:
        """Yield up to ``count`` mutants drawn from the requested kinds.

        Iteration is deterministic given ``seed``: kinds are processed
        per ``schedule`` (sequential exhausts each kind before the next;
        round-robin yields one per kind in rotation). Within a kind,
        operator-internal randomness is seeded from
        ``(seed, kind.value)``. If an operator is unimplemented (a stub
        from the TODO table), this method raises
        :class:`NotImplementedError` when that kind is reached —
        explicit rather than silent skip.
        """
        if count <= 0:
            return
        from rtl_buddy_xeno.operators import OPERATORS

        def _stream_one(kind: MutationKind) -> Iterator[Mutant]:
            op = OPERATORS[kind]
            rng = random.Random(f"{seed}:{kind.value}")
            yield from op(self.parent_sv, rng)

        if schedule is Schedule.SEQUENTIAL:
            emitted = 0
            for kind in kinds:
                for mutant in _stream_one(kind):
                    if emitted >= count:
                        return
                    yield mutant
                    emitted += 1
            return

        # ROUND_ROBIN: one per kind, repeat.
        streams = [(_stream_one(k), k) for k in kinds]
        emitted = 0
        while streams and emitted < count:
            still_live: list[tuple[Iterator[Mutant], MutationKind]] = []
            for stream, kind in streams:
                if emitted >= count:
                    break
                try:
                    mutant = next(stream)
                except StopIteration:
                    continue
                yield mutant
                emitted += 1
                still_live.append((stream, kind))
            streams = still_live

    def candidates(
        self,
        kinds: Sequence[MutationKind],
    ) -> Iterator[Site]:
        """Enumerate candidate sites without emitting mutants.

        Source-order iteration (no shuffle). ``rb mut list`` consumes
        this directly; ``rb mut run`` continues to call
        :meth:`generate` with a budget. A :class:`Site` is "what a
        mutant would be" — the natural budget-estimation primitive.

        Unimplemented operators raise :class:`NotImplementedError` when
        their kind is reached, just like :meth:`generate`.
        """
        from rtl_buddy_xeno.operators import CANDIDATES

        for kind in kinds:
            walker = CANDIDATES[kind]
            yield from walker(self.parent_sv)
