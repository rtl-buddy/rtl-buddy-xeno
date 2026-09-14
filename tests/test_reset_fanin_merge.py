"""Per-operator coverage for RESET_FANIN_MERGE (xeno#15).

The operator combines two same-polarity reset signals of one module
into a fan-in wire and re-points one flop's async reset at it — the
RDC-005 shape. These tests pin the site arithmetic (one mutant per
usable pair), the gate choice, the "rewrite *every* occurrence inside
the block" rule that keeps the if-body polarity consistent, and the
per-module scoping.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rtl_buddy_xeno import Mutant, MutationKind, Mutator

pytest.importorskip("rtl_buddy_view")
if shutil.which("verible-verilog-syntax") is None:
    pytest.skip(
        "verible-verilog-syntax not on PATH; RESET_FANIN_MERGE needs the CST.",
        allow_module_level=True,
    )

_FIXTURES = Path(__file__).parent / "fixtures"
_FIXTURE = _FIXTURES / "reset_fanin.sv"

_TWO_RESETS = """\
module two_resets (
    input  logic clk,
    input  logic global_rst_n,
    input  logic local_rst_n,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge global_rst_n) begin
        if (!global_rst_n) q <= 1'b0;
        else               q <= d;
    end
endmodule
"""

_ONE_RESET = """\
module one_reset (
    input  logic clk,
    input  logic rst_n,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 1'b0;
        else        q <= d;
    end
endmodule
"""

_MIXED_POLARITY = """\
module mixed_polarity (
    input  logic clk,
    input  logic rst_n,
    input  logic reset,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) q <= 1'b0;
        else        q <= d;
    end
endmodule
"""

_NO_RESET_FLOP = """\
module no_reset_flop (
    input  logic clk,
    input  logic global_rst_n,
    input  logic local_rst_n,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk) q <= d;
endmodule
"""

_ACTIVE_HIGH = """\
module active_high (
    input  logic clk,
    input  logic rst,
    input  logic arst,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or posedge rst) begin
        if (rst) q <= 1'b0;
        else     q <= d;
    end
endmodule
"""

# Module `alpha` carries a single reset (no pair of its own); module
# `beta` carries two. A merge that ignored module scope would pair
# alpha's reset with beta's and emit three mutants.
_TWO_MODULES = """\
module alpha (
    input  logic clk,
    input  logic global_rst_n,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge global_rst_n) begin
        if (!global_rst_n) q <= 1'b0;
        else               q <= d;
    end
endmodule

module beta (
    input  logic clk,
    input  logic local_rst_n,
    input  logic arst_n,
    input  logic d,
    output logic q
);
    always_ff @(posedge clk or negedge local_rst_n) begin
        if (!local_rst_n) q <= 1'b0;
        else              q <= d;
    end
endmodule
"""


def _mutants(sv: str) -> list[Mutant]:
    return list(Mutator.from_sv(sv).generate([MutationKind.RESET_FANIN_MERGE], 999))


def _wire_line(mutant: Mutant) -> str:
    [line] = [
        ln.strip() for ln in mutant.sv.splitlines() if ln.strip().startswith("wire ")
    ]
    return line


# --- site arithmetic ---------------------------------------------------------


def test_two_resets_yield_exactly_one_mutant() -> None:
    mutants = _mutants(_TWO_RESETS)
    assert len(mutants) == 1
    assert mutants[0].kind is MutationKind.RESET_FANIN_MERGE


def test_single_reset_yields_nothing() -> None:
    assert _mutants(_ONE_RESET) == []


def test_three_resets_yield_three_pairwise_mutants() -> None:
    """Fixture has three same-polarity resets and a flop for every pair."""
    mutants = _mutants(_FIXTURE.read_text())
    assert len(mutants) == 3
    assert len({m.sv for m in mutants}) == 3


def test_mixed_polarity_pair_is_skipped() -> None:
    """`rst_n` (active-low) and `reset` (active-high) have no single
    correct fan-in gate, so the pair yields no site at all."""
    assert _mutants(_MIXED_POLARITY) == []


def test_reset_pair_without_an_async_flop_yields_nothing() -> None:
    """Two resets in scope, but the only flop has no reset edge."""
    assert _mutants(_NO_RESET_FLOP) == []


def test_single_reset_fixture_yields_nothing() -> None:
    """``instances_and_resets.sv`` carries one reset name (`rst_n`) per
    module — no pair, so no merge."""
    assert _mutants((_FIXTURES / "instances_and_resets.sv").read_text()) == []


# --- emission ----------------------------------------------------------------


def test_active_low_pair_uses_an_and_gate() -> None:
    [mutant] = _mutants(_TWO_RESETS)
    new_name = "global_rst_n_local_rst_n_xeno_merge_1"
    assert _wire_line(mutant) == f"wire {new_name} = global_rst_n & local_rst_n;"


def test_active_high_pair_uses_an_or_gate() -> None:
    [mutant] = _mutants(_ACTIVE_HIGH)
    new_name = "rst_arst_xeno_merge_1"
    assert _wire_line(mutant) == f"wire {new_name} = rst | arst;"


def test_every_occurrence_inside_the_block_is_rewritten() -> None:
    """The sensitivity edge *and* the if-body polarity check both move
    to the merged wire — rewriting only the edge is the shape Yosys
    rejects (xeno#12)."""
    [mutant] = _mutants(_TWO_RESETS)
    new_name = "global_rst_n_local_rst_n_xeno_merge_1"
    assert f"negedge {new_name}" in mutant.sv
    assert f"if (!{new_name})" in mutant.sv
    assert "if (!global_rst_n)" not in mutant.sv
    assert "negedge global_rst_n)" not in mutant.sv


def test_references_outside_the_block_are_untouched() -> None:
    """The port declarations still name the original resets — only the
    mutated block's references move."""
    [mutant] = _mutants(_TWO_RESETS)
    assert "input  logic global_rst_n," in mutant.sv
    assert "input  logic local_rst_n," in mutant.sv


def test_the_wire_is_declared_immediately_before_the_block() -> None:
    [mutant] = _mutants(_TWO_RESETS)
    lines = mutant.sv.splitlines()
    wire_idx = next(i for i, ln in enumerate(lines) if ln.strip().startswith("wire "))
    assert lines[wire_idx + 1].lstrip().startswith("always_ff")
    # Indentation matches the block it precedes.
    leading = len(lines[wire_idx]) - len(lines[wire_idx].lstrip())
    following = len(lines[wire_idx + 1]) - len(lines[wire_idx + 1].lstrip())
    assert leading == following


def test_modules_never_cross_merge() -> None:
    """`alpha`'s lone reset is never paired with `beta`'s."""
    mutants = _mutants(_TWO_MODULES)
    assert len(mutants) == 1
    assert _wire_line(mutants[0]) == (
        "wire local_rst_n_arst_n_xeno_merge_1 = local_rst_n & arst_n;"
    )


# --- prediction / enumeration ------------------------------------------------


def test_prediction_is_conservative() -> None:
    [mutant] = _mutants(_TWO_RESETS)
    assert mutant.prediction.cdc_rules_added == frozenset()
    assert mutant.prediction.cdc_rules_removed == frozenset()
    assert "RDC-005" in mutant.prediction.rationale
    assert mutant.prediction.perturbs_signals == frozenset(
        {
            "global_rst_n",
            "local_rst_n",
            "global_rst_n_local_rst_n_xeno_merge_1",
        }
    )
    assert not mutant.prediction.perturbs_liveness


def test_candidates_match_generate_count() -> None:
    sv = _FIXTURE.read_text()
    sites = list(Mutator.from_sv(sv).candidates([MutationKind.RESET_FANIN_MERGE]))
    assert len(sites) == len(_mutants(sv)) == 3
    assert [s.line for s in sites] == sorted(s.line for s in sites)
    for site in sites:
        assert site.kind is MutationKind.RESET_FANIN_MERGE
        assert "RDC-005" in site.prediction.rationale


def test_seed_is_deterministic() -> None:
    sv = _FIXTURE.read_text()
    a = list(
        Mutator.from_sv(sv).generate([MutationKind.RESET_FANIN_MERGE], 999, seed=7)
    )
    b = list(
        Mutator.from_sv(sv).generate([MutationKind.RESET_FANIN_MERGE], 999, seed=7)
    )
    assert [m.diff_summary for m in a] == [m.diff_summary for m in b]
