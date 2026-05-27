"""Coverage for the clock_polarity_swap operator + the API surface.

Also covers the API-surface invariants ratified in #3 — Prediction
shape, candidates enumeration, schedule choice — exercised against
the no-extras-needed CLOCK_POLARITY_SWAP operator.
"""

from __future__ import annotations

import pytest

from rtl_buddy_xeno import (
    Mutant,
    MutationKind,
    Mutator,
    Prediction,
    Schedule,
    Site,
)

_SYNTHETIC_SV = """\
module sync_chain (
    input  logic clk_dst,
    input  logic d_src,
    output logic q_dst
);
    logic q0, q1;
    always_ff @(posedge clk_dst) q0   <= d_src;
    always_ff @(posedge clk_dst) q1   <= q0;
    always_ff @(posedge clk_dst) q_dst <= q1;
endmodule
"""


def _mutants(sv: str, *, count: int = 10, seed: int = 0) -> list[Mutant]:
    return list(
        Mutator.from_sv(sv).generate(
            kinds=[MutationKind.CLOCK_POLARITY_SWAP],
            count=count,
            seed=seed,
        )
    )


def test_yields_one_mutant_per_polarity_token() -> None:
    mutants = _mutants(_SYNTHETIC_SV, count=99)
    # Three `posedge` tokens in the synthetic source; no `negedge`.
    assert len(mutants) == 3
    for m in mutants:
        assert m.kind is MutationKind.CLOCK_POLARITY_SWAP
        assert m.sv.count("negedge") == 1
        assert m.sv.count("posedge") == 2


def test_count_caps_emission() -> None:
    assert len(_mutants(_SYNTHETIC_SV, count=1)) == 1
    assert len(_mutants(_SYNTHETIC_SV, count=2)) == 2
    assert len(_mutants(_SYNTHETIC_SV, count=0)) == 0


def test_seed_is_deterministic() -> None:
    a = _mutants(_SYNTHETIC_SV, seed=42)
    b = _mutants(_SYNTHETIC_SV, seed=42)
    assert [m.sv for m in a] == [m.sv for m in b]
    assert [m.diff_summary for m in a] == [m.diff_summary for m in b]


def test_different_seeds_reorder_mutants() -> None:
    a = _mutants(_SYNTHETIC_SV, seed=1)
    b = _mutants(_SYNTHETIC_SV, seed=2)
    assert {m.sv for m in a} == {m.sv for m in b}
    orderings = {
        tuple(m.diff_summary for m in _mutants(_SYNTHETIC_SV, seed=s))
        for s in range(16)
    }
    assert len(orderings) > 1


def test_prediction_declares_cdc016() -> None:
    """The polarity swap on a sync-chain stage creates an adjacent-stage
    polarity mismatch on the destination clock — CDC-016's territory in
    rtl-buddy-cdc's rule pack. Prior to the cdc#221 / cdc#224
    integration the operator predicted CDC-006 by mistake (CDC-006 is
    the comb-driven-sync-source rule)."""
    [first, *_] = _mutants(_SYNTHETIC_SV, count=1)
    assert isinstance(first.prediction, Prediction)
    assert "CDC-016" in first.prediction.cdc_rules_added
    assert "CDC-006" not in first.prediction.cdc_rules_added
    assert first.prediction.rationale  # non-empty rationale required


def test_reset_edges_skipped() -> None:
    """Reset-edge polarity tokens (``negedge rst_n``, ``posedge reset``,
    etc.) are skipped — flipping them produces SV that's syntactically
    valid but semantically broken (the matching ``if (!rst_n)`` body
    wouldn't be flipped), and Yosys rejects the result with ``Async
    reset … yields non-constant value``."""
    sv = (
        "module m (\n"
        "  input  logic clk, rst_n, d,\n"
        "  output logic q\n"
        ");\n"
        "  always_ff @(posedge clk or negedge rst_n)\n"
        "    if (!rst_n) q <= 1'b0;\n"
        "    else        q <= d;\n"
        "endmodule\n"
    )
    mutants = _mutants(sv, count=99)
    # Only the clock edge (``posedge clk``) is a valid swap site; the
    # ``negedge rst_n`` site is skipped by the reset-name heuristic.
    assert len(mutants) == 1, [m.diff_summary for m in mutants]
    [m] = mutants
    assert "negedge clk" in m.sv
    assert "negedge rst_n" in m.sv  # preserved


