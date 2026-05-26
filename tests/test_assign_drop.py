"""Coverage for the assign_drop operator.

ASSIGN_DROP needs Verible (CST) and optionally pyslang (elaboration
confidence flag in the rationale). These tests skip when the Verible
binary isn't available — same pattern view's own Verible tests use.
"""

from __future__ import annotations

import shutil

import pytest

from rtl_buddy_xeno import Mutant, MutationKind, Mutator, Site

# Skip the whole module when Verible isn't on PATH or vendored.
pytest.importorskip("rtl_buddy_view")
if shutil.which("verible-verilog-syntax") is None:
    pytest.skip(
        "verible-verilog-syntax not on PATH; ASSIGN_DROP tests need it.",
        allow_module_level=True,
    )


_DUT_SV = """\
module sync_chain (
    input  logic clk_dst,
    input  logic d_src,
    output logic q_dst
);
    logic q0, q1;
    always_ff @(posedge clk_dst) q0 <= d_src;
    always_ff @(posedge clk_dst) q1 <= q0;
    always_ff @(posedge clk_dst) q_dst <= q1;
endmodule
"""


def _mutants(sv: str, *, count: int = 10, seed: int = 0) -> list[Mutant]:
    return list(
        Mutator.from_sv(sv).generate(
            kinds=[MutationKind.ASSIGN_DROP],
            count=count,
            seed=seed,
        )
    )


def _candidates(sv: str) -> list[Site]:
    return list(
        Mutator.from_sv(sv).candidates(
            kinds=[MutationKind.ASSIGN_DROP],
        )
    )


def test_finds_one_mutant_per_nonblocking_assign() -> None:
    mutants = _mutants(_DUT_SV, count=99)
    # Three non-blocking assigns in the fixture.
    assert len(mutants) == 3
    for m in mutants:
        assert m.kind is MutationKind.ASSIGN_DROP


def test_mutant_replaces_assign_with_empty_statement() -> None:
    """Dropped assignment becomes ``;`` so the enclosing always_ff stays well-formed."""
    [first, *_] = _mutants(_DUT_SV, count=1)
    # The mutated SV must still parse — empty statement keeps always_ff valid.
    assert ";" in first.sv
    # Exactly one fewer `<=` than the original.
    assert first.sv.count("<=") == _DUT_SV.count("<=") - 1


def test_prediction_perturbs_signal_and_liveness() -> None:
    [first, *_] = _mutants(_DUT_SV, count=1)
    assert first.prediction.perturbs_liveness is True
    assert len(first.prediction.perturbs_signals) == 1
    name = next(iter(first.prediction.perturbs_signals))
    assert name in {"q0", "q1", "q_dst"}
    assert first.prediction.rationale  # non-empty


def test_prediction_carries_no_cdc_rules() -> None:
    """ASSIGN_DROP is an FPV-oracle operator; CDC fields stay empty."""
    [first, *_] = _mutants(_DUT_SV, count=1)
    assert first.prediction.cdc_rules_added == frozenset()
    assert first.prediction.cdc_rules_removed == frozenset()


def test_diff_summary_carries_lhs_name() -> None:
    mutants = _mutants(_DUT_SV, count=99)
    summaries = " ".join(m.diff_summary for m in mutants)
    assert "q0" in summaries
    assert "q1" in summaries
    assert "q_dst" in summaries


def test_candidates_enumerates_in_source_order() -> None:
    sites = _candidates(_DUT_SV)
    assert len(sites) == 3
    lines = [s.line for s in sites]
    assert lines == sorted(lines)


def test_source_with_no_nonblocking_yields_nothing() -> None:
    sv = "module a;\n  assign x = y;\nendmodule\n"
    assert _mutants(sv) == []


def test_seed_determinism() -> None:
    a = _mutants(_DUT_SV, seed=11)
    b = _mutants(_DUT_SV, seed=11)
    assert [m.sv for m in a] == [m.sv for m in b]


def test_rationale_reports_elaboration_confidence() -> None:
    """Rationale records whether pyslang elaborated successfully."""
    [first, *_] = _mutants(_DUT_SV, count=1)
    # Either "pyslang-elaborated" (when [slang] is installed) or "CST-only"
    # (when it isn't) must appear in the rationale.
    assert (
        "pyslang-elaborated" in first.prediction.rationale
        or "CST-only" in first.prediction.rationale
    )
