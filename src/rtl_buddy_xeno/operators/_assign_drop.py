"""``ASSIGN_DROP`` — drop a non-blocking assignment statement.

Verible+slang operator. Verible's CST finds candidate sites
(``kNonblockingAssignmentStatement`` subtrees, which span the LHS,
``<=``, RHS, and terminating ``;``); pyslang elaborates the design to
confirm the SV is valid and to extract the LHS signal name for
:attr:`Prediction.perturbs_signals`.

Scope (first cut): non-blocking assignments only (``<=``). Non-blocking
in SV semantics is the register-driving idiom, so every site we find
is unambiguously state-bearing — :attr:`Prediction.perturbs_liveness`
is always ``True``. Blocking assignments (``=``) require CST-level
context to classify (``always_comb`` ⇒ combinational, ``always_ff`` ⇒
state-bearing) and are deferred. See umbrella #2's operator table.

Mutation shape: drop the entire statement byte-range. The register
keeps its previous value indefinitely (synthesised to no-driver in
elaboration); any property constraining the signal or asserting
liveness should kill the mutant.
"""

from __future__ import annotations

import hashlib
import random
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno import slang as _slang
from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site

_NB_ASSIGN_TAG = "kNonblockingAssignmentStatement"

# Match the LHS identifier at the start of the assignment slice. The
# identifier may be hierarchical (``a.b.c``) or bit-selected
# (``a[7:0]``); we strip indices for the perturbs_signals report.
_LHS_NAME_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_\.]*)")


def _sv_to_tempfile(sv: str) -> Path:
    digest = hashlib.sha256(sv.encode("utf-8")).hexdigest()[:16]
    tmpdir = Path(tempfile.gettempdir()) / "rtl-buddy-xeno"
    tmpdir.mkdir(parents=True, exist_ok=True)
    target = tmpdir / f"sv-{digest}.sv"
    if not target.exists() or target.read_text() != sv:
        target.write_text(sv)
    return target


def _byte_to_line_col(sv: str, byte_offset: int) -> tuple[int, int]:
    head = sv.encode("utf-8")[:byte_offset]
    line = head.count(b"\n") + 1
    last_newline = head.rfind(b"\n")
    column = (byte_offset - last_newline) if last_newline >= 0 else byte_offset + 1
    return line, column


def _validate_elaborates(sv: str) -> bool:
    """Return ``True`` if pyslang accepts the SV as syntactically valid.

    A ``False`` return means pyslang isn't installed (operator should
    still proceed under the textual interpretation) or the SV fails
    elaboration. We treat both as "fall back to Verible-only
    information"; non-blocking assigns are state-bearing regardless of
    elaboration success.
    """
    if not _slang.is_available():
        return False
    try:
        _slang.elaborate_text(sv)
    except Exception:  # pragma: no cover - elaboration error catch-all
        return False
    return True


def _find_assign_sites(sv: str) -> list[tuple[int, int, str]]:
    """Return ``(start_byte, end_byte, lhs_name)`` for every ``<=`` site."""
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for subtree in _cst.walk_subtrees(cst_root, _NB_ASSIGN_TAG):
        try:
            start, end = _cst.node_span(subtree)
        except ValueError:
            continue
        if (start, end) in seen:
            continue
        seen.add((start, end))
        slice_text = sv.encode("utf-8")[start:end].decode("utf-8", errors="replace")
        match = _LHS_NAME_RE.match(slice_text)
        lhs_name = match.group(1) if match else "<unknown>"
        # Extend end to include the terminating semicolon if Verible's
        # span stops just before it (some CST shapes don't include `;`).
        sv_bytes = sv.encode("utf-8")
        cursor = end
        while cursor < len(sv_bytes) and sv_bytes[cursor : cursor + 1] in (b" ", b"\t"):
            cursor += 1
        if cursor < len(sv_bytes) and sv_bytes[cursor : cursor + 1] == b";":
            end = cursor + 1
        sites.append((start, end, lhs_name))
    sites.sort()
    return sites


def _predict_drop(lhs_name: str, line: int, slang_validated: bool) -> Prediction:
    confidence = "pyslang-elaborated" if slang_validated else "CST-only"
    return Prediction(
        rationale=(
            f"dropped non-blocking assign to `{lhs_name}` at line {line} "
            f"({confidence}); the register stops updating, so any property "
            "constraining the signal value or asserting liveness on its "
            "downstream cone should kill this mutant"
        ),
        perturbs_signals=frozenset({lhs_name}),
        perturbs_liveness=True,
    )


def _drop_byte_range(sv: str, start: int, end: int) -> str:
    """Replace the byte range with an empty statement ``;``.

    Replacing rather than deleting keeps the enclosing ``always_ff``
    / ``always_comb`` block syntactically well-formed. An empty
    statement is the SV equivalent of "do nothing this cycle" — the
    register stops being driven, which is exactly the semantic we
    want for ASSIGN_DROP (state-bearing register loses its driver).
    """
    data = sv.encode("utf-8")
    mutated_bytes = data[:start] + b";" + data[end:]
    return mutated_bytes.decode("utf-8")


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_assign_sites(sv)
    if not sites:
        return
    slang_validated = _validate_elaborates(sv)
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        start, end, lhs_name = sites[idx]
        line, _ = _byte_to_line_col(sv, start)
        mutated = _drop_byte_range(sv, start, end)
        yield Mutant(
            sv=mutated,
            diff_summary=f"line {line}: drop `{lhs_name} <= ...;`",
            seed=start,
            prediction=_predict_drop(lhs_name, line, slang_validated),
            kind=MutationKind.ASSIGN_DROP,
        )


def _candidates(sv: str) -> Iterator[Site]:
    slang_validated = _validate_elaborates(sv)
    for start, _end, lhs_name in _find_assign_sites(sv):
        line, column = _byte_to_line_col(sv, start)
        yield Site(
            kind=MutationKind.ASSIGN_DROP,
            line=line,
            column=column,
            snippet=f"{lhs_name} <=",
            prediction=_predict_drop(lhs_name, line, slang_validated),
        )


operator = _mutants
candidates = _candidates
