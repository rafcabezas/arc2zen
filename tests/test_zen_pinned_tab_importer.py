"""
Tests for zen_pinned_tab_importer.py

Covers:
  Bug 6 — tab_exists() must be scoped per workspace_uuid, not globally
"""
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Minimal SQLite DB that looks like Zen's places.sqlite
# ---------------------------------------------------------------------------

def _create_places_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE zen_pins (
                uuid         TEXT PRIMARY KEY,
                title        TEXT,
                url          TEXT,
                container_id INTEGER,
                workspace_uuid TEXT,
                position     INTEGER,
                is_essential INTEGER DEFAULT 0,
                is_group     INTEGER DEFAULT 0,
                parent_uuid  TEXT,
                created_at   INTEGER,
                updated_at   INTEGER,
                edited_title INTEGER DEFAULT 0,
                arc_tab_id   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE zen_pins_changes (uuid TEXT PRIMARY KEY, timestamp INTEGER)
        """)
        conn.commit()


@pytest.fixture()
def places_db(tmp_path: Path) -> Path:
    db = tmp_path / "places.sqlite"
    _create_places_db(db)
    return db


@pytest.fixture()
def importer(tmp_path: Path, places_db: Path):
    from zen_pinned_tab_importer import ZenPinnedTabImporter
    return ZenPinnedTabImporter(tmp_path)


def _insert_tab(db: Path, arc_tab_id: str, title: str, url: str, workspace_uuid: str) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO zen_pins (uuid, arc_tab_id, title, url, workspace_uuid, container_id, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, 1, 0, 0)",
            ("{" + str(uuid.uuid4()) + "}", arc_tab_id, title, url, workspace_uuid),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Bug 6 — Duplicate detection is scoped per workspace
# ---------------------------------------------------------------------------

class TestTabExistsWorkspaceScoped:
    def test_same_tab_allowed_in_different_workspaces(self, importer, places_db):
        """The same arc_tab_id in workspace A must NOT block insertion in workspace B."""
        arc_id = str(uuid.uuid4())
        ws_a = "{ws-a}"
        ws_b = "{ws-b}"
        _insert_tab(places_db, arc_id, "YouTube", "https://youtube.com", ws_a)

        # Must NOT be a duplicate for workspace B
        assert importer.tab_exists(arc_id, "YouTube", "https://youtube.com", workspace_uuid=ws_b) is False

    def test_same_tab_is_duplicate_in_same_workspace(self, importer, places_db):
        """A tab already in workspace A must be detected as a duplicate for that same workspace."""
        arc_id = str(uuid.uuid4())
        ws_a = "{ws-a}"
        _insert_tab(places_db, arc_id, "YouTube", "https://youtube.com", ws_a)

        assert importer.tab_exists(arc_id, "YouTube", "https://youtube.com", workspace_uuid=ws_a) is True

    def test_no_workspace_scope_falls_back_to_global(self, importer, places_db):
        """When workspace_uuid is None, check is global (backward compat)."""
        arc_id = str(uuid.uuid4())
        _insert_tab(places_db, arc_id, "YouTube", "https://youtube.com", "{any-ws}")

        assert importer.tab_exists(arc_id, "YouTube", "https://youtube.com", workspace_uuid=None) is True

    def test_title_url_fallback_also_workspace_scoped(self, importer, places_db):
        """Title+URL fallback duplicate check (no arc_tab_id) must also be workspace-scoped."""
        ws_a = "{ws-a}"
        ws_b = "{ws-b}"
        _insert_tab(places_db, None, "Example", "https://example.com", ws_a)

        assert importer.tab_exists(None, "Example", "https://example.com", workspace_uuid=ws_a) is True
        assert importer.tab_exists(None, "Example", "https://example.com", workspace_uuid=ws_b) is False

    def test_session_cache_is_workspace_scoped(self, importer, places_db):
        """In-memory session cache must also use (arc_tab_id, title, url, workspace_uuid) as key."""
        arc_id = str(uuid.uuid4())
        ws_a = "{ws-a}"
        ws_b = "{ws-b}"

        # Prime the session cache for ws_a
        importer.imported_in_session.add((arc_id, "YouTube", "https://youtube.com", ws_a))

        assert importer.tab_exists(arc_id, "YouTube", "https://youtube.com", workspace_uuid=ws_a) is True
        assert importer.tab_exists(arc_id, "YouTube", "https://youtube.com", workspace_uuid=ws_b) is False
