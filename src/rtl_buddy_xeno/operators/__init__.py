"""Operator registry — maps :class:`MutationKind` → callable.

Two registries are exposed: :data:`OPERATORS` for the mutation
generator (``(sv, rng) -> Iterator[Mutant]``) and :data:`CANDIDATES`
for the enumeration walker (``sv -> Iterator[Site]``). Both registries
are populated in lockstep — adding a new operator always lands both
callables so :meth:`Mutator.candidates` never sees an unimplemented
walker for an implemented mutator (or vice-versa).

The no-straddle rule from #4 is enforceable by reading this directory:
each operator's module declares which parser layer it sits in via the
imports it carries. Regex-only operators import only ``re`` (or the
careful string-scanner pattern); Verible-CST operators import
``rtl_buddy_xeno.cst``; Verible+slang operators import both
``rtl_buddy_xeno.cst`` and ``rtl_buddy_xeno.slang``. A single
operator never mixes paths within itself.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator

from rtl_buddy_xeno.mutator import Mutant, MutationKind, Site
from rtl_buddy_xeno.operators import (
    _arith_flip,
    _assign_drop,
    _attribute_toggle,
    _bit_op_flip,
    _clock_polarity_swap,
    _cond_const,
    _cond_negate,
    _stubs,
)

OperatorFn = Callable[[str, random.Random], Iterator[Mutant]]
CandidatesFn = Callable[[str], Iterator[Site]]

_ISSUE_CDC = "rtl-buddy-cdc#221"
_ISSUE_RBMUT = "rtl_buddy#206"


OPERATORS: dict[MutationKind, OperatorFn] = {
    # CDC operators
    MutationKind.CLOCK_POLARITY_SWAP: _clock_polarity_swap.operator,
    MutationKind.ATTRIBUTE_TOGGLE: _attribute_toggle.operator,
    MutationKind.SYNC_CHAIN_DEPTH_PERTURB: _stubs.make_mutant_stub(
        MutationKind.SYNC_CHAIN_DEPTH_PERTURB, _ISSUE_CDC
    ),
    MutationKind.BIT_EXTRACT_PERMUTE: _stubs.make_mutant_stub(
        MutationKind.BIT_EXTRACT_PERMUTE, _ISSUE_CDC
    ),
    MutationKind.RESET_POLARITY_FLIP: _stubs.make_mutant_stub(
        MutationKind.RESET_POLARITY_FLIP, _ISSUE_CDC
    ),
    # rb-mut operators
    MutationKind.ASSIGN_DROP: _assign_drop.operator,
    MutationKind.ARITH_FLIP: _arith_flip.operator,
    MutationKind.BIT_OP_FLIP: _bit_op_flip.operator,
    MutationKind.COND_NEGATE: _cond_negate.operator,
    MutationKind.COND_CONST: _cond_const.operator,
    MutationKind.PORT_BINDING_SWAP: _stubs.make_mutant_stub(
        MutationKind.PORT_BINDING_SWAP, _ISSUE_RBMUT
    ),
}


CANDIDATES: dict[MutationKind, CandidatesFn] = {
    # CDC operators
    MutationKind.CLOCK_POLARITY_SWAP: _clock_polarity_swap.candidates,
    MutationKind.ATTRIBUTE_TOGGLE: _attribute_toggle.candidates,
    MutationKind.SYNC_CHAIN_DEPTH_PERTURB: _stubs.make_candidates_stub(
        MutationKind.SYNC_CHAIN_DEPTH_PERTURB, _ISSUE_CDC
    ),
    MutationKind.BIT_EXTRACT_PERMUTE: _stubs.make_candidates_stub(
        MutationKind.BIT_EXTRACT_PERMUTE, _ISSUE_CDC
    ),
    MutationKind.RESET_POLARITY_FLIP: _stubs.make_candidates_stub(
        MutationKind.RESET_POLARITY_FLIP, _ISSUE_CDC
    ),
    # rb-mut operators
    MutationKind.ASSIGN_DROP: _assign_drop.candidates,
    MutationKind.ARITH_FLIP: _arith_flip.candidates,
    MutationKind.BIT_OP_FLIP: _bit_op_flip.candidates,
    MutationKind.COND_NEGATE: _cond_negate.candidates,
    MutationKind.COND_CONST: _cond_const.candidates,
    MutationKind.PORT_BINDING_SWAP: _stubs.make_candidates_stub(
        MutationKind.PORT_BINDING_SWAP, _ISSUE_RBMUT
    ),
}


IMPLEMENTED_KINDS: frozenset[MutationKind] = frozenset(
    {
        MutationKind.CLOCK_POLARITY_SWAP,
        MutationKind.ATTRIBUTE_TOGGLE,
        MutationKind.ASSIGN_DROP,
        MutationKind.ARITH_FLIP,
        MutationKind.BIT_OP_FLIP,
        MutationKind.COND_NEGATE,
        MutationKind.COND_CONST,
    }
)