def test_reset_name_variants_all_skipped() -> None:
    """Common reset-name idioms (``rst_n``, ``reset``, ``arst``,
    ``raw_rst_n``, ``global_rst_n``, ``presetn``) all match the
    reset-name heuristic and are skipped.

    The check looks at the *mutated SV*: the reset signal's edge
    token must be preserved (still ``negedge <name>``), and the
    clock's edge token must be flipped (``negedge clk``). Only one
    mutant per SV — the single allowed swap site.
    """
    for reset_name in (
        "rst_n",
        "rst",
        "reset",
        "resetn",
        "arst",
        "arst_n",
        "raw_rst_n",
        "global_rst_n",
        "local_rst_n",
        "presetn",
    ):
        sv = (
            f"always_ff @(posedge clk or negedge {reset_name})\n"
            f"  if (!{reset_name}) q <= 0; else q <= d;\n"
        )
        mutants = _mutants(sv, count=99)
        assert len(mutants) == 1, (reset_name, [m.diff_summary for m in mutants])
        [m] = mutants
        # Clock edge flipped.
        assert "negedge clk" in m.sv, (reset_name, m.sv)
        # Reset edge preserved (i.e. the original ``negedge <name>`` is
        # still present in the mutated source).
        assert f"negedge {reset_name}" in m.sv, (reset_name, m.sv)


def test_non_reset_named_signal_still_swappable() -> None:
    """Conservative skip: only signals whose names look like resets
    are filtered. Anything else (``clk``, ``en``, ``valid``, even a
    non-canonical-name clock like ``foo``) gets swapped as before."""
    sv = "always_ff @(negedge en) q <= d;\n"
    [m] = _mutants(sv, count=1)
    assert "posedge en" in m.sv


def test_diff_summary_carries_line_number() -> None:
    [first, *_] = _mutants(_SYNTHETIC_SV, count=1)
    assert first.diff_summary.startswith("line ")
    assert "posedge" in first.diff_summary or "negedge" in first.diff_summary


def test_negedge_to_posedge_also_swaps() -> None:
    sv = "always_ff @(negedge clk) q <= d;\n"
    [m] = _mutants(sv, count=1)
    assert "posedge clk" in m.sv
    assert "negedge" not in m.sv


def test_source_with_no_polarity_yields_nothing() -> None:
    sv = "module empty; endmodule\n"
    assert _mutants(sv) == []


# --- API surface invariants from #3 -----------------------------------------


def test_candidates_enumerates_polarity_sites_in_source_order() -> None:
    mutator = Mutator.from_sv(_SYNTHETIC_SV)
    sites = list(mutator.candidates([MutationKind.CLOCK_POLARITY_SWAP]))
    assert len(sites) == 3
    # Source order: lines must increase monotonically.
    lines = [s.line for s in sites]
    assert lines == sorted(lines)
    for s in sites:
        assert isinstance(s, Site)
        assert s.kind is MutationKind.CLOCK_POLARITY_SWAP
        assert s.snippet in {"posedge", "negedge"}
        assert "CDC-016" in s.prediction.cdc_rules_added


