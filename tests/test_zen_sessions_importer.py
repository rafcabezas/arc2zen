"""
Tests for zen_sessions_importer.py

Covers every bug fixed in this PR:
  Bug 1 — Incomplete tab entry structure
  Bug 2 — _sync_sessionstore must not overwrite win["tabs"]
  Bug 3 — _merge_with_existing cleans stale spaces and orphaned tabs
  Bug 4 — container_mappings are applied (containerTabId + userContextId)
  Edge  — arc:// URLs are filtered out
  Edge  — root-level non-essential tabs are dropped by default
"""
from pathlib import Path

import pytest

from tests.conftest import (
    HAS_LZ4,
    make_arc_export,
    make_arc_space_data,
    make_arc_tab_data,
    make_space,
    make_tab,
    read_mozlz4,
    write_mozlz4,
)

pytestmark = pytest.mark.skipif(not HAS_LZ4, reason="lz4 not installed")


@pytest.fixture()
def importer(tmp_profile):
    from zen_sessions_importer import ZenSessionsImporter
    return ZenSessionsImporter(tmp_profile)


# ---------------------------------------------------------------------------
# Bug 1 — Tab entry must contain all required fields
# ---------------------------------------------------------------------------

class TestBuildTab:
    REQUIRED_ENTRY_FIELDS = {
        "url", "title", "cacheKey", "ID", "docshellUUID",
        "originalURI", "resultPrincipalURI", "hasUserInteraction",
        "triggeringPrincipal_base64", "docIdentifier", "transient",
    }
    REQUIRED_TAB_FIELDS = {
        "entries", "lastAccessed", "pinned", "hidden", "zenWorkspace",
        "zenSyncId", "zenEssential", "zenDefaultUserContextId",
        "zenPinnedIcon", "zenIsEmpty", "zenHasStaticIcon",
        "zenGlanceId", "zenIsGlance", "_zenPinnedInitialState",
        "zenLiveFolderItemId", "searchMode", "userContextId",
        "attributes", "index", "scroll", "storage",
        "userTypedValue", "userTypedClear", "image",
    }

    def test_entry_has_all_required_fields(self, importer):
        tab_data = make_arc_tab_data("https://example.com", "Example")
        tab = importer._build_tab(tab_data, "{ws-uuid}", index=1)
        entry = tab["entries"][0]
        missing = self.REQUIRED_ENTRY_FIELDS - set(entry.keys())
        assert not missing, f"Missing entry fields: {missing}"

    def test_tab_has_all_required_fields(self, importer):
        tab_data = make_arc_tab_data("https://example.com", "Example")
        tab = importer._build_tab(tab_data, "{ws-uuid}", index=1)
        missing = self.REQUIRED_TAB_FIELDS - set(tab.keys())
        assert not missing, f"Missing tab fields: {missing}"

    def test_essential_tab_zenDefaultUserContextId_is_string_true(self, importer):
        """zenDefaultUserContextId must be the STRING "true" for essentials, not boolean."""
        tab_data = make_arc_tab_data("https://example.com", "X", is_essential=True)
        tab = importer._build_tab(tab_data, "{ws}", index=1)
        assert tab["zenDefaultUserContextId"] == "true"
        assert isinstance(tab["zenDefaultUserContextId"], str)

    def test_non_essential_tab_zenDefaultUserContextId_is_null(self, importer):
        tab_data = make_arc_tab_data("https://example.com", "X", is_essential=False)
        tab = importer._build_tab(tab_data, "{ws}", index=1)
        assert tab["zenDefaultUserContextId"] is None

    def test_attributes_always_empty(self, importer):
        """attributes must always be {} — Zen reads zenEssential, not attributes."""
        for is_essential in (True, False):
            tab_data = make_arc_tab_data("https://example.com", "X", is_essential=is_essential)
            tab = importer._build_tab(tab_data, "{ws}", index=1)
            assert tab["attributes"] == {}, \
                f"attributes should be {{}} for is_essential={is_essential}"

    def test_syncId_uses_docIdentifier_format(self, importer):
        """zenSyncId must be '{timestamp}-{docIdentifier}' matching the entry's docIdentifier."""
        tab_data = make_arc_tab_data("https://example.com", "X")
        tab = importer._build_tab(tab_data, "{ws}", index=1)
        doc_id = tab["entries"][0]["docIdentifier"]
        ts, counter = tab["zenSyncId"].split("-", 1)
        assert counter == str(doc_id), "zenSyncId second part must equal entry docIdentifier"

    def test_container_id_applied_to_userContextId(self, importer):
        """Bug 4 — container_id must propagate to userContextId on every tab."""
        tab_data = make_arc_tab_data("https://example.com", "X")
        for cid in (0, 1, 2, 9):
            tab = importer._build_tab(tab_data, "{ws}", index=1, container_id=cid)
            assert tab["userContextId"] == cid


