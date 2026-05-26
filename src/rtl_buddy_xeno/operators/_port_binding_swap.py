"""``PORT_BINDING_SWAP`` — swap expressions between two named-port bindings.

Verible-only operator. Walks every ``kPortActualList`` (the list of
named port bindings on a module instance) and emits one mutant per
adjacent-pair expression swap. Named-port binding is order-independent
in SV, so the only mutation that changes wiring is swapping the
*expression* sides while keeping the names in place:

  ``inst u (.a(x), .b(y))`` → ``inst u (.a(y), .b(x))``

For a list with N named ports, this yields N-1 mutants (one per
adjacent pair). The construction can produce width-mismatch
elaboration errors when the swapped expressions have different
widths — caught by the validity gate's pyslang layer in CI.
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


def _named_port_parts(named_port: dict) -> tuple[str | None, int, int] | None:
    """Return ``(port_name, expr_start_byte, expr_end_byte)`` for a kActualNamedPort.

    Verible emits ``kActualNamedPort`` as
    ``['.', SymbolIdentifier(name), kParenGroup(expr)]``. We extract
    the name from the SymbolIdentifier leaf and the expression's byte
    span from the kParenGroup (the inside of the parens — not the
    parens themselves).
    """
    port_name: str | None = None
    paren_group: dict | None = None
    for child in named_port.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        if child.get("tag") == "SymbolIdentifier":
            port_name = child.get("text")
        elif child.get("tag") == "kParenGroup":
            paren_group = child
    if paren_group is None or port_name is None:
        return None
    # Find the inner expression inside the parens.
    inner_start: int | None = None
    inner_end: int | None = None
    for child in paren_group.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        tag = child.get("tag")
        if tag in ("(", ")"):
            continue
        try:
            s, e = _cst.node_span(child)
        except ValueError:
            continue
        inner_start = s if inner_start is None else min(inner_start, s)
        inner_end = e if inner_end is None else max(inner_end, e)
    if inner_start is None or inner_end is None:
        return None
    return port_name, inner_start, inner_end


def _find_swap_sites(
    sv: str,
) -> list[tuple[int, int, int, int, str, str]]:
    """Return ``(a_start, a_end, b_start, b_end, a_name, b_name)`` per swap.

    Each entry is one adjacent-pair swap candidate. Sort key is the
    earlier of the two start offsets — so source-order iteration on the
    enumerated swaps lines up with source-order iteration on the
    underlying port lists.
    """
    path = _sv_to_tempfile(sv)
    cst_root = _cst.parse(path)
    swaps: list[tuple[int, int, int, int, str, str]] = []
    for port_list in _cst.walk_subtrees(cst_root, "kPortActualList"):
        named: list[dict] = []
        for child in port_list.get("children", []) or []:
            if isinstance(child, dict) and child.get("tag") == "kActualNamedPort":
                named.append(child)
        if len(named) < 2:
            continue
        parts: list[tuple[str, int, int]] = []
        for np in named:
            extracted = _named_port_parts(np)
            if extracted is None:
                continue
            name, start, end = extracted
            assert name is not None
            parts.append((name, start, end))
        for i in range(len(parts) - 1):
            a_name, a_start, a_end = parts[i]
            b_name, b_start, b_end = parts[i + 1]
            swaps.append((a_start, a_end, b_start, b_end, a_name, b_name))
    swaps.sort()
    return swaps


def _splice_swap(sv: str, a_start: int, a_end: int, b_start: int, b_end: int) -> str:
    """Swap the byte ranges ``sv[a_start:a_end]`` and ``sv[b_start:b_end]``.

    Assumes ``a_end <= b_start`` (the two ranges don't overlap and
    ``a`` comes before ``b``). Returns the mutated source.
    """
    assert a_end <= b_start, "swap byte ranges must not overlap"
    data = sv.encode("utf-8")
    a_text = data[a_start:a_end]
    b_text = data[b_start:b_end]
    middle = data[a_end:b_start]
    mutated_bytes = data[:a_start] + b_text + middle + a_text + data[b_end:]
    return mutated_bytes.decode("utf-8")


def _predict(a_name: str, b_name: str, line: int) -> Prediction:
    return Prediction(
        rationale=(
            f"swapped expressions of `.{a_name}(...)` and `.{b_name}(...)` "
            f"at line {line}; the instance's input/output wiring changes, "
            "so any property constraining the signal flow through this "
            "instance should detect the change"
        ),
        perturbs_signals=frozenset({a_name, b_name}),
        perturbs_liveness=False,
    )


def _mutants(sv: str, rng: random.Random) -> Iterator[Mutant]:
    swaps = _find_swap_sites(sv)
    if not swaps:
        return
    order = list(range(len(swaps)))
    rng.shuffle(order)
    for idx in order:
        a_start, a_end, b_start, b_end, a_name, b_name = swaps[idx]
        line, _ = _byte_to_line_col(sv, a_start)
        yield Mutant(
            sv=_splice_swap(sv, a_start, a_end, b_start, b_end),
            diff_summary=(f"line {line}: swap `.{a_name}(...)` ↔ `.{b_name}(...)`"),
            seed=a_start,
            prediction=_predict(a_name, b_name, line),
            kind=MutationKind.PORT_BINDING_SWAP,
        )


def _candidates(sv: str) -> Iterator[Site]:
    for a_start, _a_end, _b_start, _b_end, a_name, b_name in _find_swap_sites(sv):
        line, column = _byte_to_line_col(sv, a_start)
        yield Site(
            kind=MutationKind.PORT_BINDING_SWAP,
            line=line,
            column=column,
            snippet=f".{a_name}(...) <-> .{b_name}(...)",
            prediction=_predict(a_name, b_name, line),
        )


operator = _mutants
candidates = _candidates
