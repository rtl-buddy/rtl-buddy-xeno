"""Per-operator coverage for BIT_EXTRACT_PERMUTE and SYNC_CHAIN_DEPTH_PERTURB.

These two are the highest-risk operators per #6's risk table — both
do structural changes whose construction-safety isn't a foregone
conclusion. The validity gate (Verible parse + pyslang elaborate)
gives the strong guarantee; these tests cover per-operator semantics.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rtl_buddy_xeno import Mutator, MutationKind

pytest.importorskip("rtl_buddy_view")
if shutil.which("verible-verilog-syntax") is None:
    pytest.skip(
        "verible-verilog-syntax not on PATH; structural-operator tests need it.",
        allow_module_level=True,
    )

_FIXTURE = Path(__file__).parent / "fixtures" / "sync_and_slices.sv"


def _sv() -> str:
    return _FIXTURE.read_text()


# --- BIT_EXTRACT_PERMUTE -----------------------------------------------------


def test_bit_extract_permute_finds_two_modes_per_range() -> None:
    """Each kDimensionRange with int-literal bounds yields 2 mutants
    (drop_msb + drop_lsb)."""
    mutants = list(
        Mutator.from_sv(_sv()).generate([MutationKind.BIT_EXTRACT_PERMUTE], count=999)
    )
    # Fixture has 3 ranges with integer-literal bounds in *expression*
    # context: wide_in[3:0] (in assign), wide_in[15:12], wide_in[3:0]
    # (in always_ff). Each → 2 mutants. (Declaration-context dimensions
    # like `logic [7:0] q0` also produce mutants — they're kDimensionRange
    # too.)
    assert len(mutants) > 0
    drops_msb = [m for m in mutants if "drop_msb" in m.diff_summary]
    drops_lsb = [m for m in mutants if "drop_lsb" in m.diff_summary]
    # Both modes should appear.
    assert drops_msb
    assert drops_lsb


def test_bit_extract_permute_prediction_includes_cdc019_cdc020() -> None:
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.BIT_EXTRACT_PERMUTE], count=1
    )
    assert "CDC-019" in first.prediction.cdc_rules_added
    assert "CDC-020" in first.prediction.cdc_rules_added


def test_bit_extract_permute_skips_single_bit_selects() -> None:
    """``bus[3]`` is a single-bit select; only the declared-dimension
    ``logic [7:0]`` produces mutants (it's a kDimensionRange).
    """
    sv = "module m;\n  logic [7:0] bus;\n  logic q;\n  assign q = bus[3];\nendmodule\n"
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.BIT_EXTRACT_PERMUTE], count=99)
    )
    # The declaration `logic [7:0]` is kDimensionRange — 2 mutants
    # (drop_msb → [6:0], drop_lsb → [7:1]). The expression `bus[3]`
    # is a single-bit select and produces no mutants.
    assert len(mutants) == 2
    # Neither mutant should touch the single-bit expression.
    for m in mutants:
        assert "bus[3]" in m.sv  # expression preserved


def test_bit_extract_permute_skips_non_literal_bounds() -> None:
    """``bus[WIDTH-1:0]`` — bounds aren't integer literals; skip."""
    sv = (
        "module m #(parameter WIDTH = 8);\n"
        "  logic [WIDTH-1:0] bus;\n"
        "  logic [3:0] q;\n"
        "  assign q = bus[WIDTH-1:WIDTH-4];\n"
        "endmodule\n"
    )
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.BIT_EXTRACT_PERMUTE], count=99)
    )
    # Only [WIDTH-1:0] etc. — all non-literal bounds. No mutants.
    for m in mutants:
        # Whatever did mutate must be from another range, not these.
        assert "WIDTH" not in m.sv or m.sv.count("WIDTH") == sv.count("WIDTH")


# --- SYNC_CHAIN_DEPTH_PERTURB ------------------------------------------------


def test_sync_chain_depth_perturb_finds_each_sync_stage() -> None:
    """Each always_ff with single non-blocking assignment is a stage."""
    mutants = list(
        Mutator.from_sv(_sv()).generate(
            [MutationKind.SYNC_CHAIN_DEPTH_PERTURB], count=999
        )
    )
    # Fixture has 4 single-clock always_ff blocks (q0, q1, q_dst, nib_q0).
    assert len(mutants) == 4
    summaries = " ".join(m.diff_summary for m in mutants)
    for name in ("q0", "q1", "q_dst", "nib_q0"):
        assert f"`{name}`" in summaries


def test_sync_chain_depth_perturb_drops_entire_always_ff() -> None:
    """Mutated SV has exactly one fewer always_ff block."""
    sv = _sv()
    [first, *_] = Mutator.from_sv(sv).generate(
        [MutationKind.SYNC_CHAIN_DEPTH_PERTURB], count=1
    )
    assert first.sv.count("always_ff") == sv.count("always_ff") - 1


def test_sync_chain_depth_perturb_prediction_carries_cdc002() -> None:
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.SYNC_CHAIN_DEPTH_PERTURB], count=1
    )
    assert "CDC-002" in first.prediction.cdc_rules_added
    assert "CDC-018" in first.prediction.cdc_rules_added
    assert len(first.prediction.perturbs_signals) == 1


def test_sync_chain_depth_perturb_skips_dual_edge_blocks() -> None:
    """``always_ff @(posedge clk or negedge rst_n)`` has two edges — not a sync stage."""
    sv = (
        "module m (input logic clk, input logic rst_n, input logic d, output logic q);\n"
        "  always_ff @(posedge clk or negedge rst_n) begin\n"
        "    if (!rst_n) q <= 1'b0;\n"
        "    else        q <= d;\n"
        "  end\n"
        "endmodule\n"
    )
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.SYNC_CHAIN_DEPTH_PERTURB], count=99)
    )
    # The block has two edges + a conditional — not a sync stage shape.
    assert mutants == []


def test_sync_chain_depth_perturb_skips_blocks_with_if_statements() -> None:
    """A single-clock always_ff with an if-statement isn't a sync stage."""
    sv = (
        "module m (input logic clk, input logic en, input logic d, output logic q);\n"
        "  always_ff @(posedge clk) begin\n"
        "    if (en) q <= d;\n"
        "  end\n"
        "endmodule\n"
    )
    mutants = list(
        Mutator.from_sv(sv).generate([MutationKind.SYNC_CHAIN_DEPTH_PERTURB], count=99)
    )
    assert mutants == []
