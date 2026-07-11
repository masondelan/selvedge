# selvedge-telemetry worker

The receiving end of Selvedge's opt-in heartbeat. Sender, payload
schema, and privacy posture are documented in
[`docs/telemetry.md`](../../docs/telemetry.md) — read that first. This
worker validates schema-v1 pings and writes them to a Workers Analytics
Engine dataset. It stores no IPs, sets no cookies, and has no other
storage.

## Deploy (one time, ~5 minutes)

```bash
cd infra/telemetry-worker
npx wrangler deploy
```

Prereqs on the Cloudflare account (same account as the selvedge.sh
zone):

1. `npx wrangler login` (or `CLOUDFLARE_API_TOKEN` with Workers +
   Analytics Engine edit scopes).
2. A `telemetry` DNS record on selvedge.sh so the route attaches —
   proxied `AAAA` record pointing at `100::` (the standard
   Workers-route placeholder).
3. Analytics Engine enabled on the account (Workers → Analytics Engine
   — free tier is fine).

Verify after deploy:

```bash
curl -s https://telemetry.selvedge.sh/            # transparency text, 200
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST https://telemetry.selvedge.sh/v1/ping \
  -H 'content-type: application/json' \
  -d '{"schema":1,"install_id":"0b6dcb1e-7c58-4a2e-9f39-2f6e3ac0d8a1","version":"0.0.0","python":"3.12","os":"linux","arch":"x86_64","source":"cli"}'
# expect 204
```

## Querying — the north-star KPI

Blob layout (fixed by `worker.js`): `blob1`=version `blob2`=python
`blob3`=os `blob4`=arch `blob5`=source `blob6`=install_id.

Weekly active installs:

```sql
SELECT COUNT(DISTINCT blob6) AS weekly_active_installs
FROM selvedge_telemetry
WHERE timestamp > NOW() - INTERVAL '7' DAY
```

Version mix over the last 30 days:

```sql
SELECT blob1 AS version, COUNT(DISTINCT blob6) AS installs
FROM selvedge_telemetry
WHERE timestamp > NOW() - INTERVAL '30' DAY
GROUP BY blob1
ORDER BY installs DESC
```

Run via the SQL API:

```bash
curl -s "https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/analytics_engine/sql" \
  -H "Authorization: Bearer <API_TOKEN>" \
  -d "SELECT COUNT(DISTINCT blob6) FROM selvedge_telemetry WHERE timestamp > NOW() - INTERVAL '7' DAY"
```

Caveats: Analytics Engine samples under load (weight in
`_sample_interval`), so at Selvedge's volumes counts are exact but the
query recipes should be re-checked if volume ever makes sampling kick
in; retention is ~90 days, so snapshot the weekly number somewhere
durable if you want history past that window.

## Change discipline

A payload change is a three-place change: `selvedge/telemetry.py`
(sender), this worker (receiver), `docs/telemetry.md` (disclosure) —
plus a `PAYLOAD_SCHEMA_VERSION` bump. Never widen silently.
