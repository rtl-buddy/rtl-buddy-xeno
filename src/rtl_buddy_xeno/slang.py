"""pyslang facade — lazy elaboration backend with install-time guard.

Operators that need semantic context (sync-chain identification,
state-bearing-assignment classification, etc.) elaborate via pyslang.
The import is deferred until an operator actually invokes
:func:`elaborate` — the ``[slang]`` extra is optional, and the
no-extras install path keeps working for ``CLOCK_POLARITY_SWAP``
(and the no-pyslang-needed Verible operators like
``ATTRIBUTE_TOGGLE``).

If pyslang isn't installed when :func:`elaborate` (or any other
helper here) is called, we re-raise as :class:`ImportError` with a
clear pointer at the ``[slang]`` extra.

See umbrella #2 and #4 for the layering decision.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyslang  # noqa: F401  (typing only)


_IMPORT_HINT = (
    "rtl-buddy-xeno's pyslang-backed operators require the `[slang]` extra. "
    "Install with `pip install rtl-buddy-xeno[slang]` (or "
    "`uv pip install rtl-buddy-xeno[slang]`). The extra pulls in "
    "`pyslang>=10,<11`."
)


def _import_pyslang() -> Any:
    try:
        import pyslang  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(_IMPORT_HINT) from exc
    return pyslang


def elaborate(sv_path: Path) -> Any:
    """Elaborate ``sv_path`` and return the pyslang :class:`Compilation`.

    The compilation object carries every elaborated declaration with
    source ranges attached — operators consume it to classify sites
    structurally (state-bearing vs combinational; parameter-expression
    vs runtime expression; etc.).

    Raises :class:`ImportError` with the ``[slang]`` extra hint if
    pyslang is not installed.
    """
    pyslang = _import_pyslang()
    tree = pyslang.SyntaxTree.fromFile(str(sv_path))
    compilation = pyslang.Compilation()
    compilation.addSyntaxTree(tree)
    return compilation


def elaborate_text(sv_text: str) -> Any:
    """Like :func:`elaborate` but accepts SV source as a literal string.

    Useful for tests and for in-memory mutation flows where the mutated
    SV hasn't been written to disk.
    """
    pyslang = _import_pyslang()
    tree = pyslang.SyntaxTree.fromText(sv_text)
    compilation = pyslang.Compilation()
    compilation.addSyntaxTree(tree)
    return compilation


def is_available() -> bool:
    """Return ``True`` iff pyslang can be imported in the current env.

    Useful for test conditionals and for operator-level "skip if no
    slang" diagnostics. Does not raise; on missing pyslang returns
    ``False``.
    """
    try:
        _import_pyslang()
    except ImportError:
        return False
    return True
