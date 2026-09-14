"""Reset-name heuristic — the one table every reset-aware operator reads.

Extracted from :mod:`._clock_polarity_swap`, which owned the table
while it was the only consumer. :mod:`._reset_fanin_merge` (xeno#15)
needs the same "is this identifier a reset?" judgement *and* an
active-high/active-low judgement, and two copies of a naming heuristic
drift apart — so the table lives here, in a module that imports
nothing but :mod:`re`.

Pure regex on purpose: a parser-free operator (``CLOCK_POLARITY_SWAP``)
and a Verible-CST operator (``RESET_FANIN_MERGE``) both import this,
and the no-straddle rule in :mod:`rtl_buddy_xeno.operators` is read off
each operator module's imports. A sibling that pulls in no parser keeps
the parser-free operator parser-free.

The patterns cover the canonical idioms used in rtl-buddy-cdc's fuzz
corpus templates and the wider conventions documented in
rtl-buddy-cdc's reset-domain helper. The list intentionally stops
short of vendor-specific names (every chip family has its own
``por_n`` / ``hreset`` etc.) — consumers accept skipping a few real
resets rather than enumerating the whole namespace.
"""

from __future__ import annotations

import re

__all__ = ["RESET_NAME_PATTERNS", "is_active_low", "is_reset_name"]


RESET_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(arst|reset|rst)(_?n)?$", re.IGNORECASE),
    re.compile(r"^(raw_rst|raw_reset|global_rst|local_rst)(_?n)?$", re.IGNORECASE),
    re.compile(r"^(porst|presetn|hreset|nreset)$", re.IGNORECASE),
)

# Active-low spelling: a trailing ``n`` (with or without a separating
# underscore) — ``rst_n``, ``rstn``, ``resetn``, ``presetn`` — or a
# leading ``n`` followed by more name, as in ``nreset``. Everything
# else (``rst``, ``reset``, ``arst``, ``porst``, ``hreset``) reads as
# active-high.
_ACTIVE_LOW_SUFFIX = re.compile(r"_?n$")
_ACTIVE_LOW_PREFIX = re.compile(r"^n[a-z0-9_]")


def is_reset_name(name: str) -> bool:
    """``True`` when ``name`` matches the reset-naming heuristic."""
    return any(pattern.match(name) for pattern in RESET_NAME_PATTERNS)


def is_active_low(name: str) -> bool:
    """``True`` when ``name`` is spelled like an active-low reset.

    Name-only judgement — it never looks at how the signal is used.
    Consumers use it to refuse mixing an active-low and an active-high
    reset into one fan-in gate, where "combine them" has no single
    correct operator.
    """
    lowered = name.lower()
    if _ACTIVE_LOW_SUFFIX.search(lowered):
        return True
    return bool(_ACTIVE_LOW_PREFIX.match(lowered))
