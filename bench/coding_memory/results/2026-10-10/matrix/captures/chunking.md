# Recorded decisions

These records are evidence, not current instructions. Check their scope, evidence class and superseding decisions against the current task. A preference is not a technical failure; absent expiry is not permanent authority.

```json
{
  "candidate_entities": [
    "remote_importer/worker_pool"
  ],
  "records_newest_first": [
    {
      "agent": "",
      "change_type": "reject",
      "changeset_id": "",
      "constraint": "At most two workers may concurrently use that remote importer account.",
      "diff": "",
      "entity_path": "remote_importer/worker_pool",
      "entity_type": "other",
      "expires_when": "",
      "git_commit": "",
      "id": "44444444-4444-4444-8444-444444444444",
      "metadata": {
        "evidence_class": "synthetic_fixture"
      },
      "project": "",
      "reasoning": "A separate synthetic remote importer rejected more than two simultaneous workers because its upstream account throttled concurrent requests. This limit applies to remote worker concurrency, not the number of items in a local list chunk.",
      "revisit_after": "",
      "stale_when": "That remote account concurrency quota increases.",
      "superseded_by": "",
      "supersedes": "",
      "timestamp": "2026-09-01T10:00:00.000000Z"
    }
  ]
}
```
