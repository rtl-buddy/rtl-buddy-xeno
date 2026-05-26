"""Coverage for the cst.py and slang.py facades.

The facades wrap optional-extras imports (``[verible]`` →
``rtl-buddy-view``; ``[slang]`` → ``pyslang``). These tests verify:

1. When the extra is installed, the facade returns useful results.
2. When the extra is missing, calling into the facade raises
   :class:`ImportError` with a clear pointer at the extra to install
   (not a bare ``ModuleNotFoundError``).

The "missing extra" path is exercised by monkeypatching the facade's
internal ``_import_*`` helpers — that's cheaper and more deterministic
than uninstalling packages in-process.
"""

from __future__ import annotations

import sys

import pytest

from rtl_buddy_xeno import cst, slang


# --- slang.py facade --------------------------------------------------------


def test_slang_is_available_returns_bool() -> None:
    assert isinstance(slang.is_available(), bool)


def test_slang_elaborate_raises_clear_importerror_when_pyslang_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If pyslang isn't installed, the error message must point at [slang]."""
    monkeypatch.setitem(sys.modules, "pyslang", None)  # poison the import
    with pytest.raises(ImportError, match=r"\[slang\]"):
        slang.elaborate_text("module a; endmodule\n")


def test_slang_is_available_returns_false_when_pyslang_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pyslang", None)
    assert slang.is_available() is False


# --- cst.py facade ----------------------------------------------------------


def test_cst_parse_raises_clear_importerror_when_view_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If rtl-buddy-view isn't installed, the error message points at [verible]."""
    monkeypatch.setitem(sys.modules, "rtl_buddy_view", None)
    monkeypatch.setitem(sys.modules, "rtl_buddy_view.cst_cache", None)
    monkeypatch.setitem(sys.modules, "rtl_buddy_view.offsets", None)
    monkeypatch.setitem(sys.modules, "rtl_buddy_view.frontend", None)
    monkeypatch.setitem(sys.modules, "rtl_buddy_view.frontend.verible", None)
    from pathlib import Path

    with pytest.raises(ImportError, match=r"\[verible\]"):
        cst.parse(Path("/nonexistent.sv"))


def test_cst_offset_index_raises_clear_importerror_when_view_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "rtl_buddy_view", None)
    monkeypatch.setitem(sys.modules, "rtl_buddy_view.offsets", None)
    with pytest.raises(ImportError, match=r"\[verible\]"):
        cst.offset_index("module a; endmodule\n")


# --- cst.py walkers (pure-Python; no extras needed for these tests) ---------


def test_walk_subtrees_finds_matching_tags() -> None:
    tree = {
        "tag": "kModuleDeclaration",
        "children": [
            {"tag": "kModuleHeader", "children": []},
            {
                "tag": "kModuleItemList",
                "children": [
                    {"tag": "kAlwaysStatement", "start": 10, "end": 50},
                    {"tag": "kAlwaysStatement", "start": 60, "end": 100},
                ],
            },
        ],
    }
    matches = cst.walk_subtrees(tree, "kAlwaysStatement")
    assert len(matches) == 2
    assert matches[0]["start"] == 10
    assert matches[1]["start"] == 60


def test_walk_tokens_finds_leaves() -> None:
    tree = {
        "tag": "outer",
        "children": [
            {"tag": "+", "start": 5, "end": 6, "text": "+"},
            {"tag": "other", "start": 10, "end": 11, "text": "x"},
            {"tag": "+", "start": 20, "end": 21, "text": "+"},
        ],
    }
    matches = cst.walk_tokens(tree, "+")
    assert matches == [(5, 6, "+"), (20, 21, "+")]


def test_node_span_handles_inner_node() -> None:
    """An inner node without its own ``start`` derives from leftmost/rightmost leaves."""
    tree = {
        "tag": "inner",
        "children": [
            {"tag": "a", "start": 5, "end": 6},
            {"tag": "b", "start": 20, "end": 25},
        ],
    }
    start, end = cst.node_span(tree)
    assert start == 5
    assert end == 25


def test_node_span_uses_node_own_span_when_present() -> None:
    tree = {"tag": "leaf", "start": 100, "end": 110, "text": "abc"}
    assert cst.node_span(tree) == (100, 110)
