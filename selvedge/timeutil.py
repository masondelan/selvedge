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
_RELATIVE_RE = re.compile(r"(\d+)(mo|mon|h|d|m|y)$", re.IGNORECASE)


def normalize_timestamp(ts: str) -> str:
    """
    Normalize an ISO 8601 timestamp string to canonical UTC form.

    Accepts naive timestamps (assumed UTC), tz-aware timestamps in any
    offset, and 'Z' suffixed timestamps. Always returns a string of the
    form ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``.

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
    iso = dt.isoformat()
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

    m = _RELATIVE_RE.fullmatch(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta_map = {
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "m": timedelta(minutes=n),
            "y": timedelta(days=n * 365),
            "mo": timedelta(days=n * 30),
            "mon": timedelta(days=n * 30),
        }
        cutoff = datetime.now(timezone.utc) - delta_map[unit]
        return normalize_timestamp(cutoff.isoformat())

    # Try as an absolute ISO 8601 timestamp.
    try:
        return normalize_timestamp(s)
    except ValueError as e:
        raise ValueError(
            f"could not parse {since!r} as a relative time "
            "(e.g. '7d', '24h', '15m', '5mo', '1y') or ISO 8601 timestamp"
        ) from e
