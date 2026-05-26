"""``RESET_POLARITY_FLIP`` — flip ``posedge``/``negedge`` on reset edges.

Verible-only operator with optional pyslang confidence flagging.
Walks ``kEventExpression`` nodes (sensitivity-list entries like
``posedge clk`` or ``negedge rst_n``) and emits a mutant when:

1. The edge token is ``posedge`` or ``negedge``.
2. The signal name matches the reset-naming heuristic (contains
   ``rst`` or ``reset``, case-insensitive).

The name heuristic catches the SV convention used in 95%+ of real
designs (``rst_n``, ``reset``, ``arst``, ``a_rst_n``, ``preset``,
etc.). When the ``[slang]`` extra is installed, the rationale notes
"pyslang-elaborated" to flag that the design's overall structure was
also valid; otherwise it says "name-heuristic only."

This operator is intentionally narrower than :mod:`._clock_polarity_swap`
— that one flips every ``posedge``/``negedge`` token regardless of
signal identity, which targets CDC-006. This one targets RDC-007
(reset-sync polarity wired backwards). The two operators have
distinct prediction sets and shouldn't be conflated.
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

_FLIPS: dict[str, str] = {"posedge": "negedge", "negedge": "posedge"}

# Match reset-style identifiers. Case-insensitive substring on `rst` or
# `reset` covers `rst_n`, `arst`, `reset`, `Preset`, `clk_rst_b`, etc.
# Word-boundary anchors would skip `rst_n` (Python regex treats `_` as a
# word character) so we accept any substring. False positives like
# `restore` are rare in sensitivity-list context — real RTL doesn't
# clock on arbitrary identifiers.
_RESET_NAME_RE = re.compile(r"rst|reset", re.IGNORECASE)


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


def _signal_name(expression_node: dict) -> str:
    """Walk a kExpression subtree and return its first SymbolIdentifier text.

    A bare reset reference like ``rst_n`` is a kExpression containing a
    SymbolIdentifier leaf. We pick the first one we find — good enough
    for the heuristic.
    """
    if isinstance(expression_node, dict):
        if (
            expression_node.get("tag") == "SymbolIdentifier"
            and expression_node.get("text") is not None
        ):
            return str(expression_node["text"])
        for child in expression_node.get("children", []) or []:
            name = _signal_name(child)
            if name:
                return name
    return ""


def _looks_like_reset(name: str) -> bool:
    return bool(_RESET_NAME_RE.search(name))


def _find_sites(sv: str) -> list[tuple[int, int, str, str, str]]:
    """Return ``(start, end, original_edge, replacement_edge, signal_name)`` per site."""
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    sites: list[tuple[int, int, str, str, str]] = []
    for ev in _cst.walk_subtrees(cst_root, "kEventExpression"):
        children = [c for c in (ev.get("children", []) or []) if isinstance(c, dict)]
        if len(children) < 2:
            continue
        edge_leaf = children[0]
        expr = children[1]
        edge_tag = edge_leaf.get("tag")
        if edge_tag not in _FLIPS:
            continue
        if "start" not in edge_leaf or "end" not in edge_leaf:
            continue
        name = _signal_name(expr)
        if not _looks_like_reset(name):
            continue
        sites.append(
            (
                int(edge_leaf["start"]),
                int(edge_leaf["end"]),
                str(edge_tag),
                _FLIPS[str(edge_tag)],
                name,
            )
        )
    sites.sort()
    return sites


def _splice(sv: str, start: int, end: int, replacement: str) -> str:
    data = sv.encode("utf-8")
    return (data[:start] + replacement.encode("utf-8") + data[end:]).decode("utf-8")


def _predict(
    original: str, replacement: str, signal: str, line: int, slang_elaborated: bool
) -> Prediction:
    confidence = "pyslang-elaborated" if slang_elaborated else "name-heuristic only"
    return Prediction(
        rationale=(
            f"flipped `{original}` → `{replacement}` on reset signal "
            f"`{signal}` at line {line} ({confidence}); the synchroniser "
            "now deasserts on the opposite edge, so any property "
            "constraining reset-release timing or RDC-007 (reset-sync "
            "polarity wired backwards) should fire"
        ),
        cdc_rules_added=frozenset({"RDC-007"}),
        perturbs_signals=frozenset({signal}),
        perturbs_liveness=False,
    )


def _try_elaborate(sv: str) -> bool:
    if not _slang.is_available():
        return False
    try:
        _slang.elaborate_text(sv)
    except Exception:  # pragma: no cover - elaboration error catch-all
        return False
    return True


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    sites = _find_sites(sv)
    if not sites:
        return
    slang_elaborated = _try_elaborate(sv)
    order = list(range(len(sites)))
    rng.shuffle(order)
    for idx in order:
        start, end, original, replacement, name = sites[idx]
        line, _ = _byte_to_line_col(sv, start)
        yield Mutant(
            sv=_splice(sv, start, end, replacement),
            diff_summary=(
                f"line {line}: reset edge `{original}` -> `{replacement}` on `{name}`"
            ),
            seed=start,
            prediction=_predict(original, replacement, name, line, slang_elaborated),
            kind=MutationKind.RESET_POLARITY_FLIP,
        )


def _candidates(sv: str) -> Iterator[Site]:
    slang_elaborated = _try_elaborate(sv)
    for start, _end, original, replacement, name in _find_sites(sv):
        line, column = _byte_to_line_col(sv, start)
        yield Site(
            kind=MutationKind.RESET_POLARITY_FLIP,
            line=line,
            column=column,
            snippet=f"{original} {name}",
            prediction=_predict(original, replacement, name, line, slang_elaborated),
        )


operator = _mutants
candidates = _candidates
