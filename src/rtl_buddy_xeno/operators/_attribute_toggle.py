"""``ATTRIBUTE_TOGGLE`` — strip SV attribute blocks ``(* attr *)``.

Parser-free operator (no ``[verible]`` or ``[slang]`` required). The
design assumption in #4 was that Verible's CST would expose
``(* attr *)`` as a discrete node, but Verible's ``--export_json``
schema consumes attributes during parsing and does **not** emit them
as CST nodes (verified against ``verible-verilog-syntax v0.0-3946``).
A regex-based scan with string/comment awareness is the practical
substitute: SV attribute syntax is regular enough that a careful
scanner produces no false positives in real RTL.

This operator therefore ships in the no-extras install path alongside
``CLOCK_POLARITY_SWAP``. The no-straddle rule from #4 still holds:
the scanner is exactly one path (regex with skip-zones), never a
mix of regex and CST within one operator.

Mutation shape: strip the entire ``(* attr *)`` block (plus one
trailing space if it leaves a stray double-space). Predictions are
attribute-name aware — see ``_ATTR_PREDICTIONS``.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator

from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site

# Attribute name → predicted CDC rule(s) that should fire when stripped.
# Mapping verified against rtl-buddy-cdc's rule pack in #221 (Stage 3 Layer B
# fuzz integration); the ``glitchless_clock_mux`` row was previously
# CDC-008 ("clock pin driven by data") — the right rule for an unmarked
# async clock-mux is CDC-010 ("async clock mux select"). See
# rtl-buddy-cdc/src/rtl_buddy_cdc/rules.py ``check_cdc_010``.
_ATTR_PREDICTIONS: dict[str, frozenset[str]] = {
    "cdc_sync": frozenset({"CDC-002", "CDC-003"}),
    "glitchless_clock_mux": frozenset({"CDC-010"}),
    "cdc_gray": frozenset({"CDC-019"}),
    "reset_sync": frozenset({"RDC-001"}),
    "reset_polarity": frozenset({"RDC-001", "RDC-007"}),
}

_ATTR_NAME_RE = re.compile(r"\(\*\s*([A-Za-z_][A-Za-z0-9_]*)")


def _find_attribute_sites(sv: str) -> list[tuple[int, int, str]]:
    """Return ``(start_byte, end_byte, attr_name)`` for every ``(* … *)``.

    Scans the SV source as bytes, skipping line comments
    (``// ...``), block comments (``/* ... */``), and string literals
    (``"..."`` with backslash-escape handling). Inside legal positions
    we look for ``(*`` … ``*)`` (the attribute syntax) and capture the
    first identifier inside it as the attribute name.
    """
    data = sv.encode("utf-8")
    sites: list[tuple[int, int, str]] = []
    i = 0
    n = len(data)
    while i < n:
        b = data[i : i + 1]
        # Line comment — skip to end of line.
        if b == b"/" and data[i : i + 2] == b"//":
            nl = data.find(b"\n", i + 2)
            i = n if nl < 0 else nl + 1
            continue
        # Block comment — skip to closing */
        if b == b"/" and data[i : i + 2] == b"/*":
            end = data.find(b"*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        # String literal — skip with backslash-escape handling.
        if b == b'"':
            j = i + 1
            while j < n:
                cb = data[j : j + 1]
                if cb == b"\\" and j + 1 < n:
                    j += 2
                    continue
                if cb == b'"':
                    j += 1
                    break
                j += 1
            i = j
            continue
        # Attribute block — must be `(*` *not* immediately followed by
        # another `*` (which would be `(**`, not standard SV).
        if b == b"(" and data[i : i + 2] == b"(*" and data[i : i + 3] != b"(**":
            end = data.find(b"*)", i + 2)
            if end < 0:
                break
            start = i
            end_byte = end + 2
            slice_text = data[start:end_byte].decode("utf-8", errors="replace")
            match = _ATTR_NAME_RE.search(slice_text)
            if match:
                sites.append((start, end_byte, match.group(1)))
            i = end_byte
            continue
        i += 1
    return sites


def _byte_to_line_col(sv: str, byte_offset: int) -> tuple[int, int]:
    head = sv.encode("utf-8")[:byte_offset]
    line = head.count(b"\n") + 1
    last_newline = head.rfind(b"\n")
    column = (byte_offset - last_newline) if last_newline >= 0 else byte_offset + 1
    return line, column


def _predict_strip(attr_name: str, line: int) -> Prediction:
    cdc_rules = _ATTR_PREDICTIONS.get(attr_name, frozenset())
    if cdc_rules:
        rationale = (
            f"stripping `(* {attr_name} *)` at line {line} removes the "
            f"attribute that gates {' / '.join(sorted(cdc_rules))}; "
            "those rules should now fire on the surrounding construct"
        )
    else:
        rationale = (
            f"stripping `(* {attr_name} *)` at line {line}; attribute "
            "is not in xeno's known prediction table, so no specific "
            "CDC-rule firing is asserted — exploratory candidate"
        )
    return Prediction(rationale=rationale, cdc_rules_added=cdc_rules)


def _strip_byte_range(sv: str, start: int, end: int) -> str:
    """Strip ``sv[start:end]`` (byte-offsets, UTF-8) plus one trailing space."""
    data = sv.encode("utf-8")
    cut_end = end
    if cut_end < len(data) and data[cut_end : cut_end + 1] == b" ":
        cut_end += 1
    mutated_bytes = data[:start] + data[cut_end:]
    return mutated_bytes.decode("utf-8")


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_attribute_sites(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        start, end, attr_name = sites[idx]
        line, _ = _byte_to_line_col(sv, start)
        mutated = _strip_byte_range(sv, start, end)
        yield Mutant(
            sv=mutated,
            diff_summary=f"line {line}: strip (* {attr_name} *)",
            seed=start,
            prediction=_predict_strip(attr_name, line),
            kind=MutationKind.ATTRIBUTE_TOGGLE,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for start, _end, attr_name in _find_attribute_sites(sv):
        line, column = _byte_to_line_col(sv, start)
        yield Site(
            kind=MutationKind.ATTRIBUTE_TOGGLE,
            line=line,
            column=column,
            snippet=f"(* {attr_name} *)",
            prediction=_predict_strip(attr_name, line),
        )


operator = _mutants
candidates = _candidates
