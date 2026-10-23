"""
Tests for reset_zen.py

Covers:
  Fix — Reset works on Zen 1.18+ profiles without zen_pins / zen_workspaces tables
  Fix — Reset says that zen-sessions.jsonlz4 (Zen 1.18+ sidebar) is not reset
"""
import sqlite3

import pytest

from reset_zen import ZenResetter


def _make_places_db(profile, legacy_zen_tables: bool) -> None:
    conn = sqlite3.connect(profile / "places.sqlite")
    conn.executescript("""
        CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, visit_count INTEGER);
        CREATE TABLE moz_historyvisits (id INTEGER PRIMARY KEY, place_id INTEGER);
        CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO moz_bookmarks (id, title) VALUES (1, 'root'), (6, 'Example');
    """)
    if legacy_zen_tables:
        conn.executescript("""
            CREATE TABLE zen_pins (uuid TEXT, title TEXT);
            CREATE TABLE zen_workspaces (uuid TEXT, name TEXT);
            INSERT INTO zen_pins VALUES ('pin-1', 'Example');
            INSERT INTO zen_workspaces VALUES ('ws-1', 'Default'), ('ws-2', 'Imported');
        """)
    conn.commit()
    conn.close()


def _rows(profile, sql):
    conn = sqlite3.connect(profile / "places.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def run_reset(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.chdir(tmp_path)  # backups/ is written to the working directory
    monkeypatch.setattr("builtins.input", lambda _prompt: "RESET")

    def _run(legacy_zen_tables: bool, zen_sessions: bool = False, dry_run: bool = False) -> bool:
        _make_places_db(profile, legacy_zen_tables)
        if zen_sessions:
            (profile / "zen-sessions.jsonlz4").write_bytes(b"mozLz40\0")
        return ZenResetter().reset_zen_profile(profile, dry_run=dry_run)

    return profile, _run


def test_reset_without_legacy_zen_tables(run_reset):
    """Zen 1.18+ profiles have no zen_pins / zen_workspaces; reset must still succeed."""
    profile, run = run_reset
    assert run(legacy_zen_tables=False) is True
    assert _rows(profile, "SELECT id FROM moz_bookmarks") == [(1,)]


def test_reset_clears_legacy_zen_tables(run_reset):
    profile, run = run_reset
    assert run(legacy_zen_tables=True) is True
    assert _rows(profile, "SELECT * FROM zen_pins") == []
    assert _rows(profile, "SELECT name FROM zen_workspaces") == [("Default",)]


def test_reset_warns_that_zen_sessions_is_kept(run_reset, capsys):
    """Zen 1.18+ keeps the sidebar in zen-sessions.jsonlz4; don't claim a fresh state."""
    profile, run = run_reset
    assert run(legacy_zen_tables=False, zen_sessions=True) is True
    out = capsys.readouterr().out
    before_prompt, after_prompt = out.split("🧹 Resetting profile...")
    assert "zen-sessions.jsonlz4 is NOT reset" in before_prompt
    assert "zen-sessions.jsonlz4 is NOT reset" in after_prompt
    assert "fresh state" not in out
    assert (profile / "zen-sessions.jsonlz4").exists()


def test_dry_run_warns_that_zen_sessions_is_kept(run_reset, capsys):
    _, run = run_reset
    assert run(legacy_zen_tables=False, zen_sessions=True, dry_run=True) is True
    assert "zen-sessions.jsonlz4 is NOT reset" in capsys.readouterr().out


def test_reset_without_zen_sessions_is_fresh(run_reset, capsys):
    _, run = run_reset
    assert run(legacy_zen_tables=True) is True
    out = capsys.readouterr().out
    assert "fresh state" in out
    assert "NOT reset" not in out
