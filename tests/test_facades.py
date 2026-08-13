"""Coverage for the cst.py and slang.py facades.

The facades wrap optional-extras imports (``[verible]`` →
``rtl-buddy-sch``; ``[slang]`` → ``pyslang``). These tests verify:

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
    """If the viewer isn't installed, the error message points at [verible]."""
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


# --- cst.py view-floor guard ------------------------------------------------


def test_version_tuple_drops_nonnumeric_suffix() -> None:
    """A pre-release of the floor compares equal to it (rc suffix dropped)."""
    assert cst._version_tuple("0.2.1") == (0, 2, 1)
    assert cst._version_tuple("0.2.1rc1") == (0, 2, 1)
    assert cst._version_tuple("0.2.0") < cst._version_tuple("0.2.1")


def _fake_dists(monkeypatch: pytest.MonkeyPatch, installed: dict[str, str]) -> None:
    """Answer viewer-distribution lookups from ``installed``.

    Only the names in ``cst._VIEW_DIST_NAMES`` are served from the
    fixture; every other distribution defers to the real
    ``importlib.metadata.version``. Patching the module attribute is
    process-wide for the duration of a test, so narrowing it this way
    keeps the probe *order* as the thing under test rather than blinding
    every unrelated metadata lookup that happens to run inside the
    window.
    """
    import importlib.metadata

    real_version = importlib.metadata.version

    def _version(name: str) -> str:
        if name in installed:
            return installed[name]
        if name in cst._VIEW_DIST_NAMES:
            raise importlib.metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(importlib.metadata, "version", _version)


def test_view_dist_names_probe_the_renamed_dist_first() -> None:
    """The rename (view -> sch) is what the probe order encodes."""
    assert cst._VIEW_DIST_NAMES == ("rtl-buddy-sch", "rtl-buddy-view")


def test_view_dist_version_finds_the_renamed_dist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New dist only — the case the old single-name probe read as absent."""
    _fake_dists(monkeypatch, {"rtl-buddy-sch": "0.7.0"})
    assert cst._view_dist_version() == ("rtl-buddy-sch", "0.7.0")


def test_view_dist_version_falls_back_to_the_legacy_dist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old dist only — frozen at 0.5.0, still a working install."""
    _fake_dists(monkeypatch, {"rtl-buddy-view": "0.5.0"})
    assert cst._view_dist_version() == ("rtl-buddy-view", "0.5.0")


def test_view_dist_version_prefers_new_dist_when_both_are_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale `rtl-buddy-view` metadata must not mask the renamed dist."""
    _fake_dists(monkeypatch, {"rtl-buddy-sch": "0.7.0", "rtl-buddy-view": "0.2.0"})
    assert cst._view_dist_version() == ("rtl-buddy-sch", "0.7.0")


def test_view_dist_version_is_none_when_neither_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_dists(monkeypatch, {})
    assert cst._view_dist_version() is None


def test_view_dist_probe_leaves_other_distributions_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown names defer to the real lookup, not to the fixture."""
    import importlib.metadata

    real = importlib.metadata.version("pytest")
    _fake_dists(monkeypatch, {"rtl-buddy-sch": "0.7.0"})
    assert importlib.metadata.version("pytest") == real


def test_check_view_version_skips_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither distribution present (e.g. a test stub) → skip silently."""
    _fake_dists(monkeypatch, {})
    cst._check_view_version()  # must not raise


def test_check_view_version_raises_on_too_old_present_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A present-but-too-old viewer fails with an actionable upgrade hint."""
    _fake_dists(monkeypatch, {"rtl-buddy-view": "0.2.0"})
    with pytest.raises(ImportError) as excinfo:
        cst._check_view_version()
    message = str(excinfo.value)
    # Names the dist actually found, and points the remedy at the new one.
    assert "rtl-buddy-view 0.2.0" in message
    assert f'pip install -U "rtl-buddy-sch >= {cst._SCH_MIN_VERSION}"' in message
    # The uninstall leads: two dists claiming `rtl_buddy_view` is worse
    # than the stale version this guard exists to catch.
    assert "pip uninstall -y rtl-buddy-view && pip install -U" in message


def test_check_view_version_accepts_floor_and_newer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The legacy floor itself and any newer version pass, under either name."""
    for ver in ("0.2.1", "0.2.1rc1", "0.3.0", "0.5.0", "1.0.0"):
        _fake_dists(monkeypatch, {"rtl-buddy-view": ver})
        cst._check_view_version()  # must not raise


def test_check_view_version_accepts_every_renamed_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`rtl-buddy-sch` starts at 0.7.0, so it clears the floor outright."""
    assert cst._version_tuple(cst._SCH_MIN_VERSION) >= cst._version_tuple(
        cst._VIEW_MIN_VERSION
    )
    for ver in (cst._SCH_MIN_VERSION, "0.8.0", "1.0.0"):
        _fake_dists(monkeypatch, {"rtl-buddy-sch": ver})
        cst._check_view_version()  # must not raise


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
