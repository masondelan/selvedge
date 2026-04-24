"""
Tests for the shared reasoning-quality validator.

Both ``server.log_change`` and the ``selvedge log`` CLI route through
``selvedge.validation.check_reasoning_quality`` so the rules stay in sync.
"""

from __future__ import annotations

import pytest

from selvedge.validation import (
    GENERIC_REASONING_PATTERNS,
    REASONING_MIN_LENGTH,
    check_reasoning_quality,
)

# ---------------------------------------------------------------------------
# Empty / whitespace
# ---------------------------------------------------------------------------


def test_empty_reasoning_warns():
    warnings = check_reasoning_quality("")
    assert any("empty" in w for w in warnings)


def test_whitespace_only_warns():
    warnings = check_reasoning_quality("   \n   ")
    assert any("empty" in w for w in warnings)


def test_empty_does_not_double_warn():
    """An empty reasoning is one problem — one warning, not stacked
    short+empty+generic warnings."""
    warnings = check_reasoning_quality("")
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Length
# ---------------------------------------------------------------------------


def test_short_reasoning_warns():
    warnings = check_reasoning_quality("too brief")
    assert any("short" in w for w in warnings)


def test_short_reasoning_includes_length():
    warnings = check_reasoning_quality("nope")
    short = next(w for w in warnings if "short" in w)
    assert "4 chars" in short


def test_reasoning_at_min_length_does_not_warn():
    """Edge: exactly REASONING_MIN_LENGTH chars, no generic match."""
    text = "x" * REASONING_MIN_LENGTH
    warnings = check_reasoning_quality(text)
    assert not any("short" in w for w in warnings)


# ---------------------------------------------------------------------------
# Generic patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("placeholder", [
    "user request",
    "USER REQUEST",
    "as requested",
    "per request",
    "done",
    "Done",
    "updated",
    "update",
    "changed",
    "change",
    "fixed",
    "fix",
    "added",
    "removed",
    "n/a",
    "na",
    "none",
    "todo",
    "see diff",
    "see code",
    "see pr",
])
def test_generic_placeholders_warn(placeholder):
    warnings = check_reasoning_quality(placeholder)
    assert any("generic" in w for w in warnings), \
        f"expected generic warning for placeholder={placeholder!r}"


def test_generic_placeholder_with_extra_context_does_not_warn():
    """Only EXACT matches are generic — `done with the migration` is fine."""
    warnings = check_reasoning_quality(
        "done with the migration to support legacy users"
    )
    assert not any("generic" in w for w in warnings)


# ---------------------------------------------------------------------------
# Good reasoning
# ---------------------------------------------------------------------------


def test_good_reasoning_returns_no_warnings():
    warnings = check_reasoning_quality(
        "User asked to add 2FA — needs phone number to send SMS verification codes."
    )
    assert warnings == []


def test_long_descriptive_reasoning_no_warnings():
    warnings = check_reasoning_quality(
        "Added stripe_customer_id to support per-user billing. "
        "Required by the new subscription system shipping next quarter."
    )
    assert warnings == []


# ---------------------------------------------------------------------------
# Public surface invariants
# ---------------------------------------------------------------------------


def test_patterns_constant_is_tuple():
    """Tuple = immutable — accidental mutation would silently change behavior."""
    assert isinstance(GENERIC_REASONING_PATTERNS, tuple)
    assert len(GENERIC_REASONING_PATTERNS) > 0


def test_min_length_is_positive_int():
    assert isinstance(REASONING_MIN_LENGTH, int)
    assert REASONING_MIN_LENGTH > 0
