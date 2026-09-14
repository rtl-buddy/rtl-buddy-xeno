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
from rtl_buddy_xeno import slang as _slang

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
    """Sites = stages that have a same-clock, direct non-blocking reader.

    ``sync_chain_readers.sv`` holds 7 recognised stages (``sync_meta``,
    ``sync_q``, ``sync_out``, ``flag_meta``, ``lone_q``, ``sg_meta``,
    ``sg_q``). ``sync_out`` and ``sg_q`` are consumed only by a
    continuous ``assign`` and ``lone_q`` by nothing at all, so
    7 - 3 = 4 sites.
    """
    mutants = _insert_mutants(_insert_sv())
    assert len(mutants) == 4
    summaries = " ".join(m.diff_summary for m in mutants)
    for name in ("sync_meta", "sync_q", "flag_meta", "sg_meta"):
        assert f"after `{name}`" in summaries
    for name in ("sync_out", "lone_q", "sg_q"):
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
    assert "logic [7:0] sync_meta_xeno_stage_1;" in mutant.sv


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
    """Applying the operator *to the stage it just inserted* yields
    ``_xeno_stage_2``, not a doubled ``_xeno_stage_1_xeno_stage_1``.

    The second round sees two sites — the original ``s1`` and the
    inserted ``s1_xeno_stage_1`` — and only the latter exercises the
    suffix-stripping path, so the assertion picks it out by name rather
    than searching every mutant for the string.
    """
    [first] = _insert_mutants(_TWO_DEEP_SV)
    assert "s1_xeno_stage_1" in first.sv
    second_round = _insert_mutants(first.sv)
    [on_inserted] = [
        m for m in second_round if "after `s1_xeno_stage_1`" in m.diff_summary
    ]
    assert "insert sync stage `s1_xeno_stage_2`" in on_inserted.diff_summary
    assert "s1_xeno_stage_1_xeno_stage" not in on_inserted.sv
    assert "s2 <= s1_xeno_stage_2;" in on_inserted.sv


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


# --- CHAIN_STAGE_INSERT: reader-matching guardrails --------------------------

# Two modules that reuse a stage name. The walker is module-scoped, so
# `dup` in `top_b` must find `top_b`'s own reader and never `top_a`'s —
# rewiring the other module's reader would leave it naming a declaration
# it cannot see.
_CROSS_MODULE_SV = (
    "module top_a (input logic clk, input logic d, output logic q);\n"
    "  logic dup, dup_q;\n"
    "  always_ff @(posedge clk) dup   <= d;\n"
    "  always_ff @(posedge clk) dup_q <= dup;\n"
    "  assign q = dup_q;\n"
    "endmodule\n"
    "\n"
    "module top_b (input logic clk, input logic d, output logic q);\n"
    "  logic dup, dup_q;\n"
    "  always_ff @(posedge clk) dup   <= d;\n"
    "  always_ff @(posedge clk) dup_q <= dup;\n"
    "  assign q = dup_q;\n"
    "endmodule\n"
)


def _elaborates(sv: str) -> bool:
    """True iff pyslang elaborates ``sv`` with no error-or-worse diagnostic.

    Skips the calling test when the ``[slang]`` extra isn't installed —
    the repo-wide gate in ``test_mutant_validity.py`` does the same.
    """
    if not _slang.is_available():
        pytest.skip("pyslang not installed; cannot check elaboration")
    compilation = _slang.elaborate_text(sv)
    return not [d for d in compilation.getAllDiagnostics() if d.isError()]


def test_chain_stage_insert_never_crosses_module_scope() -> None:
    """One site per module, each rewiring only its own module's reader."""
    mutants = _insert_mutants(_CROSS_MODULE_SV)
    assert len(mutants) == 2
    assert all("after `dup`" in m.diff_summary for m in mutants)
    for mutant in mutants:
        head, sep, tail = mutant.sv.partition("module top_b")
        assert sep
        touched = [part for part in (head, tail) if "dup_xeno_stage_1" in part]
        # The declaration, the new flop's LHS and the rewired reader —
        # all three inside one module, none in the other.
        assert len(touched) == 1
        assert touched[0].count("dup_xeno_stage_1") == 3
        untouched = head if touched[0] is tail else tail
        assert "dup_q <= dup;" in untouched
    # Both modules still elaborate: the rewired reader sees the new
    # declaration because it is a sibling in the same scope.
    assert [m.diff_summary for m in mutants if not _elaborates(m.sv)] == []


