"""Per-operator coverage for PORT_BINDING_SWAP and RESET_POLARITY_FLIP."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rtl_buddy_xeno import Mutator, MutationKind

pytest.importorskip("rtl_buddy_view")
if shutil.which("verible-verilog-syntax") is None:
    pytest.skip(
        "verible-verilog-syntax not on PATH; PR-B operator tests need it.",
        allow_module_level=True,
    )

_FIXTURE = Path(__file__).parent / "fixtures" / "instances_and_resets.sv"


def _sv() -> str:
    return _FIXTURE.read_text()


# --- PORT_BINDING_SWAP -------------------------------------------------------


def test_port_binding_swap_finds_adjacent_pairs_per_instance() -> None:
    """Each instance has 5 named ports → 4 adjacent-pair swaps per instance."""
    mutants = list(
        Mutator.from_sv(_sv()).generate([MutationKind.PORT_BINDING_SWAP], count=999)
    )
    # Two `child` instantiations × 4 adjacent pairs each = 8 mutants.
    assert len(mutants) == 8


def test_port_binding_swap_preserves_named_port_names() -> None:
    """Mutation swaps *expressions* (.name(X) and .name(Y)), not names."""
    sv = _sv()
    [first, *_] = Mutator.from_sv(sv).generate(
        [MutationKind.PORT_BINDING_SWAP], count=1
    )
    # The names ``.clk``, ``.rst_n``, ``.d``, ``.en``, ``.q`` all still appear.
    for name in (".clk(", ".rst_n(", ".d(", ".en(", ".q("):
        assert name in first.sv


def test_port_binding_swap_prediction_names_both_ports() -> None:
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.PORT_BINDING_SWAP], count=1
    )
    assert len(first.prediction.perturbs_signals) == 2
    assert not first.prediction.perturbs_liveness


def test_port_binding_swap_skips_instances_with_fewer_than_two_ports() -> None:
    sv = "module a;\n  child u (.clk(clk));\nendmodule\n"
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.PORT_BINDING_SWAP], count=99)
    )
    assert mutants == []


def test_port_binding_swap_no_instances_yields_nothing() -> None:
    sv = "module empty; endmodule\n"
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.PORT_BINDING_SWAP], count=99)
    )
    assert mutants == []


# --- RESET_POLARITY_FLIP -----------------------------------------------------


def test_reset_polarity_flip_finds_two_reset_edges() -> None:
    """Fixture has two always_ff blocks with `negedge rst_n` each."""
    mutants = list(
        Mutator.from_sv(_sv()).generate([MutationKind.RESET_POLARITY_FLIP], count=999)
    )
    # Two reset edges, both `negedge rst_n` → flip both to `posedge rst_n`.
    assert len(mutants) == 2


def test_reset_polarity_flip_skips_clock_edges() -> None:
    """The `posedge clk` tokens should NOT match — `clk` isn't a reset name."""
    mutants = list(
        Mutator.from_sv(_sv()).generate([MutationKind.RESET_POLARITY_FLIP], count=999)
    )
    for m in mutants:
        assert "rst" in m.diff_summary.lower() or "reset" in m.diff_summary.lower()
        assert (
            "clk" not in m.diff_summary.split("on `")[1]
            if "on `" in m.diff_summary
            else True
        )


def test_reset_polarity_flip_prediction_includes_rdc007() -> None:
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.RESET_POLARITY_FLIP], count=1
    )
    assert "RDC-007" in first.prediction.cdc_rules_added


def test_reset_polarity_flip_rationale_records_slang_confidence() -> None:
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.RESET_POLARITY_FLIP], count=1
    )
    assert (
        "pyslang-elaborated" in first.prediction.rationale
        or "name-heuristic only" in first.prediction.rationale
    )


def test_reset_polarity_flip_matches_various_reset_names() -> None:
    sv = (
        "module m (input logic clk, input logic reset, input logic rstn);\n"
        "  logic q;\n"
        "  always_ff @(posedge clk or posedge reset) q <= 1'b0;\n"
        "  always_ff @(posedge clk or negedge rstn) q <= 1'b0;\n"
        "endmodule\n"
    )
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.RESET_POLARITY_FLIP], count=99)
    )
    # Both `reset` and `rstn` should match the heuristic.
    assert len(mutants) == 2


def test_reset_polarity_flip_no_reset_yields_nothing() -> None:
    sv = (
        "module m (input logic clk, input logic data);\n"
        "  logic q;\n"
        "  always_ff @(posedge clk) q <= data;\n"
        "endmodule\n"
    )
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.RESET_POLARITY_FLIP], count=99)
    )
    assert mutants == []
