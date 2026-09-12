"""An isolated, deterministic first-use demonstration of decision memory."""

from pathlib import Path
from tempfile import TemporaryDirectory

from .models import ChangeEvent
from .storage import SelvedgeStorage


def run_demo() -> dict:
    """Persist a rejection, reopen the store and retrieve it as a new session."""
    reasoning = "Rejected storing API keys in users.api_key: plaintext credentials would be exposed in a database leak. Store a hash instead."
    condition = "The credential storage and threat model change."
    with TemporaryDirectory(prefix="selvedge-demo-") as directory:
        path = Path(directory) / "demo.db"
        first = SelvedgeStorage(path)
        first.log_event(ChangeEvent(
            entity_path="users.api_key", entity_type="column", change_type="reject",
            reasoning=reasoning, stale_when=condition, agent="demo-agent", session_id="session-1",
        ))
        second = SelvedgeStorage(path)
        attempts = second.get_prior_attempts(entity_path="users.api_key")
        row = attempts[0]
        return {
            "entity_path": "users.api_key", "reasoning": reasoning,
            "outcome": row["outcome"], "confidence": row["confidence"],
            "stale_when": condition, "isolated": True,
        }
