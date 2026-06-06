"""Verible CST facade — thin wrapper over ``rtl-buddy-view`` public API.

This module is the bridge between xeno's operators and view's
Verible-CST infrastructure (locator + content-hashed cache + offset
index). It exists for two reasons:

1. **Lazy import**: the ``[verible]`` extra is optional. If a user
   installs xeno without it and runs ``CLOCK_POLARITY_SWAP``, no
   import of ``rtl_buddy_view`` happens — that path stays working.
   Calling any function in this module from inside an operator
   triggers the import; we re-raise as :class:`ImportError` with a
   clear pointer at the extra to install.
2. **Library-boundary pass-through**: callers inject ``cache_dir``,
   we pass it straight to view. xeno never reads ``root_config.yaml``
   itself — that's the orchestrator's job (see #4).

See umbrella #2 and #4 for the layering decision; view#109 for the
upstream that made these helpers public.
"""

from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rtl_buddy_view.offsets import OffsetIndex


# Minimum rtl-buddy-view release this facade targets. Kept in sync with
# the `verible` extra's `>=` floor in pyproject.toml. The pyproject floor
# guards pip/uv resolves; `_check_view_version()` repeats it on the lazy
# import path so a too-old git/editable view (which bypasses the resolve
# floor) fails with a friendly upgrade hint instead of an obscure
# AttributeError mid-mutation. There is deliberately no upper cap — view
# is pre-1.0 and we don't speculatively block its next minor.
_VIEW_MIN_VERSION = "0.2.1"


_IMPORT_HINT = (
    "rtl-buddy-xeno's Verible-CST operators require the `[verible]` extra. "
    "Install with `pip install rtl-buddy-xeno[verible]` (or "
    "`uv pip install rtl-buddy-xeno[verible]`). The extra pulls in "
    f"`rtl-buddy-view>={_VIEW_MIN_VERSION}` which ships the public CST cache "
    "+ offset helpers — see view#109."
)


def _version_tuple(version: str) -> tuple[int, ...]:
    """Leading (major, minor, patch) ints of a PEP 440 version string.

    Enough for a floor comparison; non-numeric suffixes (rc/dev/+local)
    are dropped, so a pre-release of the floor compares equal to it.
    """
    parts = []
    for segment in version.split(".")[:3]:
        match = re.match(r"\d+", segment)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts)


def _check_view_version() -> None:
    """Fail fast when a present-but-too-old rtl-buddy-view is installed.

    The `verible` extra's `>=` floor in pyproject.toml guards pip/uv
    resolves, but git and editable installs bypass it. This repeats the
    floor at import time so view drift below ``_VIEW_MIN_VERSION`` (e.g.
    the v0.2.0 git tag, which predates the include-dir filelist fix and
    ships no prebuilt SPA) surfaces as a friendly hint, not an obscure
    AttributeError mid-mutation. Skipped when the installed version can't
    be read (no distribution metadata — e.g. a test stub); there the
    resolve-time floor and a successful import stand in.
    """
    try:
        installed = importlib.metadata.version("rtl-buddy-view")
    except importlib.metadata.PackageNotFoundError:
        return
    if _version_tuple(installed) < _version_tuple(_VIEW_MIN_VERSION):
        raise ImportError(
            f"rtl-buddy-xeno's `[verible]` operators require "
            f"rtl-buddy-view >= {_VIEW_MIN_VERSION}, but {installed} is "
            f"installed. Upgrade it with:\n"
            f'    pip install -U "rtl-buddy-view >= {_VIEW_MIN_VERSION}"'
        )


def _import_view_cst() -> Any:
    try:
        from rtl_buddy_view import cst_cache  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(_IMPORT_HINT) from exc
    _check_view_version()
    return cst_cache


def _import_view_offsets() -> Any:
    try:
        from rtl_buddy_view import offsets  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(_IMPORT_HINT) from exc
    _check_view_version()
    return offsets


def _import_view_verible() -> Any:
    try:
        from rtl_buddy_view.frontend import verible  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(_IMPORT_HINT) from exc
    _check_view_version()
    return verible


