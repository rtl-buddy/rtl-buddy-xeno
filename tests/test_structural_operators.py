"""Per-operator coverage for the structural CDC operators.

BIT_EXTRACT_PERMUTE and SYNC_CHAIN_DEPTH_PERTURB are the highest-risk
operators per #6's risk table — both do structural changes whose
construction-safety isn't a foregone conclusion. CHAIN_STAGE_INSERT
(xeno#13) is riskier still: it *synthesises* source rather than
deleting or rewriting in place. The validity gate (Verible parse +
pyslang elaborate) gives the strong guarantee; these tests cover
per-operator semantics.
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


def test_bit_extract_permute_prediction_conservative() -> None:
    """The operator's prediction stays conservative: the rationale
    *mentions* CDC-019 / CDC-020 (the relevant rules) but
    ``cdc_rules_added`` stays empty because the operator can't
    verify the surrounding sliced-bus reconvergence structure from
    the bit-select site alone. Prior to rtl-buddy-cdc#221 fuzz
    integration the prediction was unconditional CDC-019/CDC-020,
    which over-claimed on the majority of corpus parents."""
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.BIT_EXTRACT_PERMUTE], count=1
    )
    assert first.prediction.cdc_rules_added == frozenset()
    # Rationale still calls out the rules the mutation targets so
    # the downstream consumer can see operator intent.
    assert "CDC-019" in first.prediction.rationale
    assert "CDC-020" in first.prediction.rationale


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


def test_sync_chain_depth_perturb_prediction_conservative() -> None:
    """The operator's prediction stays conservative: the rationale
    mentions CDC-002 and CDC-018 (the rules that would fire for
    the right parent chain shape) but ``cdc_rules_added`` is empty.
    Prior to rtl-buddy-cdc#221 fuzz integration the prediction was
    unconditional CDC-002/CDC-018, which over-claimed on chains
    that didn't sit at the required-depth boundary."""
    [first, *_] = Mutator.from_sv(_sv()).generate(
        [MutationKind.SYNC_CHAIN_DEPTH_PERTURB], count=1
    )
    assert first.prediction.cdc_rules_added == frozenset()
    assert "CDC-002" in first.prediction.rationale
    assert "CDC-018" in first.prediction.rationale
    # ``perturbs_signals`` is the operator's structural side-effect
    # claim (which signal lost its driving flop) — that's verifiable
    # from the CST alone, so we keep it as a positive prediction.
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


# --- CHAIN_STAGE_INSERT ------------------------------------------------------

_INSERT_FIXTURE = Path(__file__).parent / "fixtures" / "sync_chain_readers.sv"

# A 2-deep chain: `s1` has a downstream flop reader, `s2` only feeds an
# `assign`, so exactly one of the two stages is an insertion site.
_TWO_DEEP_SV = (
    "module m (input logic clk, input logic d, output logic q);\n"
    "  logic s1, s2;\n"
    "  always_ff @(posedge clk) s1 <= d;\n"
    "  always_ff @(posedge clk) s2 <= s1;\n"
    "  assign q = s2;\n"
    "endmodule\n"
)


def _insert_sv() -> str:
    return _INSERT_FIXTURE.read_text()


def _insert_mutants(sv: str) -> list:
    return list(Mutator.from_sv(sv).generate([MutationKind.CHAIN_STAGE_INSERT], 999))


def test_chain_stage_insert_site_count_on_fixture() -> None:
    """Sites = stages that have a downstream non-blocking reader.

    ``sync_chain_readers.sv`` holds 5 recognised stages (``sync_meta``,
    ``sync_q``, ``sync_out``, ``flag_meta``, ``lone_q``). ``sync_out``
    is consumed only by a continuous ``assign`` and ``lone_q`` by
    nothing at all, so 5 - 2 = 3 sites.
    """
    mutants = _insert_mutants(_insert_sv())
    assert len(mutants) == 3
    summaries = " ".join(m.diff_summary for m in mutants)
    for name in ("sync_meta", "sync_q", "flag_meta"):
        assert f"after `{name}`" in summaries
    for name in ("sync_out", "lone_q"):
        assert f"after `{name}`" not in summaries


def test_chain_stage_insert_adds_exactly_one_always_ff() -> None:
    """The mutant is the parent plus one synthesised flop — no more."""
    sv = _insert_sv()
    for mutant in _insert_mutants(sv):
        assert mutant.sv.count("always_ff") == sv.count("always_ff") + 1


