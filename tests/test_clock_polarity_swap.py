"""Coverage for the clock_polarity_swap operator."""

from __future__ import annotations

import pytest

from rtl_buddy_xeno import ExpectedChange, Mutant, MutationKind, Mutator

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
    # Same set of SVs, possibly different order — at least one of the seed
    # pairs in the synthetic corpus must reorder, otherwise the shuffle is
    # a no-op and the determinism guarantee is hollow.
    assert {m.sv for m in a} == {m.sv for m in b}
    # Sweep a few seeds to find a reordering. With 3! = 6 permutations and
    # 16 seeds, missing all six is vanishingly unlikely if the RNG works.
    orderings = {
        tuple(m.diff_summary for m in _mutants(_SYNTHETIC_SV, seed=s))
        for s in range(16)
    }
    assert len(orderings) > 1


def test_expected_change_declares_cdc006() -> None:
    [first, *_] = _mutants(_SYNTHETIC_SV, count=1)
    assert isinstance(first.expected_change, ExpectedChange)
    assert "CDC-006" in first.expected_change.rules_added
    assert first.expected_change.rationale  # non-empty rationale required


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


def test_stubbed_kinds_raise_with_issue_link() -> None:
    mutator = Mutator.from_sv(_SYNTHETIC_SV)
    for kind in MutationKind:
        if kind is MutationKind.CLOCK_POLARITY_SWAP:
            continue
        gen = mutator.generate(kinds=[kind], count=1)
        with pytest.raises(NotImplementedError, match="rtl-buddy-cdc#221"):
            next(gen)
