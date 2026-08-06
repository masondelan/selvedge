"""The canonical precedence chain for `.selvedge/config.toml` settings.

    CLI flag > env var > project config.toml > global config.toml > default

with one deliberate exception: `SELVEDGE_DB` always wins for DB-path
resolution, because the config file that would override it is found *by*
resolving that path.

Every later phase that adds a config setting extends this file rather than
adding a one-off check somewhere else — the chart is the contract, and it is
asserted step by step below.
"""

from __future__ import annotations

import pytest

from selvedge import config as cfg


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose .selvedge/ is the resolved DB's parent, and a fake HOME."""
    home = tmp_path / "home"
    (home / ".selvedge").mkdir(parents=True)
    monkeypatch.setattr(cfg.Path, "home", staticmethod(lambda: home))

    proj = tmp_path / "proj" / ".selvedge"
    proj.mkdir(parents=True)
    monkeypatch.setenv("SELVEDGE_DB", str(proj / "selvedge.db"))
    monkeypatch.setenv("SELVEDGE_QUIET", "1")
    for spec in cfg.SETTINGS.values():
        monkeypatch.delenv(spec.env, raising=False)
    return proj


def _write(path, body: str):
    path.joinpath("config.toml").write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Each step of the chain wins where it should
# ---------------------------------------------------------------------------


def test_default_when_nothing_is_configured(project):
    resolved = cfg.resolve_setting("diff_bytes")
    assert resolved == (65536, "default")


def test_global_config_beats_the_default(project, tmp_path):
    _write(tmp_path / "home" / ".selvedge", "diff_bytes = 111\n")
    assert cfg.resolve_setting("diff_bytes") == (111, "global")


def test_project_config_beats_the_global(project, tmp_path):
    _write(tmp_path / "home" / ".selvedge", "diff_bytes = 111\n")
    _write(project, "diff_bytes = 222\n")
    assert cfg.resolve_setting("diff_bytes") == (222, "project")


def test_env_beats_the_project_config(project, monkeypatch, tmp_path):
    _write(project, "diff_bytes = 222\n")
    monkeypatch.setenv("SELVEDGE_DIFF_BYTES", "333")
    assert cfg.resolve_setting("diff_bytes") == (333, "env")


def test_flag_beats_everything(project, monkeypatch):
    _write(project, "diff_bytes = 222\n")
    monkeypatch.setenv("SELVEDGE_DIFF_BYTES", "333")
    assert cfg.resolve_setting("diff_bytes", flag_value=444) == (444, "flag")


def test_absent_flag_does_not_shadow_lower_steps(project):
    """`flag_value=None` means "not passed", not "set to nothing"."""
    _write(project, "diff_bytes = 222\n")
    assert cfg.resolve_setting("diff_bytes", flag_value=None) == (222, "project")


# ---------------------------------------------------------------------------
# The SELVEDGE_DB exception
# ---------------------------------------------------------------------------


def test_config_cannot_override_the_db_path_env_var(project, tmp_path):
    """The one documented exception to the chain.

    A config file cannot redirect the database, because Selvedge has to
    resolve the database path in order to find the config file in the first
    place. Encoding that as a rule rather than an accident.
    """
    _write(project, 'db_path = "/somewhere/else.db"\nselvedge_db = "/elsewhere.db"\n')
    resolved = cfg.resolve_db_path()
    assert resolved.source == "env"
    assert resolved.path == (project / "selvedge.db").resolve()
    # And no setting exists that could be mistaken for one that would.
    assert "db_path" not in cfg.SETTINGS
    assert "selvedge_db" not in cfg.SETTINGS


# ---------------------------------------------------------------------------
# Degrading rather than raising
# ---------------------------------------------------------------------------


def test_unparseable_config_falls_through_to_the_default(project):
    _write(project, "this is not = = valid toml [[[\n")
    assert cfg.resolve_setting("diff_bytes") == (65536, "default")


def test_wrong_typed_value_falls_through_to_the_next_step(project, tmp_path):
    """A bad project value must not shadow a good global one."""
    _write(tmp_path / "home" / ".selvedge", "diff_bytes = 111\n")
    _write(project, 'diff_bytes = "not a number"\n')
    assert cfg.resolve_setting("diff_bytes") == (111, "global")


def test_negative_value_is_rejected(project):
    _write(project, "diff_bytes = -1\n")
    assert cfg.resolve_setting("diff_bytes") == (65536, "default")


def test_missing_config_file_is_not_an_error(project):
    assert not (project / "config.toml").exists()
    assert cfg.resolve_all_settings()["backup_keep_last"].source == "default"


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_every_setting_resolves_and_declares_a_unique_env_var():
    envs = [spec.env for spec in cfg.SETTINGS.values()]
    assert len(envs) == len(set(envs)), f"duplicate env var among {envs}"
    for name, spec in cfg.SETTINGS.items():
        assert spec.env.startswith("SELVEDGE_")
        assert spec.help.strip(), f"{name} has no help text"


def test_retention_days_events_defaults_to_never_deleting():
    """The default that matters most: never lose captured reasoning."""
    assert cfg.SETTINGS["retention_days_events"].default == cfg.UNLIMITED
    assert cfg.UNLIMITED == 0


def test_string_list_setting_accepts_toml_list_and_env_csv(project, monkeypatch):
    _write(project, 'redaction_patterns = ["foo", "bar"]\n')
    assert cfg.resolve_setting("redaction_patterns") == (["foo", "bar"], "project")

    monkeypatch.setenv("SELVEDGE_REDACTION_PATTERNS", "baz, qux ,")
    assert cfg.resolve_setting("redaction_patterns") == (["baz", "qux"], "env")


def test_list_default_is_not_shared_between_callers():
    """A mutable default returned by reference would leak across calls."""
    first = cfg.get_setting("redaction_patterns")
    first.append("leaked")
    assert cfg.get_setting("redaction_patterns") == []
