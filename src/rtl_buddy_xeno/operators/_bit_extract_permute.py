"""``BIT_EXTRACT_PERMUTE`` — narrow a bit-select range by 1.

Verible-only operator. Walks ``kDimensionRange`` nodes whose bounds
are integer literals, and emits a mutant per "narrow by 1" operation
that keeps the range valid (LSB ≤ MSB):

- ``bus[N:M]`` → ``bus[N-1:M]`` (drop MSB; new range still valid if
  N-1 ≥ M).
- ``bus[N:M]`` → ``bus[N:M+1]`` (drop LSB; new range still valid if
  M+1 ≤ N).

The resulting slice is one bit narrower than the original. Width
mismatches against the LHS in an assignment context produce pyslang
Warnings (implicit zero-extend), not Errors — the validity gate
accepts them. CDC-019 / CDC-020 (sliced-bus reconvergence) is the
target oracle: the consumer's analyzer should detect the changed
slice composition.

Single-bit selects (``bus[N]``) are not perturbed — they have no
range to narrow without becoming an empty slice. Bounds that aren't
plain integer literals (e.g. ``bus[WIDTH-1:0]``) are also skipped;
the operator would need pyslang to evaluate the bound expressions
safely, and the [slang]-gated upgrade path is a follow-up.
"""

from __future__ import annotations

import hashlib
import random
import tempfile
from collections.abc import Iterator
from pathlib import Path

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site


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


def _literal_bound(expression_node: dict) -> tuple[int, int, int] | None:
    """If ``expression_node`` wraps a single integer literal, return
    ``(value, start_byte, end_byte)``.

    Verible CST for ``[3:0]`` puts each bound under a ``kExpression`` →
    ``kNumber`` → ``TK_DecNumber`` leaf. We recurse to find the leaf
    and confirm its text parses as an int.
    """
    if not isinstance(expression_node, dict):
        return None
    if expression_node.get("tag") == "TK_DecNumber":
        text = expression_node.get("text", "")
        if text and text.isdigit():
            return (
                int(text),
                int(expression_node["start"]),
                int(expression_node["end"]),
            )
    for child in expression_node.get("children", []) or []:
        result = _literal_bound(child)
        if result is not None:
            return result
    return None


def _find_sites(sv: str) -> list[tuple[int, int, str, str, str, int]]:
    """Return ``(start, end, original_range_text, replacement, mode, line_anchor)`` per site.

    Two mutation modes per source range with integer-literal bounds:
    "drop_msb" (decrement the LHS bound) and "drop_lsb" (increment
    the RHS bound). Each yields one mutant.
    """
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str, str, str, int]] = []
    seen: set[tuple[int, int]] = set()
    for dim_range in _cst.walk_subtrees(cst_root, "kDimensionRange"):
        children = [
            c for c in (dim_range.get("children", []) or []) if isinstance(c, dict)
        ]
        # Expected layout: ['[', expr_msb, ':', expr_lsb, ']'].
        if len(children) < 5:
            continue
        msb_bound = _literal_bound(children[1])
        lsb_bound = _literal_bound(children[3])
        if msb_bound is None or lsb_bound is None:
            continue
        msb_val, msb_start, msb_end = msb_bound
        lsb_val, lsb_start, lsb_end = lsb_bound
        if msb_val <= lsb_val:
            # Already minimum width (or ascending range); skip.
            continue
        full_range_text = sv.encode("utf-8")[msb_start - 1 : lsb_end + 1].decode(
            "utf-8", errors="replace"
        )
        if (msb_start, lsb_end) in seen:
            continue
        seen.add((msb_start, lsb_end))
        # Mode 1: drop_msb — replace MSB literal with MSB - 1.
        new_msb = str(msb_val - 1)
        sites.append(
            (
                msb_start,
                msb_end,
                str(msb_val),
                new_msb,
                "drop_msb",
                msb_start,
            )
        )
        # Mode 2: drop_lsb — replace LSB literal with LSB + 1.
        new_lsb = str(lsb_val + 1)
        sites.append(
            (
                lsb_start,
                lsb_end,
                str(lsb_val),
                new_lsb,
                "drop_lsb",
                msb_start,
            )
        )
        # full_range_text is for the rationale message only.
        _ = full_range_text
    # Sort by the source-order anchor (start of the MSB), then by mode
    # so the two mutants for a single range appear together.
    sites.sort(key=lambda s: (s[5], s[4]))
    return sites


def _splice(sv: str, start: int, end: int, replacement: str) -> str:
    data = sv.encode("utf-8")
    return (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")


def _predict(original: str, replacement: str, mode: str, line: int) -> Prediction:
    """Conservative prediction.

    The operator narrows a bit-select range by one bit. Whether
    CDC-019 (independently-synced one-hot decode) or CDC-020
    (sliced-bus reconvergence) actually fire on the mutated source
    depends on the surrounding *crossing* structure (per-lane src
    flops on a common comb driver, per-lane synchronisers in the
    dst domain) — none of which the Verible CST recogniser walks
    when looking for bit-select sites. Predicting CDC-019/CDC-020
    unconditionally over-claimed on the majority of corpus parents
    (where the narrowed slice doesn't recompose at a sync chain),
    so this prediction now records intent in the rationale without
    making a positive CDC-rule claim. Coverage report tracks the
    actual fires on the mutated source.
    """
    side = "MSB" if mode == "drop_msb" else "LSB"
    return Prediction(
        rationale=(
            f"narrowed bit-select range by dropping the {side} (`{original}` → "
            f"`{replacement}`) at line {line}; the slice's composition changes. "
            "CDC-019 / CDC-020 (sliced-bus reconvergence) fire when the "
            "narrowed slice recomposes at a sync chain — the operator "
            "doesn't verify that surrounding structure, so cdc_rules_added "
            "stays empty and the downstream coverage report measures the "
            "actual rule fires on the mutated source"
        ),
        perturbs_liveness=False,
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_sites(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        start, end, original, replacement, mode, _anchor = sites[idx]
        line, _ = _byte_to_line_col(sv, start)
        yield Mutant(
            sv=_splice(sv, start, end, replacement),
            diff_summary=(f"line {line}: {mode} (`{original}` -> `{replacement}`)"),
            seed=start * 10 + (0 if mode == "drop_msb" else 1),
            prediction=_predict(original, replacement, mode, line),
            kind=MutationKind.BIT_EXTRACT_PERMUTE,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for start, _end, original, replacement, mode, _anchor in _find_sites(sv):
        line, column = _byte_to_line_col(sv, start)
        yield Site(
            kind=MutationKind.BIT_EXTRACT_PERMUTE,
            line=line,
            column=column,
            snippet=f"[{mode}: {original} -> {replacement}]",
            prediction=_predict(original, replacement, mode, line),
        )


operator = _mutants
candidates = _candidates
