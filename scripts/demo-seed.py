#!/usr/bin/env python3
"""
Seed a Selvedge database with a believable demo dataset.

Used by ``scripts/record-demo.sh`` to populate ``scripts/demo.selvedge.db``
before vhs records ``docs/demo.gif``. The dataset tells one small story —
a SaaS team adding Stripe billing over ~5 weeks — chosen so that the
four commands shown in the gif each return interesting, on-message
output:

  - ``selvedge status``                   → a populated overview
  - ``selvedge blame payments.amount``    → a concrete ``retype`` event
                                            with strong reasoning
  - ``selvedge diff  payments.amount``    → multi-event history
                                            (CREATE → ADD → RETYPE)
  - ``selvedge search "stripe"``          → many matches across entities

The dataset is fiction. No real customer data, no real keys. Re-running
the script truncates and reseeds — never mutates an existing DB in
place.

Run directly:
    SELVEDGE_DB=scripts/demo.selvedge.db python scripts/demo-seed.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the in-repo selvedge package wins over any installed copy when
# this script is run from the repo root (so dev changes are reflected
# in the demo immediately).
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from selvedge.models import ChangeEvent  # noqa: E402
from selvedge.storage import SelvedgeStorage  # noqa: E402

DB_PATH = Path(
    os.environ.get("SELVEDGE_DB", REPO_ROOT / "scripts" / "demo.selvedge.db")
).resolve()

PROJECT = "freightline-api"
AGENT = "claude-code"
SESSION = "demo-stripe-billing"
CHANGESET = "add-stripe-billing"

# Anchor "now" so the demo's "recent" output stays stable across runs.
# Each event subtracts a delta from this. Using an explicit datetime
# (vs. utc_now_iso) keeps consecutive demo recordings byte-identical
# until you intentionally bump it.
NOW = datetime(2026, 4, 26, 18, 30, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    """Return an ISO-8601 UTC timestamp ``days_ago`` days before NOW."""
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Each row: (days_ago, entity_type, entity_path, change_type, diff, reasoning)
#
# Ordering is reverse-chronological in narrative, but log_event_batch
# preserves whatever timestamps we set. The "headline" event for the
# demo — payments.amount RETYPE — sits ~3 days back so it feels recent
# but not the very last write.
EVENTS: list[tuple[float, str, str, str, str, str]] = [
    # ----- 32d ago: investigating Stripe -----
    (
        32.0, "dependency", "deps/stripe", "add",
        "+ stripe = \"^11.0.0\"",
        "Adding Stripe SDK so the Pro tier ($19/mo) can actually charge. "
        "Picked Stripe over Paddle because we already use their Atlas "
        "for the corp entity and their tax handling is one less thing.",
    ),
    (
        31.8, "env_var", "env/STRIPE_SECRET_KEY", "add",
        "+ STRIPE_SECRET_KEY=sk_live_...",
        "Server-side secret key for charging customers and reading "
        "subscription state. Lives in 1Password vault 'freightline-prod'.",
    ),
    (
        31.7, "env_var", "env/STRIPE_WEBHOOK_SECRET", "add",
        "+ STRIPE_WEBHOOK_SECRET=whsec_...",
        "Per-endpoint signing secret so we can verify webhook payloads "
        "and reject anything that doesn't carry a valid Stripe signature.",
    ),

    # ----- 28d ago: schema -----
    (
        28.0, "table", "payments", "create",
        "CREATE TABLE payments ( id, user_id, amount INTEGER, "
        "stripe_charge_id, created_at )",
        "New payments table records every successful Stripe charge. "
        "user_id FK to users, stripe_charge_id is the source of truth "
        "for reconciliation against Stripe's dashboard.",
    ),
    (
        27.95, "column", "payments.amount", "add",
        "+ amount INTEGER NOT NULL",
        "Stored in cents (INTEGER) following Stripe's convention for "
        "USD. Will revisit if/when we add JPY or other zero-decimal "
        "currencies — those need different handling.",
    ),
    (
        27.9, "column", "payments.stripe_charge_id", "add",
        "+ stripe_charge_id TEXT NOT NULL UNIQUE",
        "UNIQUE so a duplicate webhook delivery (Stripe sends each "
        "event up to 3 times until we 200 it) can't double-record a "
        "charge. INSERT OR IGNORE keys off this column.",
    ),
    (
        27.85, "column", "payments.created_at", "add",
        "+ created_at TEXT NOT NULL",
        "Stripe charge creation time, NOT row insert time — for "
        "billing-period reporting we care when the charge happened "
        "not when our webhook handler ran.",
    ),

    # ----- 21d ago: users table changes -----
    (
        21.0, "column", "users.stripe_customer_id", "add",
        "+ stripe_customer_id TEXT",
        "One Stripe Customer per user. Created lazily on first checkout "
        "(not on signup) so we don't pollute Stripe's dashboard with "
        "free-tier accounts that never convert.",
    ),
    (
        20.5, "column", "users.tier", "add",
        "+ tier TEXT NOT NULL DEFAULT 'free' "
        "CHECK (tier IN ('free','pro'))",
        "Drives feature gating in the UI and rate limits in the API. "
        "CHECK constraint so a typo can't slip a 'pro_v2' value past "
        "the schema.",
    ),

    # ----- 14d ago: webhooks -----
    (
        14.0, "endpoint", "api/v1/stripe/webhooks", "create",
        "POST /api/v1/stripe/webhooks",
        "Receives Stripe's signed webhooks. Handles charge.succeeded, "
        "customer.subscription.updated, customer.subscription.deleted. "
        "Returns 200 fast and queues processing — Stripe will retry "
        "anything slower than 30s.",
    ),
    (
        13.9, "function",
        "src/billing/webhooks.py::handle_charge_succeeded",
        "add",
        "+ def handle_charge_succeeded(event): ...",
        "Inserts a row into payments and bumps users.tier to 'pro' if "
        "the charge corresponds to a Pro subscription. Idempotent on "
        "stripe_charge_id (see UNIQUE constraint above).",
    ),

    # ----- 7d ago: more iteration -----
    (
        7.0, "column", "users.subscription_status", "add",
        "+ subscription_status TEXT",
        "Mirrors Stripe's subscription.status (active, past_due, "
        "canceled, ...). Cheaper than calling the Stripe API every "
        "time we render the billing page.",
    ),
    (
        6.5, "column", "payments.refunded", "add",
        "+ refunded INTEGER NOT NULL DEFAULT 0",
        "Boolean flag set when we receive charge.refunded. Kept as a "
        "flag rather than deleting the row so the historical record "
        "for finance/accounting stays intact.",
    ),
    (
        6.0, "column", "users.tier_v2", "add",
        "+ tier_v2 TEXT",
        "Backwards-compat: keep `tier` as the legacy enum, new code "
        "reads tier_v2 which adds 'team' and 'enterprise'. We'll drop "
        "the legacy column once all reads are migrated.",
    ),

    # ----- 3d ago: THE HEADLINE EVENT for `selvedge blame payments.amount` -----
    (
        3.0, "column", "payments.amount", "retype",
        "- amount INTEGER NOT NULL\n+ amount NUMERIC(12,2) NOT NULL",
        "Switching from cents-as-INTEGER to NUMERIC because Stripe's "
        "Payment Intents API returns Decimal in zero-decimal currencies "
        "(JPY, KWD) and we were silently losing precision converting "
        "twice. New column stores native currency units. Migration "
        "0014 backfills existing USD rows by dividing INTEGER cents by "
        "100; rows for new currencies are written natively from the "
        "webhook handler. amount_currency stays as the source of truth "
        "for unit interpretation.",
    ),

    # ----- 1d ago: recent activity -----
    (
        1.4, "column", "users.email", "modify",
        "- email TEXT UNIQUE\n+ email TEXT UNIQUE CHECK "
        "(email NOT LIKE '%+%@%')",
        "Tightened email validation per finance team — they were "
        "deduping customers whose Stripe Customer email used '+' "
        "subaddressing against ours, which made reconciliation noisy. "
        "Existing rows are grandfathered (no backfill).",
    ),
    (
        1.2, "endpoint", "api/v1/users/me", "modify",
        "+ \"subscription_status\": user.subscription_status",
        "Frontend billing page now needs subscription_status to "
        "render the 'past due' banner without a second round-trip.",
    ),
    (
        0.9, "index", "users.stripe_customer_id", "index_add",
        "CREATE INDEX users_stripe_customer_id_idx "
        "ON users(stripe_customer_id)",
        "Webhook handler does WHERE stripe_customer_id = $1 on every "
        "incoming event. Lookups went 200ms → 4ms after this index "
        "landed; without it the webhook timeout (30s) was getting "
        "scary on the larger orgs.",
    ),

    # ----- a few hours ago: today -----
    (
        0.3, "function", "src/auth.py::login", "modify",
        "+ ensure_stripe_customer(user)",
        "Backfill: if a user predates the stripe_customer_id column, "
        "create their Stripe Customer on next login so the upgrade "
        "flow doesn't 500 when they hit Pricing.",
    ),
    (
        0.05, "config", "config/feature_flags.yaml", "modify",
        "  billing_enabled: false → true",
        "Flipping the global flag now that the migration's settled "
        "for 24h with no Sentry alerts. Pricing page and /billing "
        "route go live with this.",
    ),
]


def seed() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    storage = SelvedgeStorage(DB_PATH)

    events: list[ChangeEvent] = []
    for days_ago, entity_type, entity_path, change_type, diff, reasoning in EVENTS:
        events.append(
            ChangeEvent(
                entity_path=entity_path,
                change_type=change_type,
                entity_type=entity_type,
                diff=diff,
                reasoning=reasoning,
                agent=AGENT,
                session_id=SESSION,
                project=PROJECT,
                changeset_id=CHANGESET,
                timestamp=_ts(days_ago),
                git_commit=f"{abs(hash((entity_path, days_ago))) & 0xFFFFFFF:07x}",
            )
        )

    storage.log_event_batch(events)
    print(f"Seeded {len(events)} events → {DB_PATH}")


if __name__ == "__main__":
    seed()
