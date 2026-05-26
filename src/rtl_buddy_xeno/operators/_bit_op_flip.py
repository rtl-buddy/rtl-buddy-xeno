"""``BIT_OP_FLIP`` — flip bitwise operators (``&`` ↔ ``|``, ``~`` removal).

Verible-only operator. Two mutation kinds, both byte-splice on a
Verible-identified operator leaf:

1. **Binary swap**: ``kBinaryExpression`` nodes whose operator is
   ``&`` or ``|`` get their operator swapped.
2. **Unary strip**: ``kUnaryPrefixExpression`` nodes whose operator is
   ``~`` get the operator removed (the operand survives unchanged).

``~`` *insertion* (wrapping a bare expression in ``~(…)``) is out of
scope for first cut — it requires deciding both where to insert and
whether parens are needed at the splice site. The current two-mode
flip is enough for rb-mut's "weakness gauge" use case.
"""

from __future__ import annotations

import hashlib
import random
import tempfile
from collections.abc import Iterator
from pathlib import Path

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site

_BINARY_SWAPS: dict[str, str] = {"&": "|", "|": "&"}


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


def _find_sites(sv: str) -> list[tuple[int, int, str, str, str]]:
    """Return ``(start, end, original, replacement, mode)`` per candidate.

    ``mode`` is one of ``"binary"`` (operator swap on a kBinaryExpression)
    or ``"unary_strip"`` (remove the ``~`` prefix from a
    kUnaryPrefixExpression). The mode is carried so the rationale can
    name what kind of mutation occurred.
    """
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str, str, str]] = []

    for subtree in _cst.walk_subtrees(cst_root, "kBinaryExpression"):
        for child in subtree.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            tag = child.get("tag")
            if tag not in _BINARY_SWAPS:
                continue
            if "start" not in child or "end" not in child:
                continue
            sites.append(
                (
                    int(child["start"]),
                    int(child["end"]),
                    tag,
                    _BINARY_SWAPS[tag],
                    "binary",
                )
            )

    for subtree in _cst.walk_subtrees(cst_root, "kUnaryPrefixExpression"):
        children = subtree.get("children", []) or []
        if not children:
            continue
        op_leaf = children[0] if isinstance(children[0], dict) else None
        if op_leaf is None or op_leaf.get("tag") != "~":
            continue
        if "start" not in op_leaf or "end" not in op_leaf:
            continue
        # Strip the `~` and any trailing whitespace that would otherwise
        # leave a stray space before the operand.
        op_start = int(op_leaf["start"])
        op_end = int(op_leaf["end"])
        sv_bytes = sv.encode("utf-8")
        while op_end < len(sv_bytes) and sv_bytes[op_end : op_end + 1] == b" ":
            op_end += 1
        sites.append((op_start, op_end, "~", "", "unary_strip"))

    sites.sort()
    return sites


def _splice(sv: str, start: int, end: int, replacement: str) -> str:
    data = sv.encode("utf-8")
    return (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")


def _predict(original: str, replacement: str, mode: str, line: int) -> Prediction:
    if mode == "binary":
        rationale = (
            f"swapped bitwise `{original}` → `{replacement}` at line "
            f"{line}; any property constraining the bit-vector output "
            "should detect the change"
        )
    else:
        rationale = (
            f"stripped unary `~` at line {line}; the operand's bits flow "
            "through unaltered, so any property comparing against the "
            "inverted form should detect the change"
        )
    return Prediction(rationale=rationale, perturbs_liveness=False)


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_sites(sv)
    if not sites:
        return
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        start, end, original, replacement, mode = sites[idx]
        line, _ = _byte_to_line_col(sv, start)
        summary = (
            f"line {line}: `{original}` -> `{replacement}`"
            if mode == "binary"
            else f"line {line}: strip unary `~`"
        )
        yield Mutant(
            sv=_splice(sv, start, end, replacement),
            diff_summary=summary,
            seed=start,
            prediction=_predict(original, replacement, mode, line),
            kind=MutationKind.BIT_OP_FLIP,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for start, _end, original, replacement, mode in _find_sites(sv):
        line, column = _byte_to_line_col(sv, start)
        snippet = original if mode == "binary" else "~"
        yield Site(
            kind=MutationKind.BIT_OP_FLIP,
            line=line,
            column=column,
            snippet=snippet,
            prediction=_predict(original, replacement, mode, line),
        )


operator = _mutants
candidates = _candidates
