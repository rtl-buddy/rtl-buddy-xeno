"""``ARITH_FLIP`` — swap arithmetic operators (``+`` ↔ ``-``, ``*`` ↔ ``/``).

Verible-only operator. Walks ``kBinaryExpression`` nodes in the CST,
identifies the operator leaf, and emits a byte-splice mutant when the
operator is one of the arithmetic ones we know how to flip. The
Verible CST stops us from mutating ``+`` / ``-`` / ``*`` / ``/``
inside string literals, comments, and macro bodies — those never
become ``kBinaryExpression`` operator leaves.

Mutation map:

- ``+`` → ``-``
- ``-`` → ``+``
- ``*`` → ``/``
- ``/`` → ``*``

The mutation predicts ``perturbs_signals`` (LHS of the enclosing
assignment, when discoverable) and a rationale recording the swap.
"""

from __future__ import annotations

import hashlib
import random
import tempfile
from collections.abc import Iterator
from pathlib import Path

from rtl_buddy_xeno import cst as _cst
from rtl_buddy_xeno.mutator import MutationKind, Mutant, Prediction, Site

_SWAPS: dict[str, str] = {
    "+": "-",
    "-": "+",
    "*": "/",
    "/": "*",
}


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


def _find_sites(sv: str) -> list[tuple[int, int, str, str]]:
    """Return ``(start, end, original_op, replacement_op)`` per candidate."""
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str, str]] = []
    for subtree in _cst.walk_subtrees(cst_root, "kBinaryExpression"):
        # kBinaryExpression children: [lhs, op_leaf, rhs]. The op leaf
        # has its own `tag` equal to the operator string and carries
        # the byte span.
        for child in subtree.get("children", []) or []:
            if not isinstance(child, dict):
                continue
            tag = child.get("tag")
            if tag not in _SWAPS:
                continue
            if "start" not in child or "end" not in child:
                continue
            sites.append((int(child["start"]), int(child["end"]), tag, _SWAPS[tag]))
    sites.sort()
    return sites


def _splice(sv: str, start: int, end: int, replacement: str) -> str:
    data = sv.encode("utf-8")
    return (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")


def _predict(original: str, replacement: str, line: int) -> Prediction:
    return Prediction(
        rationale=(
            f"swapped `{original}` → `{replacement}` at line {line}; "
            "any property constraining the expression's output value "
            "should detect the change"
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
        start, end, original, replacement = sites[idx]
        line, _ = _byte_to_line_col(sv, start)
        yield Mutant(
            sv=_splice(sv, start, end, replacement),
            diff_summary=f"line {line}: `{original}` -> `{replacement}`",
            seed=start,
            prediction=_predict(original, replacement, line),
            kind=MutationKind.ARITH_FLIP,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for start, _end, original, replacement in _find_sites(sv):
        line, column = _byte_to_line_col(sv, start)
        yield Site(
            kind=MutationKind.ARITH_FLIP,
            line=line,
            column=column,
            snippet=original,
            prediction=_predict(original, replacement, line),
        )


operator = _mutants
candidates = _candidates
