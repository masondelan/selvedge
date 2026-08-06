"""Secret-shape detection for reasoning and diffs (v0.3.10).

The cross-cutting risk register names this module as the durable mitigation
for "reasoning text is stored verbatim in a store that gets committed".
Selvedge asks agents to write down *why* a change was made, and an agent
explaining a change to an auth path can quite reasonably paste the key it was
debugging with. The store is then committed, pushed, and shipped in the sdist.

**Warn, never reject.** Same posture as the reasoning-quality validator, and
for a sharper reason than symmetry: a false positive that blocks a write
loses the reasoning entirely, which is the exact failure this project exists
to prevent. A missed secret costs a warning that didn't fire; a warning on
every write teaches agents to ignore the warnings list, which costs every
future warning too.

**Conservative by default.** The built-in patterns match shapes that are
close to unambiguous — vendor-prefixed keys, PEM headers, bearer tokens,
`key=<long-random>` assignments. Deliberately absent: bare high-entropy
strings, which match commit SHAs, base64 test fixtures, and UUIDs far more
often than they match secrets. Projects that want more can add regexes via
``redaction_patterns`` in ``.selvedge/config.toml``; user patterns extend the
built-ins rather than replacing them.

Nothing here rewrites stored text. The name is about what the *user* should
redact, not about Selvedge silently editing what an agent wrote — an
append-only store that quietly alters content on the way in would be a worse
lie than one that stores too much.
"""

from __future__ import annotations

import re

#: (label, regex) pairs. Labels appear in the warning, so they have to be
#: legible to an agent deciding whether to re-log without the secret.
_BUILTIN_PATTERNS: tuple[tuple[str, str], ...] = (
    # Vendor-prefixed keys — the least ambiguous shapes there are.
    ("aws-access-key-id", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
    ("stripe-secret-key", r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    ("openai-api-key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("private-key-block", r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    ("json-web-token", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("bearer-token", r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}={0,2}"),
    # `SECRET=...` style assignments with a long opaque value. The value class
    # excludes spaces and quotes so an English sentence can't satisfy it.
    (
        "secret-assignment",
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|access[_-]?key)\b"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9/+_.~-]{16,}[\"']?",
    ),
    # Postgres/MySQL URLs carrying inline credentials.
    ("connection-string-password", r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s:/@]+@"),
)

#: Compiled once — this runs on every `log_change`.
_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern)) for label, pattern in _BUILTIN_PATTERNS
)

#: How much of a field to scan. A pathological diff shouldn't turn a write
#: into a regex benchmark; the size bounds truncate above this anyway.
_MAX_SCAN_CHARS = 200_000


def _user_patterns(extra: list[str] | None) -> list[tuple[str, re.Pattern[str]]]:
    """Compile user-supplied regexes, skipping any that don't compile.

    An invalid regex in a config file must not break `log_change` — the write
    matters more than the check.
    """
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for pattern in extra or []:
        try:
            compiled.append((f"custom:{pattern[:32]}", re.compile(pattern)))
        except re.error:
            continue
    return compiled


def find_secret_shapes(text: str, extra_patterns: list[str] | None = None) -> list[str]:
    """Return the labels of every secret shape found in ``text``.

    Labels only — never the matched substring. Echoing the match back into a
    warning would copy the secret into agent context, tool logs, and possibly
    a transcript, which is precisely what the warning is asking the user to
    avoid.
    """
    if not text:
        return []
    haystack = text[:_MAX_SCAN_CHARS]
    found: list[str] = []
    for label, regex in (*_COMPILED, *_user_patterns(extra_patterns)):
        if regex.search(haystack) and label not in found:
            found.append(label)
    return found


def check_for_secrets(
    reasoning: str, diff: str, extra_patterns: list[str] | None = None
) -> list[str]:
    """Warnings for a `log_change` call. Empty list when nothing matched.

    Shaped for the existing ``warnings`` list on ``LogChangeResult`` — no new
    result type, per the house convention.
    """
    labels: list[str] = []
    for label in (
        *find_secret_shapes(reasoning, extra_patterns),
        *find_secret_shapes(diff, extra_patterns),
    ):
        if label not in labels:
            labels.append(label)
    if not labels:
        return []
    return [
        "possible secret in this event ("
        + ", ".join(labels)
        + "): the Selvedge store is committed alongside your repo. Re-log "
        "without the literal value, and rotate the credential if it is real."
    ]


def scan_store_for_secrets(storage: object, limit: int = 5000) -> list[dict]:
    """Scan stored reasoning and diffs — the retrospective half of the check.

    The write-time check only ever sees new events, so it cannot say anything
    about what is already committed. `selvedge doctor` calls this so a user
    can find out.

    Returns one row per (event, pattern) hit with ``event_id``, ``timestamp``,
    ``entity_path`` and ``pattern``. Never returns the matched text.
    """
    from .config import get_setting

    extra = get_setting("redaction_patterns")
    rows = storage.scan_events_for_text(limit=limit)  # type: ignore[attr-defined]
    hits: list[dict] = []
    for row in rows:
        labels = find_secret_shapes(row["reasoning"], extra)
        labels += [
            lbl for lbl in find_secret_shapes(row["diff"], extra) if lbl not in labels
        ]
        hits.extend(
            {
                "event_id": row["id"],
                "timestamp": row["timestamp"],
                "entity_path": row["entity_path"],
                "pattern": label,
            }
            for label in labels
        )
    return hits