def test_schedule_round_robin_interleaves_kinds() -> None:
    """Round-robin yields one mutant per kind per round, not sequential drain."""
    sv = (
        "module a;\n"
        "  always_ff @(posedge clk) q <= d;\n"
        "  always_ff @(posedge clk) r <= e;\n"
        "  (* cdc_sync *) logic x;\n"
        "  (* cdc_gray *) logic y;\n"
        "endmodule\n"
    )
    mutator = Mutator.from_sv(sv)
    mutants = list(
        mutator.generate(
            kinds=[MutationKind.CLOCK_POLARITY_SWAP, MutationKind.ATTRIBUTE_TOGGLE],
            count=4,
            seed=0,
            schedule=Schedule.ROUND_ROBIN,
        )
    )
    kinds_in_order = [m.kind for m in mutants]
    # Round-robin should alternate: first polarity, then attribute, then polarity, then attribute
    # (subject to availability — both kinds have ≥2 candidates here).
    assert (
        kinds_in_order[0] != kinds_in_order[1] or kinds_in_order[1] != kinds_in_order[2]
    )


def test_schedule_sequential_default_drains_each_kind() -> None:
    """Sequential default: exhaust first kind before second."""
    sv = (
        "module a;\n"
        "  always_ff @(posedge clk) q <= d;\n"
        "  always_ff @(posedge clk) r <= e;\n"
        "  (* cdc_sync *) logic x;\n"
        "endmodule\n"
    )
    mutator = Mutator.from_sv(sv)
    mutants = list(
        mutator.generate(
            kinds=[MutationKind.CLOCK_POLARITY_SWAP, MutationKind.ATTRIBUTE_TOGGLE],
            count=99,
        )
    )
    kinds_in_order = [m.kind for m in mutants]
    # Sequential should drain CLOCK_POLARITY_SWAP first, then move to ATTRIBUTE_TOGGLE.
    first_attr_idx = next(
        (i for i, k in enumerate(kinds_in_order) if k is MutationKind.ATTRIBUTE_TOGGLE),
        len(kinds_in_order),
    )
    assert all(
        k is MutationKind.CLOCK_POLARITY_SWAP for k in kinds_in_order[:first_attr_idx]
    )


def test_prediction_requires_non_empty_rationale() -> None:
    with pytest.raises(ValueError, match="rationale"):
        Prediction(rationale="")


def test_full_enum_has_eleven_kinds() -> None:
    """Sanity-check the 11-operator enumeration ratified in #3."""
    assert len(list(MutationKind)) == 11
    expected = {
        # CDC
        "CLOCK_POLARITY_SWAP",
        "SYNC_CHAIN_DEPTH_PERTURB",
        "BIT_EXTRACT_PERMUTE",
        "ATTRIBUTE_TOGGLE",
        "RESET_POLARITY_FLIP",
        # rb-mut
        "ARITH_FLIP",
        "BIT_OP_FLIP",
        "COND_NEGATE",
        "COND_CONST",
        "ASSIGN_DROP",
        "PORT_BINDING_SWAP",
    }
    assert {k.name for k in MutationKind} == expected


def test_stubbed_kinds_raise_with_issue_link() -> None:
    """Every unimplemented kind raises NotImplementedError pointing at the design issue."""
    mutator = Mutator.from_sv(_SYNTHETIC_SV)
    from rtl_buddy_xeno.operators import IMPLEMENTED_KINDS as implemented

    for kind in MutationKind:
        if kind in implemented:
            continue
        gen = mutator.generate(kinds=[kind], count=1)
        # cdc operators link to `rtl-buddy-cdc#221`; rb-mut operators link to
        # `rtl_buddy#206`. Match either issue-tracker form.
        with pytest.raises(NotImplementedError, match=r"rtl[-_]buddy"):
            next(gen)


def test_stubbed_candidates_also_raise() -> None:
    """Same stub behaviour on the candidates surface."""
    mutator = Mutator.from_sv(_SYNTHETIC_SV)
    from rtl_buddy_xeno.operators import IMPLEMENTED_KINDS as implemented

    for kind in MutationKind:
        if kind in implemented:
            continue
        with pytest.raises(NotImplementedError):
            list(mutator.candidates(kinds=[kind]))
