"""SEL-001: superseded decisions must not surface via get_stale_decisions.

SessionStart section 1 ("Decisions due for a revisit") reads
``get_stale_decisions``. A reject/revert later re-opened by ``supersede``
must drop out of that list — even when its ``expires_when`` has fired,
``revisit_after`` is due, or ``stale_when`` matched.

The filter is the issue #30 id-link / auto-link rule, not a path+time
"any later supersede on this path" cut. A same-path sibling the
supersede did not target still surfaces.

Does not claim product-benefit proof. Pins the delivery filter only.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selvedge.hooks.sessionstart import build_digest
from selvedge.models import ChangeEvent
from selvedge.storage import SelvedgeStorage


@pytest.fixture
def storage(tmp_path: Path) -> SelvedgeStorage:
    return SelvedgeStorage(tmp_path / "stale-supersede.db")


_NOW = "2026-12-31T00:00:00Z"
_DIGEST_CAP = 8000
_PATH = "payments.retry_queue"


def _revisit_section(digest: str) -> str:
    """The SessionStart 'Decisions due for a revisit' block, or empty."""
    marker = "Decisions due for a revisit:"
    if marker not in digest:
        return ""
    rest = digest.split(marker, 1)[1]
    return rest.split("\n\n", 1)[0]


def _expired_reject(storage: SelvedgeStorage, path: str, ts: str, reasoning: str) -> ChangeEvent:
    return storage.log_event(
        ChangeEvent(
            entity_path=path,
            change_type="reject",
            timestamp=ts,
            expires_when="date:2026-06-01",
            reasoning=reasoning,
        )
    )


def test_superseded_expired_reject_drops_from_stale_and_digest(storage):
    """A supersede re-opening an expired reject removes it from both surfaces."""
    rejected = _expired_reject(
        storage,
        _PATH,
        "2026-01-05T00:00:00Z",
        "Rejected: at-least-once delivery already covers retries.",
    )
    assert [r["id"] for r in storage.get_stale_decisions(now=_NOW)] == [rejected.id]
    digest_before = build_digest(storage.db_path, _DIGEST_CAP)
    assert _PATH in _revisit_section(digest_before)

    storage.log_supersede(
        _PATH,
        supersedes=rejected.id,
        reasoning="Constraint lifted; retries re-opened.",
    )

    assert storage.get_stale_decisions(now=_NOW) == []
    digest = build_digest(storage.db_path, _DIGEST_CAP)
    assert _PATH not in _revisit_section(digest)
    assert _PATH not in digest


def test_standing_expired_reject_still_surfaces(storage):
    """Expired-but-not-superseded still surfaces — explicit supersede required."""
    rejected = _expired_reject(
        storage,
        _PATH,
        "2026-01-05T00:00:00Z",
        "Rejected: at-least-once delivery already covers retries.",
    )
    rows = storage.get_stale_decisions(now=_NOW)
    assert [r["id"] for r in rows] == [rejected.id]
    assert rows[0]["flag"] == "expired"

    digest = build_digest(storage.db_path, _DIGEST_CAP)
    assert _PATH in _revisit_section(digest)


def test_new_reject_after_supersede_still_surfaces_when_fired(storage):
    """Superseding the first reject does not hide a later, still-standing one."""
    first = _expired_reject(
        storage,
        _PATH,
        "2026-01-05T00:00:00Z",
        "Rejected the first time.",
    )
    storage.log_supersede(
        _PATH,
        supersedes=first.id,
        reasoning="Re-opened after the first constraint lifted.",
    )
    later = _expired_reject(
        storage,
        _PATH,
        "2026-03-01T00:00:00Z",
        "Rejected again after a new attempt.",
    )

    rows = storage.get_stale_decisions(now=_NOW)
    assert [r["id"] for r in rows] == [later.id]
    digest = build_digest(storage.db_path, _DIGEST_CAP)
    assert _PATH in _revisit_section(digest)
    assert "Rejected again after a new attempt." in digest
    assert "Rejected the first time." not in _revisit_section(digest)


def test_unrelated_same_path_revisit_sibling_still_surfaces(storage):
    """ID-link / auto-link must not drop an earlier untargeted sibling.

    Path+time ("any later supersede on this path") would hide the earlier
    ``revisit_after`` reject. The #30 rule auto-links only the latest prior
    removal, so the sibling stays a live revisit nudge.
    """
    sibling = storage.log_event(
        ChangeEvent(
            entity_path=_PATH,
            change_type="reject",
            timestamp="2026-01-01T00:00:00Z",
            revisit_after="2020-01-01",
            reasoning="Earlier standing revisit — not the supersede target.",
        )
    )
    storage.record_tool_call("prior_attempts", entity_path=_PATH)
    target = _expired_reject(
        storage,
        _PATH,
        "2026-01-15T00:00:00Z",
        "Later reject that the id-less supersede auto-links.",
    )
    # Hand-logged id-less supersede — the shape importers and pre-v0.3.9.1
    # rows have; log_supersede would have written the auto-linked id.
    storage.log_event(
        ChangeEvent(
            entity_path=_PATH,
            change_type="supersede",
            timestamp="2026-02-01T00:00:00Z",
            reasoning="Re-opening the later rejection only.",
        )
    )

    rows = storage.get_stale_decisions(now=_NOW)
    ids = {r["id"] for r in rows}
    assert sibling.id in ids, (
        "untargeted revisit_after sibling must still surface under the "
        "id-link / auto-link filter"
    )
    assert target.id not in ids, "auto-linked expired reject must drop out"

    digest = build_digest(storage.db_path, _DIGEST_CAP)
    section = _revisit_section(digest)
    assert _PATH in section
    assert "not the supersede target" in section
    assert "Later reject that the id-less supersede auto-links." not in section


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