def test_chain_stage_insert_skips_different_clock_reader() -> None:
    """A consumer on another clock is a crossing, not the next chain link."""
    sv = (
        "module m (input logic clk_a, input logic clk_b, input logic d,\n"
        "          output logic q);\n"
        "  logic s1, s2;\n"
        "  always_ff @(posedge clk_a) s1 <= d;\n"
        "  always_ff @(posedge clk_b) s2 <= s1;\n"
        "  assign q = s2;\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


def test_chain_stage_insert_skips_repeated_reference_reader() -> None:
    """``q2 <= q1 & q1`` reads the stage twice through comb logic — the
    edge is not a direct flop-to-flop wire, so it is not a site (and
    there is no single span to rewrite)."""
    sv = (
        "module m (input logic clk, input logic d, output logic q);\n"
        "  logic q1, q2;\n"
        "  always_ff @(posedge clk) q1 <= d;\n"
        "  always_ff @(posedge clk) q2 <= q1 & q1;\n"
        "  assign q = q2;\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


def test_chain_stage_insert_skips_expression_reader() -> None:
    """``q2 <= q1 & en`` is comb logic on the way: not a synchroniser stage."""
    sv = (
        "module m (input logic clk, input logic d, input logic en,\n"
        "          output logic q);\n"
        "  logic q1, q2;\n"
        "  always_ff @(posedge clk) q1 <= d;\n"
        "  always_ff @(posedge clk) q2 <= q1 & en;\n"
        "  assign q = q2;\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


# --- CHAIN_STAGE_INSERT: declared-type copy ----------------------------------


def test_chain_stage_insert_skips_enum_typed_stage() -> None:
    """A typedef'd enum can't be re-declared by copying ``logic [N-1:0]``
    (and ``$bits`` would erase the type outright), so it is not a site."""
    sv = (
        "typedef enum logic [1:0] {S_IDLE, S_RUN, S_DONE} state_t;\n"
        "module m (input logic clk, input state_t d, output state_t q);\n"
        "  state_t s1;\n"
        "  always_ff @(posedge clk) s1 <= d;\n"
        "  always_ff @(posedge clk) q  <= s1;\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


def test_chain_stage_insert_skips_unpacked_array_stage() -> None:
    """Copying only the packed half of ``logic [3:0] a1 [0:1]`` would
    change the object's shape, so an unpacked declarator is not a site."""
    sv = (
        "module m (input logic clk, input logic [3:0] d [0:1],\n"
        "          output logic [3:0] q);\n"
        "  logic [3:0] a1 [0:1];\n"
        "  logic [3:0] a2 [0:1];\n"
        "  always_ff @(posedge clk) a1 <= d;\n"
        "  always_ff @(posedge clk) a2 <= a1;\n"
        "  assign q = a2[0];\n"
        "endmodule\n"
    )
    assert _insert_mutants(sv) == []


def test_chain_stage_insert_copies_signedness() -> None:
    """A ``logic signed [3:0]`` stage declares a ``logic signed [3:0]``
    sibling — the ``$bits`` spelling would have dropped the sign."""
    [mutant] = [
        m for m in _insert_mutants(_insert_sv()) if "after `sg_meta`" in m.diff_summary
    ]
    assert "logic signed [3:0] sg_meta_xeno_stage_1;" in mutant.sv
    assert "sg_meta_xeno_stage_1 <= sg_meta;" in mutant.sv
    assert "sg_q    <= sg_meta_xeno_stage_1;" in mutant.sv


def test_chain_stage_insert_copies_port_declared_type() -> None:
    """The stage's Q may be declared by an ``output`` port rather than a
    module-body declaration; the port's data type is copied just the same."""
    sv = (
        "module m (input logic clk, input logic [7:0] d,\n"
        "          output logic [7:0] q, output logic [7:0] r);\n"
        "  always_ff @(posedge clk) q <= d;\n"
        "  always_ff @(posedge clk) r <= q;\n"
        "endmodule\n"
    )
    mutants = _insert_mutants(sv)
    assert len(mutants) == 1
    assert "logic [7:0] q_xeno_stage_1;" in mutants[0].sv
    assert "r <= q_xeno_stage_1;" in mutants[0].sv
    assert _elaborates(mutants[0].sv)
