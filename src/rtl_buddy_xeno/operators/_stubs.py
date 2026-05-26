"""TODO-stub factories for unimplemented operators.

Each stub raises :class:`NotImplementedError` pointing at the relevant
design issue rather than silently skipping. Stubs are wired into the
``MutationKind`` enum so consumers compile against the final surface
even while the operator pool grows additively.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator

from rtl_buddy_xeno.mutator import Mutant, MutationKind, Site

_MutantStream = Callable[[str, random.Random], Iterator[Mutant]]
_SiteStream = Callable[[str], Iterator[Site]]


def make_mutant_stub(kind: MutationKind, issue: str) -> _MutantStream:
    """Return a generator-shaped callable that raises on first yield."""

    def _stub(sv: str, rng: random.Random) -> Iterator[Mutant]:
        raise NotImplementedError(
            f"{kind.value}: stubbed, see {issue}. "
            "Will graduate to a Verible/pyslang-backed rewrite "
            "(see umbrella #2's operator-pool table)."
        )
        yield  # pragma: no cover  -- make this a generator

    return _stub


def make_candidates_stub(kind: MutationKind, issue: str) -> _SiteStream:
    """Return a ``candidates``-shaped callable that raises on first yield."""

    def _stub(sv: str) -> Iterator[Site]:
        raise NotImplementedError(
            f"{kind.value}: stubbed, see {issue}. "
            "Will graduate to a Verible/pyslang-backed rewrite "
            "(see umbrella #2's operator-pool table)."
        )
        yield  # pragma: no cover  -- make this a generator

    return _stub
