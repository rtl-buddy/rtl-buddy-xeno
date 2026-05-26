"""Coverage for the attribute_toggle operator.

ATTRIBUTE_TOGGLE is parser-free (regex scanner with string/comment
awareness), so these tests run in the no-extras install path.
"""

from __future__ import annotations

import pytest

from rtl_buddy_xeno import Mutant, MutationKind, Mutator, Prediction, Site


def _mutants(sv: str, *, count: int = 10, seed: int = 0) -> list[Mutant]:
    return list(
        Mutator.from_sv(sv).generate(
            kinds=[MutationKind.ATTRIBUTE_TOGGLE],
            count=count,
            seed=seed,
        )
    )


def _candidates(sv: str) -> list[Site]:
    return list(
        Mutator.from_sv(sv).candidates(
            kinds=[MutationKind.ATTRIBUTE_TOGGLE],
        )
    )


def test_strips_single_attribute() -> None:
    sv = "(* cdc_sync *) logic q;\n"
    [m] = _mutants(sv)
    assert "(* cdc_sync *)" not in m.sv
    assert m.sv == "logic q;\n"
    assert m.kind is MutationKind.ATTRIBUTE_TOGGLE


def test_prediction_for_known_attribute_includes_cdc_rules() -> None:
    sv = "(* cdc_sync *) logic q;\n"
    [m] = _mutants(sv)
    assert "CDC-002" in m.prediction.cdc_rules_added
    assert "CDC-003" in m.prediction.cdc_rules_added
    assert m.prediction.rationale  # non-empty


def test_prediction_for_unknown_attribute_is_empty_with_rationale() -> None:
    sv = "(* my_custom_attr *) logic q;\n"
    [m] = _mutants(sv)
    assert m.prediction.cdc_rules_added == frozenset()
    assert "exploratory candidate" in m.prediction.rationale


def test_multiple_attributes_yield_multiple_mutants() -> None:
    sv = (
        "(* cdc_sync *) logic a;\n"
        "(* cdc_gray *) logic [3:0] b;\n"
        "(* glitchless_clock_mux *) logic c;\n"
    )
    mutants = _mutants(sv, count=99)
    assert len(mutants) == 3
    stripped = {m.diff_summary for m in mutants}
    assert any("cdc_sync" in s for s in stripped)
    assert any("cdc_gray" in s for s in stripped)
    assert any("glitchless_clock_mux" in s for s in stripped)


def test_skips_attribute_lookalike_inside_string_literal() -> None:
    sv = '$display("(* not_real *)");\n(* cdc_sync *) logic q;\n'
    mutants = _mutants(sv)
    # Only the real attribute should be detected.
    assert len(mutants) == 1
    assert "cdc_sync" in mutants[0].diff_summary


def test_skips_attribute_lookalike_inside_line_comment() -> None:
    sv = "// (* not_real *) ignored\n(* cdc_sync *) logic q;\n"
    mutants = _mutants(sv)
    assert len(mutants) == 1
    assert "cdc_sync" in mutants[0].diff_summary


def test_skips_attribute_lookalike_inside_block_comment() -> None:
    sv = "/* (* not_real *) */\n(* cdc_sync *) logic q;\n"
    mutants = _mutants(sv)
    assert len(mutants) == 1
    assert "cdc_sync" in mutants[0].diff_summary


def test_candidates_source_order() -> None:
    sv = "(* cdc_sync *) logic a;\n(* cdc_gray *) logic b;\n"
    sites = _candidates(sv)
    lines = [s.line for s in sites]
    assert lines == sorted(lines)
    for s in sites:
        assert isinstance(s.prediction, Prediction)
        assert s.snippet.startswith("(*")


def test_no_attributes_yields_nothing() -> None:
    sv = "module a; endmodule\n"
    assert _mutants(sv) == []


def test_double_star_is_not_an_attribute() -> None:
    """``(**`` is not an SV attribute opener; don't false-positive on it."""
    sv = "// (** doc comment \n(* cdc_sync *) logic q;\n"
    mutants = _mutants(sv)
    assert len(mutants) == 1
    assert "cdc_sync" in mutants[0].diff_summary


def test_seed_determinism_within_kind() -> None:
    sv = (
        "(* cdc_sync *) logic a;\n"
        "(* cdc_gray *) logic b;\n"
        "(* glitchless_clock_mux *) logic c;\n"
    )
    a = _mutants(sv, seed=7)
    b = _mutants(sv, seed=7)
    assert [m.sv for m in a] == [m.sv for m in b]


@pytest.mark.parametrize(
    "attr_name, expected_rules",
    [
        ("cdc_sync", {"CDC-002", "CDC-003"}),
        ("cdc_gray", {"CDC-019"}),
        ("glitchless_clock_mux", {"CDC-008"}),
        ("reset_sync", {"RDC-001"}),
        ("reset_polarity", {"RDC-001", "RDC-007"}),
    ],
)
def test_known_attribute_predictions(attr_name: str, expected_rules: set[str]) -> None:
    sv = f"(* {attr_name} *) logic q;\n"
    [m] = _mutants(sv)
    assert set(m.prediction.cdc_rules_added) == expected_rules
