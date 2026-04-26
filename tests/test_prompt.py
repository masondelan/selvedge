"""
Tests for selvedge.prompt — the canonical agent-instructions block plus
its idempotent installer.

Coverage focus:
  - Pure-output: ``render_block`` returns sentinel-bracketed content
    that round-trips through the regex.
  - Greenfield install: writes parent dirs, no backup written.
  - Append path: existing content preserved, blank-line separator
    inserted, ``.bak`` written.
  - Update path: only the bracketed region changes; surrounding text
    is byte-identical; ``.bak`` written before modification.
  - Idempotence: byte-equal block ⇒ ``"unchanged"`` and no ``.bak``.
  - Backup numbering: subsequent installs that need a backup don't
    overwrite the prior ``.bak``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selvedge.prompt import (
    _BLOCK_RE,
    PROMPT_BLOCK,
    SENTINEL_END,
    SENTINEL_START,
    install_to_file,
    render_block,
)

# ---------------------------------------------------------------------------
# Pure rendering
# ---------------------------------------------------------------------------


def test_render_block_starts_and_ends_with_sentinels():
    block = render_block()
    assert block.startswith(SENTINEL_START)
    assert block.endswith(SENTINEL_END)


def test_render_block_contains_full_prompt():
    block = render_block()
    # The whole prompt body should be in there — guards against accidentally
    # stripping content during refactors.
    assert PROMPT_BLOCK.strip() in block


def test_block_regex_round_trips():
    block = render_block()
    surrounded = f"# README\n\n{block}\n\nmore content\n"
    match = _BLOCK_RE.search(surrounded)
    assert match is not None
    assert match.group(0) == block


# ---------------------------------------------------------------------------
# install_to_file — happy paths
# ---------------------------------------------------------------------------


def test_install_creates_file_when_missing(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / "CLAUDE.md"

    action, backup = install_to_file(target)

    assert action == "created"
    assert backup is None
    # Parent dirs were created (mkdir -p semantics)
    assert target.parent.is_dir()
    contents = target.read_text()
    assert SENTINEL_START in contents
    assert SENTINEL_END in contents


def test_install_appends_when_no_existing_block(tmp_path: Path):
    target = tmp_path / "CLAUDE.md"
    original = "# My project\n\nSome other content here.\n"
    target.write_text(original)

    action, backup = install_to_file(target)

    assert action == "appended"
    assert backup is not None and backup.exists()
    # Backup carries the pre-modification contents byte-for-byte
    assert backup.read_text() == original

    contents = target.read_text()
    # Original content preserved
    assert "# My project" in contents
    assert "Some other content here." in contents
    # Block is at the end, sentinel-bracketed
    assert contents.rstrip().endswith(SENTINEL_END)


def test_install_updates_existing_block_in_place(tmp_path: Path):
    """An old version of the block should be replaced; surrounding text untouched."""
    target = tmp_path / "CLAUDE.md"
    old_block = (
        f"{SENTINEL_START}\n"
        "## OLD selvedge instructions\n"
        "These are out of date.\n"
        f"{SENTINEL_END}"
    )
    original = f"# Project\n\nIntro.\n\n{old_block}\n\nFooter line.\n"
    target.write_text(original)

    action, backup = install_to_file(target)

    assert action == "updated"
    assert backup is not None
    assert backup.read_text() == original

    updated = target.read_text()
    # Old content gone
    assert "OLD selvedge" not in updated
    # New content present
    assert PROMPT_BLOCK.strip() in updated
    # Surrounding text untouched byte-for-byte
    assert "# Project" in updated
    assert "Intro." in updated
    assert "Footer line." in updated


def test_install_is_idempotent(tmp_path: Path):
    """Re-running on a file with the current block is a true no-op."""
    target = tmp_path / "CLAUDE.md"

    install_to_file(target)
    assert (target).exists()

    # Second call on the same content — should report unchanged and
    # NOT write a new backup.
    action, backup = install_to_file(target)

    assert action == "unchanged"
    assert backup is None
    # No .bak created on the unchanged path
    assert not (tmp_path / "CLAUDE.md.bak").exists()


# ---------------------------------------------------------------------------
# Backup numbering
# ---------------------------------------------------------------------------


def test_subsequent_modifications_dont_overwrite_first_backup(tmp_path: Path):
    """Two real edits should leave both .bak files behind."""
    target = tmp_path / "CLAUDE.md"
    first = f"# v1\n\n{SENTINEL_START}\nold one\n{SENTINEL_END}\n"
    target.write_text(first)

    # First update — produces .bak
    _, first_backup = install_to_file(target)
    assert first_backup is not None
    assert first_backup.name == "CLAUDE.md.bak"

    # Force another change so install_to_file has to write again.
    second = f"# v2\n\n{SENTINEL_START}\nstale\n{SENTINEL_END}\n"
    target.write_text(second)

    _, second_backup = install_to_file(target)

    assert second_backup is not None
    # The original .bak should still exist with its original contents
    assert first_backup.exists()
    assert first_backup.read_text() == first
    # Second backup got a numeric suffix
    assert second_backup != first_backup
    assert second_backup.read_text() == second


def test_install_no_backup_flag_skips_bak_write(tmp_path: Path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("# Project\n\nintro.\n")

    action, backup = install_to_file(target, write_backup=False)

    assert action == "appended"
    assert backup is None
    assert not (tmp_path / "CLAUDE.md.bak").exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_install_into_empty_existing_file(tmp_path: Path):
    target = tmp_path / "CLAUDE.md"
    target.write_text("")

    action, backup = install_to_file(target)

    assert action == "appended"
    # An empty file gets backed up too — "user can always undo."
    assert backup is not None
    contents = target.read_text()
    assert SENTINEL_START in contents


def test_install_handles_block_with_leading_trailing_whitespace(tmp_path: Path):
    """Block detection shouldn't be foiled by stray whitespace around sentinels."""
    target = tmp_path / "CLAUDE.md"
    weird_existing = (
        "# Project\n"
        "\n"
        "   " + SENTINEL_START + "   \n"
        "old content\n"
        "  " + SENTINEL_END + "  \n"
        "\n"
        "Footer.\n"
    )
    target.write_text(weird_existing)

    action, _ = install_to_file(target)

    # Sentinels should still match because the regex anchors on the
    # literal markers, not their surrounding whitespace.
    assert action == "updated"
    contents = target.read_text()
    assert "old content" not in contents


@pytest.mark.parametrize(
    "scenario",
    [
        "no trailing newline",
        "single trailing newline",
        "double trailing newline",
    ],
)
def test_install_preserves_trailing_newline_convention(
    tmp_path: Path, scenario: str
):
    """Don't accidentally squash existing trailing-newline conventions."""
    target = tmp_path / "CLAUDE.md"

    body = "# Project\n\nintro."
    if scenario == "single trailing newline":
        body += "\n"
    elif scenario == "double trailing newline":
        body += "\n\n"

    target.write_text(body)
    install_to_file(target)
    final = target.read_text()
    # In all scenarios the file should end with exactly one newline
    # after the closing sentinel.
    assert final.endswith(SENTINEL_END + "\n")
