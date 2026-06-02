"""Guard: every CDC/RDC rule-id an operator predicts must name a real
rtl-buddy-cdc rule.

xeno predicts rtl-buddy-cdc rule-ids (``CDC-xxx`` / ``RDC-xxx``) as
plain string literals in ``Prediction.cdc_rules_added`` /
``cdc_rules_removed``. There is **no code dependency** on
rtl-buddy-cdc, so a typo — or a stale id after rtl-buddy-cdc renumbers
(the class of bug #12 fixed by hand: ``CDC-006 -> CDC-016``,
``CDC-008 -> CDC-010``) — would otherwise go unnoticed until a
consumer's oracle mysteriously never matches. This test pins the
contract.

``RB_CDC_RULE_IDS`` is a *snapshot* of rtl-buddy-cdc's catalog, not a
live query: xeno deliberately has no rtl-buddy-cdc dependency, and CI
must not fetch a sibling repo at test time. Captured from
rtl-buddy/rtl-buddy-cdc ``origin/main`` (commit ``d28772f``,
2026-06-02): ``src/rtl_buddy_cdc/rules.py`` defines ``CDC-001..021``
and ``RDC-001..008``. **Re-sync this set by hand whenever
rtl-buddy-cdc adds or renumbers rules.**

Limitation: the snapshot is a *superset* anchor, so this test catches
xeno predicting a never-existed / typo'd id, but it cannot by itself
detect rtl-buddy-cdc *removing* an id xeno still predicts — that
surfaces only when the snapshot is refreshed. The point is a tripwire
for the next #12, not a live cross-repo diff.
"""

from __future__ import annotations

import re
from pathlib import Path

import rtl_buddy_xeno.operators as _operators_pkg
from rtl_buddy_xeno import Mutator, MutationKind
from rtl_buddy_xeno.operators._attribute_toggle import _ATTR_PREDICTIONS

# Snapshot of rtl-buddy-cdc's rule catalog — see module docstring.
RB_CDC_RULE_IDS: frozenset[str] = frozenset(
    {f"CDC-{n:03d}" for n in range(1, 22)} | {f"RDC-{n:03d}" for n in range(1, 9)}
)

_RULE_ID_RE = re.compile(r"\b(?:CDC|RDC)-\d{3}\b")

# A 2-stage sync chain on one clock. CLOCK_POLARITY_SWAP's chain-stage
# heuristic (>=2 polarity-edge sites for the same clock) fires, so the
# operator makes its positive CDC-016 claim.
_CHAIN_SV = (
    "module m(input logic clk_dst, input logic async_in, output logic q);\n"
    "  logic sync1, sync2;\n"
    "  always_ff @(posedge clk_dst) sync1 <= async_in;\n"
    "  always_ff @(posedge clk_dst) sync2 <= sync1;\n"
    "  assign q = sync2;\n"
    "endmodule\n"
)

# One of every attribute in xeno's prediction table, so ATTRIBUTE_TOGGLE
# emits the full _ATTR_PREDICTIONS rule-id set.
_ATTR_SV = (
    "(* cdc_sync *) logic a;\n"
    "(* cdc_gray *) logic [3:0] b;\n"
    "(* glitchless_clock_mux *) logic c;\n"
    "(* reset_sync *) logic d;\n"
    "(* reset_polarity *) logic e;\n"
)


def _emitted_rule_ids(sv: str, kind: MutationKind) -> set[str]:
    """Union of ``cdc_rules_added | cdc_rules_removed`` over every mutant
    *and* every candidate site the operator produces for ``sv``."""
    ids: set[str] = set()
    for mutant in Mutator.from_sv(sv).generate(kinds=[kind], count=99, seed=0):
        ids |= set(mutant.prediction.cdc_rules_added)
        ids |= set(mutant.prediction.cdc_rules_removed)
    for site in Mutator.from_sv(sv).candidates(kinds=[kind]):
        ids |= set(site.prediction.cdc_rules_added)
        ids |= set(site.prediction.cdc_rules_removed)
    return ids


def test_attr_predictions_table_within_catalog() -> None:
    """Layer 1 — the one structured prediction table (``_ATTR_PREDICTIONS``)."""
    table_ids: set[str] = set()
    for rules in _ATTR_PREDICTIONS.values():
        table_ids |= set(rules)
    extra = table_ids - RB_CDC_RULE_IDS
    assert not extra, (
        f"_ATTR_PREDICTIONS references ids absent from rtl-buddy-cdc: {sorted(extra)}"
    )


def test_attribute_toggle_emits_only_catalog_ids() -> None:
    """Layer 2a — dynamic, no-extras regex operator."""
    emitted = _emitted_rule_ids(_ATTR_SV, MutationKind.ATTRIBUTE_TOGGLE)
    # Sanity: the fixture really did exercise predictions.
    assert {"CDC-002", "CDC-010", "RDC-007"} <= emitted
    extra = emitted - RB_CDC_RULE_IDS
    assert not extra, (
        f"ATTRIBUTE_TOGGLE emitted ids absent from rtl-buddy-cdc: {sorted(extra)}"
    )


def test_clock_polarity_swap_emits_only_catalog_ids() -> None:
    """Layer 2b — dynamic; the 2-stage chain triggers the CDC-016 claim."""
    emitted = _emitted_rule_ids(_CHAIN_SV, MutationKind.CLOCK_POLARITY_SWAP)
    assert "CDC-016" in emitted  # heuristic fired
    extra = emitted - RB_CDC_RULE_IDS
    assert not extra, (
        f"CLOCK_POLARITY_SWAP emitted ids absent from rtl-buddy-cdc: {sorted(extra)}"
    )


def test_no_operator_source_cites_a_noncatalog_rule_id() -> None:
    """Layer 3 — source-scan safety net.

    Catches a rule-id literal hidden behind a Verible gate the no-extras
    dynamic tests can't reach. Deliberately broad: it scans every
    ``CDC-###`` / ``RDC-###`` literal in the operator modules, including
    those in docstrings/comments — even a cited rule-id should name a
    real rtl-buddy-cdc rule.
    """
    ops_dir = Path(_operators_pkg.__file__).parent
    offenders: dict[str, list[str]] = {}
    for path in sorted(ops_dir.glob("_*.py")):
        found = set(_RULE_ID_RE.findall(path.read_text(encoding="utf-8")))
        bad = found - RB_CDC_RULE_IDS
        if bad:
            offenders[path.name] = sorted(bad)
    assert not offenders, (
        "operator sources cite rule-ids absent from rtl-buddy-cdc's snapshot "
        f"(refresh RB_CDC_RULE_IDS if rtl-buddy-cdc changed): {offenders}"
    )
