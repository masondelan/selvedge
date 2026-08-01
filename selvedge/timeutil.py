"""
Time parsing and normalization utilities.

All Selvedge timestamps are stored as canonical UTC ISO 8601 strings with
a trailing 'Z' suffix so that lexicographic ordering matches chronological
ordering — even across mixed timezones in callers.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# Relative time pattern. The 'mo'/'mon' alternatives must precede 'm' so the
# regex matches them first — '5mo' is months, '5m' is minutes.
#
# Units are matched case-INSENSITIVELY except for a bare 'M'. Uppercase is a
# convenience everywhere else ('90D' is '90d'), but 'M' is the one letter where
# it collides with a different unit: '6M' is the near-universal spelling for
# six months, and IGNORECASE silently resolved it to six MINUTES. That made
# `--since 6M` show six minutes of history, and a decision logged with
# `revisit_after="6M"` fall due six minutes later and stay permanently overdue.
# Better to fail loudly and let the caller write '6mo' or '6m'.
_RELATIVE_RE = re.compile(r"(\d+)(mo|mon|h|d|m|y)$", re.IGNORECASE)
_AMBIGUOUS_UPPER_M = re.compile(r"^\d+M$")


def _relative_match(s: str) -> re.Match[str] | None:
    """Match the relative grammar, rejecting the ambiguous bare uppercase 'M'."""
    if _AMBIGUOUS_UPPER_M.fullmatch(s):
        return None
    return _RELATIVE_RE.fullmatch(s)


def _relative_delta(n: int, unit: str) -> timedelta:
    """Map a relative-shorthand (count, unit) pair to a ``timedelta``.

    Shared by :func:`parse_time_string` (which subtracts it from *now*) and
    :func:`resolve_revisit_due` (which adds it to an event's timestamp), so the
    two surfaces can't drift on what ``90d`` or ``5mo`` means.
    """
    return {
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "m": timedelta(minutes=n),
        "y": timedelta(days=n * 365),
        "mo": timedelta(days=n * 30),
        "mon": timedelta(days=n * 30),
    }[unit]


def normalize_timestamp(ts: str) -> str:
    """
    Normalize an ISO 8601 timestamp string to canonical UTC form.

    Accepts naive timestamps (assumed UTC), tz-aware timestamps in any
    offset, and 'Z' suffixed timestamps. Always returns a string of the
    form ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` — fixed width, always including the
    fractional part, so that lexicographic order equals chronological order
    wherever timestamps are sorted as text (which is everywhere in the store).

    Raises ValueError if the input is empty or unparseable.
    """
    if not ts:
        raise ValueError("timestamp must not be empty")
    s = ts.strip()
    s_for_parse = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    dt = datetime.fromisoformat(s_for_parse)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    # Always emit the fractional part, even when it is zero. Every ORDER BY in
    # the store sorts timestamps as TEXT, and '.' (0x2E) sorts below 'Z'
    # (0x5A) — so a bare ...SSZ would sort AFTER a ...SS.ffffffZ in the same
    # second, making lexicographic order disagree with chronological order.
    # Both precisions arrive routinely: git's %aI and imported Agent Trace
    # records are second-precision, utc_now_iso() is microsecond-precision.
    iso = dt.isoformat(timespec="microseconds")
    if iso.endswith("+00:00"):
        iso = iso[:-6] + "Z"
    return iso


def utc_now_iso() -> str:
    """Return the current time as a canonical UTC ISO 8601 string."""
    return normalize_timestamp(datetime.now(timezone.utc).isoformat())


def parse_time_string(since: str) -> str:
    """
    Convert a time string into a canonical UTC ISO 8601 timestamp.

    Accepts:
        - Relative shorthand: ``24h``, ``7d``, ``15m`` (minutes), ``5mo`` /
          ``5mon`` (months ~30d), ``1y`` (year ~365d)
        - Absolute ISO 8601 timestamps in any timezone

    Note: ``m`` means minutes, ``mo`` means months. (Earlier versions of
    Selvedge mapped ``m`` to months, which silently produced wrong results
    for users expecting ``5m`` to mean "5 minutes ago".)

    Raises ValueError if the input is neither a valid relative shorthand
    nor a parseable ISO timestamp.
    """
    s = since.strip()
    if not s:
        raise ValueError("time string is empty")

    m = _relative_match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        cutoff = datetime.now(timezone.utc) - _relative_delta(n, unit)
        return normalize_timestamp(cutoff.isoformat())

    # Try as an absolute ISO 8601 timestamp.
    try:
        return normalize_timestamp(s)
    except ValueError as e:
        raise ValueError(
            f"could not parse {since!r} as a relative time "
            "(e.g. '7d', '24h', '15m', '5mo', '1y') or ISO 8601 timestamp"
        ) from e


def parse_window_minutes(value: str) -> int:
    """Parse a duration window into whole minutes (v0.3.8 CLI ``--window``).

    Accepts the same relative grammar as ``--since`` (``7d``, ``60m``, ``24h``,
    ``6mo``, ``1y``) and bare integers (treated as minutes, so ``--window 90``
    means 90 minutes). Powers ``selvedge prior-attempts --window`` mapping onto
    the storage layer's ``window_minutes``. Raises ``ValueError`` otherwise.
    """
    s = value.strip()
    if not s:
        raise ValueError("window is empty")
    if s.isdigit():
        return int(s)
    m = _relative_match(s)
    if not m:
        raise ValueError(
            f"could not parse window {value!r} as a duration "
            "(e.g. '7d', '60m', '24h') or a plain integer of minutes"
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    return int(_relative_delta(n, unit).total_seconds() // 60)


def normalize_revisit_after(value: str) -> str:
    """Validate and canonicalize a ``revisit_after`` value for storage (v0.3.8).

    ``revisit_after`` carries *either* an absolute ISO-8601 date/datetime *or*
    a relative offset from the event's own ``timestamp`` (e.g. ``90d``). Unlike
    :func:`parse_time_string` — which resolves relative shorthand against *now*
    and into the past — this preserves a relative offset verbatim (lowercased)
    so the stored intent stays "revisit 90 days after this change," resolvable
    later by :func:`resolve_revisit_due`. It uses the SAME grammar as ``--since``
    so the two surfaces validate identically.

    Returns ``""`` for empty input. Absolute values are normalized to canonical
    UTC. Raises ``ValueError`` on anything that is neither a relative offset nor
    a parseable ISO timestamp — the write path surfaces that as a friendly
    error rather than silently storing garbage.
    """
    s = value.strip()
    if not s:
        return ""
    if _relative_match(s):
        return s.lower()
    try:
        return normalize_timestamp(s)
    except ValueError as e:
        raise ValueError(
            f"could not parse revisit_after {value!r} as a relative offset "
            "(e.g. '90d', '6mo', '1y') or an ISO 8601 date"
        ) from e


def resolve_revisit_due(timestamp: str, revisit_after: str) -> datetime:
    """Resolve when a decision's ``revisit_after`` falls due, as an aware UTC datetime.

    ``revisit_after`` is interpreted relative to ``timestamp`` (the event time)
    when it's a relative offset (``90d`` → 90 days after the change), or taken
    as an absolute moment when it's an ISO date/datetime. Inverse pairing with
    :func:`normalize_revisit_after`, which is what produced the stored value.

    Raises ``ValueError`` if ``revisit_after`` is empty or unparseable.
    """
    s = revisit_after.strip()
    if not s:
        raise ValueError("revisit_after is empty")
    m = _relative_match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        base = _parse_aware(normalize_timestamp(timestamp))
        return base + _relative_delta(n, unit)
    return _parse_aware(normalize_timestamp(s))


def _parse_aware(ts: str) -> datetime:
    """Parse a canonical UTC ISO string (``...Z``) into an aware datetime."""
    s = ts[:-1] + "+00:00" if ts.endswith(("Z", "z")) else ts
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
