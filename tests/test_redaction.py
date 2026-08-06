"""Secret-shape warnings at log_change time (v0.3.10).

Closes the cross-cutting risk register's "reasoning text is stored verbatim
in a store that gets committed" entry. The posture under test throughout:
warn, never reject, and never echo the matched value back.
"""

from __future__ import annotations

import pytest

from selvedge.redaction import check_for_secrets, find_secret_shapes
from selvedge.storage import SelvedgeStorage


@pytest.fixture(autouse=True)
def _fresh_server_storage():
    import selvedge.server as srv
    srv._storage = None
    yield
    srv._storage = None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("aws-access-key-id", "used AKIAIOSFODNN7EXAMPLE to reproduce"),
        ("github-token", "token ghp_" + "a" * 36 + " in the env"),
        ("stripe-secret-key", "sk_live_" + "b" * 24),
        ("google-api-key", "AIza" + "C" * 35),
        ("private-key-block", "-----BEGIN RSA PRIVATE KEY-----"),
        ("bearer-token", "Authorization: Bearer " + "d" * 40),
        ("secret-assignment", 'API_KEY="' + "e" * 32 + '"'),
        ("connection-string-password", "postgres://admin:hunter2@db.internal/app"),
    ],
)
def test_builtin_shapes_are_detected(label, text):
    assert label in find_secret_shapes(text)


@pytest.mark.parametrize(
    "text",
    [
        "Reverted because the auth token approach put us in PCI scope.",
        "Bumped the retry limit from 3 to 5 after the timeout incident.",
        "Renamed users.email to users.primary_email for clarity.",
        "See commit 4f9a2c1e8b7d6a5f3c2e1d0b9a8f7e6d5c4b3a29 for context.",
        "The password field is now hashed with argon2id.",
        "Set the API key in the environment, never in source.",
        "",
    ],
)
def test_ordinary_reasoning_does_not_warn(text):
    """False positives are the expensive failure here.

    A warning that fires on every write teaches agents to ignore the warnings
    list, which costs every future warning too — including the ones that
    matter. These are all sentences a real agent would plausibly write.
    """
    assert find_secret_shapes(text) == [], f"false positive on: {text!r}"


def test_the_matched_value_is_never_echoed_back():
    """Labels only. Echoing the match copies the secret into agent context."""
    secret = "AKIAIOSFODNN7EXAMPLE"
    warnings = check_for_secrets(f"debugging with {secret}", "")
    assert warnings
    assert secret not in " ".join(warnings)
    assert "aws-access-key-id" in " ".join(warnings)


def test_duplicate_labels_are_reported_once():
    text = "AKIAIOSFODNN7EXAMPLE and also AKIAJJJJJJJJJJJJJJJJ"
    assert find_secret_shapes(text).count("aws-access-key-id") == 1


def test_user_patterns_extend_rather_than_replace_the_builtins():
    text = "internal marker ACME-9999 next to AKIAIOSFODNN7EXAMPLE"
    labels = find_secret_shapes(text, [r"ACME-\d{4}"])
    assert "aws-access-key-id" in labels
    assert any(lbl.startswith("custom:") for lbl in labels)


def test_an_invalid_user_pattern_is_skipped_not_raised():
    """A broken regex in config.toml must not break the write path."""
    assert find_secret_shapes("AKIAIOSFODNN7EXAMPLE", ["(unclosed"]) == [
        "aws-access-key-id"
    ]


# ---------------------------------------------------------------------------
# Wired into log_change — warn, never reject
# ---------------------------------------------------------------------------


def test_log_change_warns_but_still_logs():
    from selvedge.server import log_change

    result = log_change(
        entity_path="src/auth.py",
        change_type="modify",
        reasoning="Reproduced with AKIAIOSFODNN7EXAMPLE before switching to STS.",
    )
    assert result["status"] == "logged", "the secret check must never reject a write"
    assert result["id"]
    assert any("possible secret" in w for w in result["warnings"])


def test_log_change_checks_the_diff_too():
    from selvedge.server import log_change

    result = log_change(
        entity_path="config/app.env", change_type="modify",
        reasoning="Rotated the key.",
        diff='STRIPE_KEY=sk_live_' + "f" * 24,
    )
    assert any("possible secret" in w for w in result["warnings"])


def test_clean_log_change_has_no_secret_warning():
    from selvedge.server import log_change

    result = log_change(
        entity_path="users.email", change_type="add",
        reasoning="Added an email column so password reset has somewhere to send.",
    )
    assert not [w for w in result["warnings"] if "possible secret" in w]


def test_doctor_scans_what_is_already_stored(tmp_path, monkeypatch):
    """The retrospective half — the write-time check can't see old events."""
    from selvedge.diagnostics import run_checks

    db = tmp_path / ".selvedge" / "selvedge.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv("SELVEDGE_DB", str(db))
    from selvedge.models import ChangeEvent
    SelvedgeStorage(db).log_event(ChangeEvent(
        entity_path="src/auth.py", change_type="modify",
        reasoning="left AKIAIOSFODNN7EXAMPLE in here months ago",
    ))

    rows = {c["label"]: c for c in run_checks()}
    assert rows["Secret-shaped content"]["status"] == "WARN"
    assert "aws-access-key-id" in rows["Secret-shaped content"]["detail"]
    assert "AKIAIOSFODNN7EXAMPLE" not in rows["Secret-shaped content"]["detail"]


def test_doctor_passes_on_a_clean_store(tmp_path, monkeypatch):
    from selvedge.diagnostics import run_checks
    from selvedge.models import ChangeEvent

    db = tmp_path / ".selvedge" / "selvedge.db"
    db.parent.mkdir(parents=True)
    monkeypatch.setenv("SELVEDGE_DB", str(db))
    SelvedgeStorage(db).log_event(ChangeEvent(
        entity_path="users.email", change_type="add",
        reasoning="Added for password reset.",
    ))

    rows = {c["label"]: c for c in run_checks()}
    assert rows["Secret-shaped content"]["status"] == "PASS"
