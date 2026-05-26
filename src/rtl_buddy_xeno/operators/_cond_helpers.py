"""Shared condition-site discovery for COND_NEGATE and COND_CONST.

Both operators perturb the same conditional sites — they only differ
in *how* they rewrite the condition. The discovery walker is shared
so the two operators always see the exact same candidate set.

Discovered site shapes:

- ``if (...)`` headers: the byte span of whatever sits between the
  parens (not including the parens themselves).
- ``... ? ... : ...`` ternaries: the byte span of the condition (the
  first sub-expression).
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from rtl_buddy_xeno import cst as _cst


def sv_to_tempfile(sv: str) -> Path:
    digest = hashlib.sha256(sv.encode("utf-8")).hexdigest()[:16]
    tmpdir = Path(tempfile.gettempdir()) / "rtl-buddy-xeno"
    tmpdir.mkdir(parents=True, exist_ok=True)
    target = tmpdir / f"sv-{digest}.sv"
    if not target.exists() or target.read_text() != sv:
        target.write_text(sv)
    return target


def byte_to_line_col(sv: str, byte_offset: int) -> tuple[int, int]:
    head = sv.encode("utf-8")[:byte_offset]
    line = head.count(b"\n") + 1
    last_newline = head.rfind(b"\n")
    column = (byte_offset - last_newline) if last_newline >= 0 else byte_offset + 1
    return line, column


def find_condition_sites(sv: str) -> list[tuple[int, int, str]]:
    """Return ``(start, end, kind)`` byte spans for every condition expr.

    ``kind`` is ``"if"`` for ``if (cond)`` / ``else if (cond)`` headers
    and ``"ternary"`` for ``cond ? a : b`` expressions. The span covers
    the *condition expression only* — the surrounding parens (for
    ``if``) and the ``?``/``:`` punctuators (for the ternary) are
    outside the returned range.
    """
    path = sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()

    # if-header pattern: kIfHeader → kParenGroup → [('(' leaf), inner, (')' leaf)]
    for header in _cst.walk_subtrees(cst_root, "kIfHeader"):
        paren_group = _first_subtree_with_tag(header, "kParenGroup")
        if paren_group is None:
            continue
        inner = _inner_of_paren_group(paren_group)
        if inner is None:
            continue
        try:
            start, end = _cst.node_span(inner)
        except ValueError:
            continue
        if (start, end) in seen:
            continue
        seen.add((start, end))
        sites.append((start, end, "if"))

    # Ternary: kConditionExpression → [cond_expr, '?' leaf, true_expr, ':' leaf, false_expr]
    for ternary in _cst.walk_subtrees(cst_root, "kConditionExpression"):
        children = [
            c for c in (ternary.get("children", []) or []) if isinstance(c, dict)
        ]
        if not children:
            continue
        cond_node = children[0]
        try:
            start, end = _cst.node_span(cond_node)
        except ValueError:
            continue
        if (start, end) in seen:
            continue
        seen.add((start, end))
        sites.append((start, end, "ternary"))

    sites.sort()
    return sites


def _first_subtree_with_tag(parent: dict, tag: str) -> dict | None:
    for child in parent.get("children", []) or []:
        if isinstance(child, dict) and child.get("tag") == tag:
            return child
    return None


def _inner_of_paren_group(paren_group: dict) -> dict | None:
    """Return the non-paren child of a kParenGroup subtree.

    Verible emits ``kParenGroup`` as ``[ '(' leaf, inner_expr, ')' leaf ]``.
    We want the inner_expr.
    """
    for child in paren_group.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if tag in ("(", ")"):
            continue
        return child
    return None