# ---------------------------------------------------------------------------
# Bug 2 — _sync_sessionstore must NOT touch win["tabs"]
# ---------------------------------------------------------------------------

class TestSyncSessionstore:
    def _make_sessionstore(self, tmp_profile: Path, existing_tabs=None) -> Path:
        ss_path = tmp_profile / "sessionstore.jsonlz4"
        existing = existing_tabs or [{"url": "https://open-tab.com", "pinned": False}]
        write_mozlz4(ss_path, {"windows": [{"tabs": existing, "groups": [], "folders": [], "spaces": [], "splitViewData": []}], "_closedWindows": []})
        return ss_path

    def test_win_tabs_not_replaced(self, importer, tmp_profile):
        """The user's open browsing session in win['tabs'] must survive sync."""
        original_tab = {"url": "https://my-open-tab.com", "pinned": False, "title": "My Tab"}
        ss_path = self._make_sessionstore(tmp_profile, [original_tab])

        merged = {
            "tabs": [make_tab("https://pinned.com", "Pinned", "{ws}")],
            "folders": [],
            "spaces": [make_space("Work")],
            "groups": [],
            "splitViewData": [],
        }
        importer._sync_sessionstore(merged)

        result = read_mozlz4(ss_path)
        win_tabs = result["windows"][0]["tabs"]
        urls_in_win = [t.get("url", t.get("entries", [{}])[0].get("url")) for t in win_tabs]
        assert "https://my-open-tab.com" in urls_in_win, \
            "Existing open tab must not be removed by _sync_sessionstore"
        assert "https://pinned.com" not in urls_in_win, \
            "Pinned workspace tabs must NOT be injected into win['tabs']"

    def test_groups_are_synced(self, importer, tmp_profile):
        """Folder groups should be written to win['groups'] so Zen can create DOM elements."""
        self._make_sessionstore(tmp_profile)
        folder_id = "{folder-123}"
        merged = {
            "tabs": [],
            "folders": [{"id": folder_id, "name": "MyFolder", "workspaceId": "{ws}", "collapsed": False}],
            "spaces": [],
            "groups": [],
            "splitViewData": [],
        }
        importer._sync_sessionstore(merged)

        ss_path = tmp_profile / "sessionstore.jsonlz4"
        result = read_mozlz4(ss_path)
        group_ids = [g["id"] for g in result["windows"][0]["groups"]]
        assert folder_id in group_ids


# ---------------------------------------------------------------------------
# Bug 3 — _merge_with_existing must clean stale spaces and orphaned tabs
# ---------------------------------------------------------------------------

