"""Mutant validity smoke test — every mutant must parse with Verible.

For each implemented operator, generate every available mutant against
a corresponding fixture and pipe each one through
``verible-verilog-syntax``. A mutant that doesn't parse is either:

- A bug in the operator (wrong byte-range computed, off-by-one in the
  splice, eats too much surrounding context, etc.) — the test fails
  and the operator PR doesn't merge.
- A construction-safety claim that turned out to be false — the test
  fails and the design is revisited.

The three operators landed today (CLOCK_POLARITY_SWAP, ATTRIBUTE_TOGGLE,
ASSIGN_DROP) are construction-safe by design (see xeno#6). This test
exists to enforce the same property as additional operators land —
particularly the high-risk ones (BIT_EXTRACT_PERMUTE,
SYNC_CHAIN_DEPTH_PERTURB, RESET_POLARITY_FLIP) where construction-
safety is not a defensible claim.

When a new operator lands, its PR must add a fixture entry to
``_FIXTURES`` below so the validity test exercises it. The CI
workflow installs Verible (see ``.github/workflows/test.yml``) so
this gate fires on every PR.

The test skips cleanly when:
- ``verible-verilog-syntax`` isn't on PATH (local dev without Verible).
- ``rtl_buddy_view`` isn't installed (no ``[verible]`` extra; some
  operators still need it via xeno's ``cst.py`` facade).

Optional second gate: pyslang elaboration. When the ``[slang]`` extra
is installed, every mutant is also fed through pyslang and asserted
to elaborate without diagnostics worse than ``Note``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rtl_buddy_xeno import Mutator, MutationKind
from rtl_buddy_xeno import slang as _slang
from rtl_buddy_xeno.operators import IMPLEMENTED_KINDS

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Fixture per implemented operator. When a new operator lands, its PR
# adds an entry here pointing at a fixture file that exercises ≥3
# candidate sites for that operator.
_FIXTURES: dict[MutationKind, Path] = {
    MutationKind.CLOCK_POLARITY_SWAP: _FIXTURES_DIR / "sync_chain.sv",
    MutationKind.ATTRIBUTE_TOGGLE: _FIXTURES_DIR / "attr_sweep.sv",
    MutationKind.ASSIGN_DROP: _FIXTURES_DIR / "sync_chain.sv",
    MutationKind.ARITH_FLIP: _FIXTURES_DIR / "expressions.sv",
    MutationKind.BIT_OP_FLIP: _FIXTURES_DIR / "expressions.sv",
    MutationKind.COND_NEGATE: _FIXTURES_DIR / "expressions.sv",
    MutationKind.COND_CONST: _FIXTURES_DIR / "expressions.sv",
}


def _verible_available() -> bool:
    return shutil.which("verible-verilog-syntax") is not None


def test_every_implemented_operator_has_a_validity_fixture() -> None:
    """Coverage invariant: an implemented operator without a fixture here
    means PR review skipped this checklist item. Fail loudly so it can't
    silently slip through.
    """
    missing = sorted(kind.value for kind in IMPLEMENTED_KINDS if kind not in _FIXTURES)
    assert missing == [], (
        f"IMPLEMENTED_KINDS contains operators without a validity fixture: "
        f"{missing}. Add an entry to _FIXTURES in this file or to "
        f"tests/fixtures/ so the validity smoke test covers them."
    )


@pytest.mark.skipif(
    not _verible_available(),
    reason="verible-verilog-syntax not on PATH; cannot validate mutants",
)
@pytest.mark.parametrize("kind", sorted(IMPLEMENTED_KINDS, key=lambda k: k.value))
def test_every_mutant_parses_with_verible(kind: MutationKind, tmp_path: Path) -> None:
    """Every emitted mutant for ``kind`` parses successfully with Verible.

    The mutator's job is to emit *parseable* SV that the downstream
    consumer's oracle can run against. A mutant that doesn't parse is
    not a valid mutation candidate — the operator has a bug.
    """
    if kind not in _FIXTURES:
        pytest.skip(f"no fixture registered for {kind.value}")
    if kind is MutationKind.ASSIGN_DROP:
        # ASSIGN_DROP needs the [verible] extra (uses cst.py facade for
        # the CST walk). Skip if rtl-buddy-view isn't installed.
        pytest.importorskip("rtl_buddy_view")

    fixture = _FIXTURES[kind]
    mutator = Mutator.from_sv(fixture)
    mutants = list(mutator.generate(kinds=[kind], count=999, seed=0))
    assert mutants, f"{kind.value}: fixture {fixture.name} produced zero mutants"

    failures: list[str] = []
    for i, mutant in enumerate(mutants):
        target = tmp_path / f"{kind.value}_{i}.sv"
        target.write_text(mutant.sv)
        proc = subprocess.run(
            ["verible-verilog-syntax", str(target)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            failures.append(
                f"  mutant #{i} ({mutant.diff_summary}):\n"
                f"    stderr: {proc.stderr.strip()}\n"
                f"    --- mutant SV ---\n{mutant.sv}\n"
                f"    --- end ---"
            )
    assert not failures, (
        f"{kind.value}: {len(failures)}/{len(mutants)} mutants failed "
        f"to parse with Verible:\n" + "\n".join(failures)
    )


@pytest.mark.skipif(
    not _slang.is_available(),
    reason="pyslang not installed; cannot validate elaboration",
)
@pytest.mark.parametrize("kind", sorted(IMPLEMENTED_KINDS, key=lambda k: k.value))
def test_every_mutant_elaborates_with_pyslang(
    kind: MutationKind,
) -> None:
    """Every emitted mutant for ``kind`` elaborates without errors via pyslang.

    Weaker than the Verible gate (pyslang's diagnostic surface is
    richer than parse/fail), so this only fails on diagnostics with
    severity ``Error`` or ``Fatal``. ``Note`` and ``Warning`` are
    expected (synthesisable-SV subset, missing ports in fixtures, etc.)
    and don't fail this test.
    """
    if kind not in _FIXTURES:
        pytest.skip(f"no fixture registered for {kind.value}")
    if kind in {MutationKind.ATTRIBUTE_TOGGLE, MutationKind.ASSIGN_DROP}:
        # These operators need rtl-buddy-view for the cst.py facade.
        # ATTRIBUTE_TOGGLE uses cst.py for its scanner today via the
        # facade import chain; ASSIGN_DROP uses Verible directly.
        # Skip if the extra is missing — pyslang alone isn't enough.
        pytest.importorskip("rtl_buddy_view")

    fixture = _FIXTURES[kind]
    mutator = Mutator.from_sv(fixture)
    mutants = list(mutator.generate(kinds=[kind], count=999, seed=0))
    assert mutants, f"{kind.value}: fixture {fixture.name} produced zero mutants"

    import pyslang  # type: ignore[import-not-found]

    failures: list[str] = []
    for i, mutant in enumerate(mutants):
        try:
            compilation = _slang.elaborate_text(mutant.sv)
            diagnostics = compilation.getAllDiagnostics()
        except Exception as exc:  # pragma: no cover - pathological mutant
            failures.append(
                f"  mutant #{i} ({mutant.diff_summary}): pyslang raised "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        bad = [
            d
            for d in diagnostics
            if d.severity
            in (pyslang.DiagnosticSeverity.Error, pyslang.DiagnosticSeverity.Fatal)
        ]
        if bad:
            failures.append(
                f"  mutant #{i} ({mutant.diff_summary}): "
                f"{len(bad)} error-or-worse diagnostic(s):\n"
                + "\n".join(f"    {d}" for d in bad)
            )
    assert not failures, (
        f"{kind.value}: {len(failures)}/{len(mutants)} mutants failed "
        f"pyslang elaboration:\n" + "\n".join(failures)
    )