def parse(
    sv_path: Path,
    *,
    cache_dir: Path | None = None,
) -> dict:
    """Parse ``sv_path`` to a Verible CST (JSON), with caching.

    Locates the Verible binary via PATH, falls back to view's vendored
    copy. The ``cache_dir`` argument is passed straight through to
    view's :func:`rtl_buddy_view.cst_cache.get_or_compute`; ``None``
    falls back to env / XDG / home per view's resolution ladder.

    Raises :class:`ImportError` with a hint about the ``[verible]``
    extra if ``rtl-buddy-view`` is not installed.
    """
    cst_cache = _import_view_cst()
    verible = _import_view_verible()
    binary = verible.locate_binary()
    raw = cst_cache.get_or_compute(
        sv_path,
        verible_binary=binary,
        compute=_run_verible_subprocess,
        cache_dir=cache_dir,
    )
    # Verible's --export_json schema wraps the tree as
    # ``{ "<filename>": { "tree": { ... } } }``. Unwrap to the tree
    # root so consumers walk the structural CST directly.
    if isinstance(raw, dict) and len(raw) == 1:
        only_value = next(iter(raw.values()))
        if isinstance(only_value, dict) and "tree" in only_value:
            return only_value["tree"]  # type: ignore[no-any-return]
    return raw  # type: ignore[no-any-return]


def offset_index(sv_text: str) -> OffsetIndex:
    """Return a byte → ``(line, column)`` index for ``sv_text``."""
    offsets = _import_view_offsets()
    return offsets.OffsetIndex.build(sv_text)


def _run_verible_subprocess(binary: Path, path: Path) -> dict:
    """Compute callback for view's CST cache.

    Mirrors the same subprocess invocation view uses internally
    (``verible-verilog-syntax --export_json --printtree <file>``). On
    cache miss view calls this with ``(binary, path)``; we just emit
    the JSON.
    """
    import json
    import subprocess

    proc = subprocess.run(
        [str(binary), "--export_json", "--printtree", str(path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"verible-verilog-syntax failed on {path}: {proc.stderr.strip()}"
        )
    return json.loads(proc.stdout)


def walk_tokens(cst: dict, kind: str) -> "list[tuple[int, int, str]]":
    """Yield ``(start_byte, end_byte, text)`` for every leaf node of ``kind``.

    Verible CST leaf nodes have shape ``{"tag": "<kind>", "start": int,
    "end": int, "text": str}``. This helper walks the tree and returns
    every leaf matching ``kind``. The caller's operator does the
    semantic filtering downstream.

    Source: Verible's ``--export_json --printtree`` schema. Tags this
    helper is known to be useful with include ``"posedge"``,
    ``"negedge"``, ``"="``, ``"<="``, ``"if"``, ``"+"``, ``"-"``,
    ``"*"``, ``"/"``, ``"&"``, ``"|"``, ``"~"``, ``"."``.
    """
    matches: list[tuple[int, int, str]] = []

    def _visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("tag") == kind and "start" in node and "end" in node:
                matches.append(
                    (int(node["start"]), int(node["end"]), str(node.get("text", "")))
                )
            for child in node.get("children", []) or []:
                _visit(child)
        elif isinstance(node, list):
            for child in node:
                _visit(child)

    _visit(cst)
    return matches


def walk_subtrees(cst: dict, tag: str) -> "list[dict]":
    """Yield every subtree (dict node) whose ``tag`` matches.

    Use this when you need the subtree itself (so you can recurse into
    its children) rather than just the leaf token range. For example,
    ``ATTRIBUTE_TOGGLE`` walks the CST for the ``"kAttributeList"`` tag
    and then inspects each subtree to extract the attribute name.
    """
    matches: list[dict] = []

    def _visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("tag") == tag:
                matches.append(node)
            for child in node.get("children", []) or []:
                _visit(child)
        elif isinstance(node, list):
            for child in node:
                _visit(child)

    _visit(cst)
    return matches


def node_span(node: dict) -> "tuple[int, int]":
    """Return ``(start_byte, end_byte)`` covering a CST subtree.

    For leaf nodes this is the node's own ``start`` / ``end``. For
    inner nodes we descend to the leftmost and rightmost leaves —
    Verible's CST inner nodes don't always carry explicit spans, so we
    derive them from the children's extremes.
    """
    if "start" in node and "end" in node:
        return int(node["start"]), int(node["end"])
    leftmost = _leftmost_offset(node)
    rightmost = _rightmost_offset(node)
    return leftmost, rightmost


def _leftmost_offset(node: Any) -> int:
    if isinstance(node, dict):
        if "start" in node:
            return int(node["start"])
        for child in node.get("children", []) or []:
            try:
                return _leftmost_offset(child)
            except ValueError:
                continue
    elif isinstance(node, list):
        for child in node:
            try:
                return _leftmost_offset(child)
            except ValueError:
                continue
    raise ValueError("no leftmost offset")


def _rightmost_offset(node: Any) -> int:
    if isinstance(node, dict):
        if "end" in node:
            return int(node["end"])
        children = node.get("children", []) or []
        for child in reversed(children):
            try:
                return _rightmost_offset(child)
            except ValueError:
                continue
    elif isinstance(node, list):
        for child in reversed(node):
            try:
                return _rightmost_offset(child)
            except ValueError:
                continue
    raise ValueError("no rightmost offset")