class TestMergeWithExisting:
    def test_stale_arc_spaces_replaced(self, importer):
        """Re-running migration must replace old Arc spaces, not append duplicates."""
        old_space = make_space("Workspace")
        old_tab = make_tab("https://old.com", "Old", old_space["uuid"])
        existing = {
            "spaces": [old_space],
            "tabs": [old_tab],
            "folders": [],
            "groups": [],
            "splitViewData": [],
        }
        new_space = make_space("Workspace")
        new_tab = make_tab("https://new.com", "New", new_space["uuid"])

        merged = importer._merge_with_existing(
            existing, [new_space], [new_tab], [], {"Workspace"}
        )

        space_names = [s["name"] for s in merged["spaces"]]
        assert space_names.count("Workspace") == 1, "Duplicate spaces must not exist"
        tab_urls = [t["entries"][0]["url"] if "entries" in t else t.get("url") for t in merged["tabs"]]
        assert "https://old.com" not in tab_urls, "Old tabs must be removed"
        assert "https://new.com" in tab_urls, "New tabs must be present"

    def test_orphaned_tabs_purged(self, importer):
        """Tabs referencing a non-existent workspace UUID must be removed."""
        ghost_uuid = "{dead-beef-uuid}"
        orphaned_tab = make_tab("https://ghost.com", "Ghost", ghost_uuid)
        keeper_space = make_space("Keeper")
        keeper_tab = make_tab("https://keeper.com", "Keeper", keeper_space["uuid"])

        existing = {
            "spaces": [keeper_space],
            "tabs": [orphaned_tab, keeper_tab],
            "folders": [],
            "groups": [],
            "splitViewData": [],
        }

        merged = importer._merge_with_existing(existing, [], [], [], set())

        tab_urls = [t["entries"][0]["url"] if "entries" in t else t.get("url") for t in merged["tabs"]]
        assert "https://ghost.com" not in tab_urls, "Orphaned tab must be purged"
        assert "https://keeper.com" in tab_urls, "Valid tab must survive"

    def test_non_arc_spaces_preserved(self, importer):
        """Manually created Zen spaces that are not Arc spaces must not be touched."""
        native_space = make_space("My Personal Space")
        native_tab = make_tab("https://native.com", "Native", native_space["uuid"])
        existing = {
            "spaces": [native_space],
            "tabs": [native_tab],
            "folders": [],
            "groups": [],
            "splitViewData": [],
        }
        new_arc_space = make_space("Work")
        merged = importer._merge_with_existing(
            existing, [new_arc_space], [], [], {"Work"}
        )

        names = [s["name"] for s in merged["spaces"]]
        assert "My Personal Space" in names, "Native Zen space must be preserved"
        assert "Work" in names


# ---------------------------------------------------------------------------
# Bug 4 — containerTabId set on spaces; userContextId set on tabs
# ---------------------------------------------------------------------------

class TestContainerPropagation:
    def test_space_containerTabId_set(self, importer):
        """Bug 4 — _build_space must write containerTabId from container_id."""
        space = importer._build_space({"space_name": "Work"}, container_id=2)
        assert space["containerTabId"] == 2

    def test_space_containerTabId_defaults_to_zero(self, importer):
        space = importer._build_space({"space_name": "Work"})
        assert space["containerTabId"] == 0

    def test_process_space_passes_container_id(self, importer):
        """container_id must flow through _process_space to every tab."""
        space_data = make_arc_space_data(
            "Work", profile="Profile 1",
            pinned_tabs=[
                make_arc_tab_data("https://asana.com", "Asana", is_essential=True),
                make_arc_tab_data("https://figma.com", "Figma", folder_path=["Design"]),
            ],
            folders=[{"folder_id": "f1", "title": "Design", "parent_id": "", "index": 0, "children_ids": []}],
        )
        space, folders, tabs = importer._process_space(space_data, container_id=2)

        assert space["containerTabId"] == 2
        real_tabs = [t for t in tabs if not t.get("zenIsEmpty")]
        for tab in real_tabs:
            assert tab["userContextId"] == 2, \
                f"Tab {tab['entries'][0]['url']} must have userContextId=2"

    def test_import_arc_data_uses_container_mappings(self, importer, tmp_profile, empty_sessions):
        """End-to-end: container_mappings must reach containerTabId on spaces and userContextId on tabs."""
        arc_export = make_arc_export([
            make_arc_space_data("Workspace", pinned_tabs=[
                make_arc_tab_data("https://asana.com", "Asana", is_essential=True),
            ]),
            make_arc_space_data("Personal", profile="Profile 2", pinned_tabs=[
                make_arc_tab_data("https://gmail.com", "Gmail", is_essential=True),
            ]),
        ])
        container_mappings = {"Workspace": 2, "Personal": 1}

        importer.import_arc_data(arc_export, container_mappings)

        result = read_mozlz4(tmp_profile / "zen-sessions.jsonlz4")
        space_by_name = {s["name"]: s for s in result["spaces"]}
        tab_by_url = {
            t["entries"][0]["url"]: t
            for t in result["tabs"]
            if not t.get("zenIsEmpty") and t.get("entries")
        }

        assert space_by_name["Workspace"]["containerTabId"] == 2
        assert space_by_name["Personal"]["containerTabId"] == 1
        assert tab_by_url["https://asana.com"]["userContextId"] == 2
        assert tab_by_url["https://gmail.com"]["userContextId"] == 1