def test_chain_stage_insert_rewires_reader_only() -> None:
    """The reader consumes the new register; the stage still drives its
    original Q (the insertion goes *between* them, it doesn't rename
    the stage)."""
    [mutant] = [
        m
        for m in _insert_mutants(_insert_sv())
        if "after `sync_meta`" in m.diff_summary
    ]
    assert "sync_q    <= sync_meta_xeno_stage_1;" in mutant.sv
    assert "sync_meta <= src_q;" in mutant.sv
    assert "sync_meta_xeno_stage_1 <= sync_meta;" in mutant.sv
    assert "logic [$bits(sync_meta)-1:0] sync_meta_xeno_stage_1;" in mutant.sv


def test_chain_stage_insert_two_deep_chain_yields_one_site() -> None:
    """Only the stage with a flop reader is a site — the tail isn't."""
    mutants = _insert_mutants(_TWO_DEEP_SV)
    assert len(mutants) == 1
    assert "after `s1`" in mutants[0].diff_summary


def test_chain_stage_insert_lone_flop_yields_no_site() -> None:
    """A single flop nothing reads — inserting would leave a dangling reg."""
    sv = (
        "module m (input logic clk, input logic d, output logic q);\n"
        "  always_ff @(posedge clk) q <= d;\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


def test_chain_stage_insert_assign_only_reader_yields_no_site() -> None:
    """A continuous ``assign`` is not a reader for this operator: the
    inserted flop has to land between two *sequential* elements."""
    sv = (
        "module m (input logic clk, input logic d, output logic q);\n"
        "  logic s1;\n"
        "  always_ff @(posedge clk) s1 <= d;\n"
        "  assign q = s1;\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


def test_chain_stage_insert_reader_before_stage() -> None:
    """The reader may sit textually *before* the stage — the two byte
    splices are applied highest-offset-first either way."""
    sv = (
        "module m (input logic clk, input logic d, output logic q);\n"
        "  logic s1, s2;\n"
        "  always_ff @(posedge clk) s2 <= s1;\n"
        "  always_ff @(posedge clk) s1 <= d;\n"
        "  assign q = s2;\n"
        "endmodule\n"
    )
    mutants = _insert_mutants(sv)
    assert len(mutants) == 1
    mutant = mutants[0]
    assert "s2 <= s1_xeno_stage_1;" in mutant.sv
    assert "s1_xeno_stage_1 <= s1;" in mutant.sv
    assert "s1 <= d;" in mutant.sv


def test_chain_stage_insert_reapplication_numbers_the_suffix() -> None:
    """Applying the operator to its own output yields ``_xeno_stage_2``,
    not a doubled ``_xeno_stage_1_xeno_stage_1`` suffix."""
    [first] = _insert_mutants(_TWO_DEEP_SV)
    assert "s1_xeno_stage_1" in first.sv
    second_round = _insert_mutants(first.sv)
    assert second_round
    for mutant in second_round:
        assert "_xeno_stage_2" in mutant.diff_summary
        assert "_xeno_stage_1_xeno_stage" not in mutant.sv


def test_chain_stage_insert_prediction_conservative() -> None:
    """Deepening a chain is the CDC-018 shape, but only from depth >=3.
    The CST recogniser can't count upstream stages, so the rationale
    names the rule and ``cdc_rules_added`` stays empty."""
    [first, *_] = Mutator.from_sv(_insert_sv()).generate(
        [MutationKind.CHAIN_STAGE_INSERT], count=1
    )
    assert first.prediction.cdc_rules_added == frozenset()
    assert first.prediction.cdc_rules_removed == frozenset()
    assert "CDC-018" in first.prediction.rationale
    assert first.prediction.perturbs_liveness is False
    lhs = first.diff_summary.rsplit("after `", 1)[1].rstrip("`")
    assert first.prediction.perturbs_signals == frozenset({lhs})


def test_chain_stage_insert_candidates_match_generate() -> None:
    """``candidates()`` enumerates exactly the sites ``generate()`` mutates."""
    sv = _insert_sv()
    sites = list(Mutator.from_sv(sv).candidates([MutationKind.CHAIN_STAGE_INSERT]))
    assert len(sites) == len(_insert_mutants(sv))
    # candidates() is source order; generate() shuffles.
    assert [s.line for s in sites] == sorted(s.line for s in sites)
