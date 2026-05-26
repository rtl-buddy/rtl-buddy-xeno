"""Per-operator coverage for the rb-mut expression-level operators.

ARITH_FLIP, BIT_OP_FLIP, COND_NEGATE, COND_CONST. The four share a
fixture (``tests/fixtures/expressions.sv``) and a discovery pattern
(walk Verible's ``kBinaryExpression`` / ``kUnaryPrefixExpression`` /
``kIfHeader`` / ``kConditionExpression`` subtrees). The heavy
mutate-then-parse check lives in ``test_mutant_validity.py`` — these
tests cover per-operator semantics: site count, predicted swap,
``Prediction`` field shape.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rtl_buddy_xeno import Mutator, MutationKind

pytest.importorskip("rtl_buddy_view")
if shutil.which("verible-verilog-syntax") is None:
    pytest.skip(
        "verible-verilog-syntax not on PATH; expression-operator tests need it.",
        allow_module_level=True,
    )

_FIXTURE = Path(__file__).parent / "fixtures" / "expressions.sv"


def _sv() -> str:
    return _FIXTURE.read_text()


# --- ARITH_FLIP --------------------------------------------------------------


def test_arith_flip_finds_three_arithmetic_sites() -> None:
    sv = _sv()
    mutants = list(Mutator.from_sv(sv).generate([MutationKind.ARITH_FLIP], count=999))
    # Fixture has `a + b`, `a - b`, `a * b` (3 arithmetic ops).
    assert len(mutants) == 3
    summaries = " ".join(m.diff_summary for m in mutants)
    assert "`+` -> `-`" in summaries
    assert "`-` -> `+`" in summaries
    assert "`*` -> `/`" in summaries


def test_arith_flip_prediction_has_rationale() -> None:
    sv = _sv()
    [first, *_] = Mutator.from_sv(sv).generate([MutationKind.ARITH_FLIP], count=1)
    assert first.prediction.rationale  # non-empty
    assert not first.prediction.cdc_rules_added  # FPV operator, no CDC prediction


# --- BIT_OP_FLIP -------------------------------------------------------------


def test_bit_op_flip_finds_binary_and_unary_sites() -> None:
    sv = _sv()
    mutants = list(Mutator.from_sv(sv).generate([MutationKind.BIT_OP_FLIP], count=999))
    # Fixture has `a & b`, `a | b`, `~a` plus `c & d` inside an if-condition.
    assert len(mutants) == 4
    binary_swaps = [
        m
        for m in mutants
        if "`&` -> `|`" in m.diff_summary or "`|` -> `&`" in m.diff_summary
    ]
    unary_strips = [m for m in mutants if "strip unary `~`" in m.diff_summary]
    assert len(binary_swaps) == 3  # two top-level + one inside if-cond
    assert len(unary_strips) == 1


def test_bit_op_flip_unary_strip_removes_tilde() -> None:
    sv = _sv()
    mutants = list(Mutator.from_sv(sv).generate([MutationKind.BIT_OP_FLIP], count=999))
    [strip_mutant] = [m for m in mutants if "strip unary" in m.diff_summary]
    # The mutated SV should have one fewer `~` than the parent.
    assert strip_mutant.sv.count("~") == sv.count("~") - 1


# --- COND_NEGATE -------------------------------------------------------------


def test_cond_negate_finds_if_and_ternary_sites() -> None:
    sv = _sv()
    mutants = list(Mutator.from_sv(sv).generate([MutationKind.COND_NEGATE], count=999))
    # Fixture has 1 ternary + 2 if conditions.
    assert len(mutants) == 3
    summaries = " ".join(m.diff_summary for m in mutants)
    assert "negate ternary" in summaries
    assert "negate if" in summaries


def test_cond_negate_wraps_in_bang_parens() -> None:
    sv = _sv()
    [first, *_] = Mutator.from_sv(sv).generate([MutationKind.COND_NEGATE], count=1)
    # Every mutant must add exactly one `!(` pattern.
    assert first.sv.count("!(") == sv.count("!(") + 1


# --- COND_CONST --------------------------------------------------------------


def test_cond_const_emits_two_mutants_per_site() -> None:
    sv = _sv()
    mutants = list(Mutator.from_sv(sv).generate([MutationKind.COND_CONST], count=999))
    # 3 sites × 2 values (`1` and `0`) = 6 mutants.
    assert len(mutants) == 6
    forced_to_1 = [m for m in mutants if m.diff_summary.endswith("-> `1`")]
    forced_to_0 = [m for m in mutants if m.diff_summary.endswith("-> `0`")]
    assert len(forced_to_1) == 3
    assert len(forced_to_0) == 3


def test_cond_const_candidates_returns_one_per_site_not_per_value() -> None:
    """`candidates` enumerates *sites*; the operator's branching factor
    (two mutants per site) is exposed via `generate`, not `candidates`."""
    sv = _sv()
    sites = list(Mutator.from_sv(sv).candidates([MutationKind.COND_CONST]))
    assert len(sites) == 3