# ---------------------------------------------------------------------------
# Edge — arc:// URLs are filtered before writing
# ---------------------------------------------------------------------------

class TestArcUrlFiltering:
    def test_arc_urls_not_imported(self, importer, empty_sessions):
        arc_export = make_arc_export([
            make_arc_space_data("Work", pinned_tabs=[
                make_arc_tab_data("arc://bookmarks/", "Bookmarks", is_essential=True),
                make_arc_tab_data("arc://extensions/", "Extensions", folder_path=["Tools"]),
                make_arc_tab_data("https://asana.com", "Asana", is_essential=True),
            ]),
        ])
        importer.import_arc_data(arc_export, {"Work": 2})

        result = read_mozlz4(importer.sessions_file)
        urls = {
            t["entries"][0]["url"]
            for t in result["tabs"]
            if not t.get("zenIsEmpty") and t.get("entries")
        }
        assert "arc://bookmarks/" not in urls
        assert "arc://extensions/" not in urls
        assert "https://asana.com" in urls

    def test_https_urls_pass_through(self, importer, empty_sessions):
        arc_export = make_arc_export([
            make_arc_space_data("Work", pinned_tabs=[
                make_arc_tab_data("https://example.com", "Example", is_essential=True),
            ]),
        ])
        importer.import_arc_data(arc_export, {"Work": 2})
        result = read_mozlz4(importer.sessions_file)
        urls = {t["entries"][0]["url"] for t in result["tabs"] if t.get("entries")}
        assert "https://example.com" in urls


# ---------------------------------------------------------------------------
# Edge — root-level tabs dropped by default; kept with drop_root_tabs=False
# ---------------------------------------------------------------------------

class TestRootTabFiltering:
    def _run(self, importer, drop_root_tabs: bool):
        arc_export = make_arc_export([
            make_arc_space_data("Work", pinned_tabs=[
                make_arc_tab_data("https://essential.com", "Ess", is_essential=True),
                make_arc_tab_data("https://root.com", "Root"),           # root-level
                make_arc_tab_data("https://infolder.com", "InFolder", folder_path=["Docs"]),
            ]),
        ])
        importer.import_arc_data(arc_export, {"Work": 2}, drop_root_tabs=drop_root_tabs)
        result = read_mozlz4(importer.sessions_file)
        return {
            t["entries"][0]["url"]
            for t in result["tabs"]
            if not t.get("zenIsEmpty") and t.get("entries")
        }

    def test_root_tabs_dropped_by_default(self, importer, empty_sessions):
        urls = self._run(importer, drop_root_tabs=True)
        assert "https://root.com" not in urls, "Root-level tab should be dropped by default"
        assert "https://essential.com" in urls
        assert "https://infolder.com" in urls

    def test_root_tabs_kept_when_flag_false(self, importer, empty_sessions):
        urls = self._run(importer, drop_root_tabs=False)
        assert "https://root.com" in urls, "Root-level tab should be kept when drop_root_tabs=False"

    def test_essential_tabs_never_dropped(self, importer, empty_sessions):
        """Essentials at root must always pass through regardless of drop_root_tabs."""
        urls = self._run(importer, drop_root_tabs=True)
        assert "https://essential.com" in urls
