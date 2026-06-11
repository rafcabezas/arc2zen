"""
Tests for zen_space_importer.py

Covers:
  Bug 5  — No longer creates per-space custom containers; uses Work/Personal
  Fix    — Stale per-space containers cleaned from containers.json on re-run
  Fix    — Smart container heuristic (name + profile-order fallback)
"""
import json
import sys
from pathlib import Path
from typing import List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tests.conftest import make_arc_export, make_arc_space_data

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUILTIN_CONTAINERS = [
    {"icon": "fingerprint", "color": "blue",   "public": True, "userContextId": 1,
     "name": "Personal"},
    {"icon": "briefcase",   "color": "orange", "public": True, "userContextId": 2,
     "l10nId": "user-context-work"},
    {"icon": "dollar",      "color": "green",  "public": True, "userContextId": 3,
     "l10nId": "user-context-banking"},
    {"icon": "cart",        "color": "pink",   "public": True, "userContextId": 4,
     "l10nId": "user-context-shopping"},
]


def _make_containers_file(path: Path, extra: Optional[List] = None) -> None:
    identities = list(BUILTIN_CONTAINERS)
    if extra:
        identities.extend(extra)
    data = {"version": 5, "lastUserContextId": max(c["userContextId"] for c in identities), "identities": identities}
    with open(path, "w") as f:
        json.dump(data, f)


def _load_containers(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


@pytest.fixture()
def space_importer(tmp_path):
    from zen_space_importer import ZenProfile, ZenSpaceImporter
    profile = ZenProfile(name="Default", path=tmp_path)
    _make_containers_file(tmp_path / "containers.json")
    (tmp_path / "prefs.js").write_text("")
    return ZenSpaceImporter(profile)


# ---------------------------------------------------------------------------
# Bug 5 — Uses Work/Personal containers, no new custom containers
# ---------------------------------------------------------------------------

class TestContainerMapping:
    def test_personal_space_gets_personal_container(self, space_importer, tmp_path):
        arc_export = make_arc_export([
            make_arc_space_data("Personal", profile="Profile 2"),
            make_arc_space_data("Workspace", profile="Profile 1"),
        ])
        mappings = space_importer.import_arc_spaces_as_containers(arc_export)
        assert mappings["Personal"] == 1, "Personal space must use Personal container (ID 1)"
        assert mappings["Workspace"] == 2, "Work space must use Work container (ID 2)"

    def test_no_new_containers_created(self, space_importer, tmp_path):
        """No custom per-space containers should be added to containers.json."""
        arc_export = make_arc_export([
            make_arc_space_data("Workspace", profile="Profile 1"),
            make_arc_space_data("AI Projects", profile="Profile 1"),
            make_arc_space_data("Personal", profile="Profile 2"),
        ])
        space_importer.import_arc_spaces_as_containers(arc_export)
        containers = _load_containers(tmp_path / "containers.json")
        ids = [c["userContextId"] for c in containers["identities"]]
        assert max(ids) <= 4, \
            f"No new containers should be created (IDs > 4 found: {[i for i in ids if i > 4]})"

    def test_all_work_spaces_get_work_container(self, space_importer):
        arc_export = make_arc_export([
            make_arc_space_data("Workspace",       profile="Profile 1"),
            make_arc_space_data("Placetel Project", profile="Profile 1"),
            make_arc_space_data("AI Projects",      profile="Profile 1"),
            make_arc_space_data("Onboarding",       profile="Profile 1"),
        ])
        mappings = space_importer.import_arc_spaces_as_containers(arc_export)
        for name in ("Workspace", "Placetel Project", "AI Projects", "Onboarding"):
            assert mappings[name] == 2, f"{name} must map to Work container"

    def test_dry_run_returns_mapping_without_writing(self, space_importer, tmp_path):
        original_mtime = (tmp_path / "containers.json").stat().st_mtime
        arc_export = make_arc_export([make_arc_space_data("Workspace")])
        mappings = space_importer.import_arc_spaces_as_containers(arc_export, dry_run=True)
        assert mappings == {}, "dry_run must return empty dict"
        assert (tmp_path / "containers.json").stat().st_mtime == original_mtime, \
            "containers.json must not be modified in dry_run"


# ---------------------------------------------------------------------------
# Fix — Stale containers from previous runs are cleaned up
# ---------------------------------------------------------------------------

class TestStaleContainerCleanup:
    def test_stale_per_space_containers_removed(self, tmp_path):
        """Containers with Arc space names and IDs > 5 must be removed."""
        from zen_space_importer import ZenProfile, ZenSpaceImporter
        stale = [
            {"icon": "fingerprint", "color": "blue", "public": True,
             "userContextId": 6, "name": "Workspace"},
            {"icon": "briefcase", "color": "turquoise", "public": True,
             "userContextId": 7, "name": "AI Projects"},
        ]
        _make_containers_file(tmp_path / "containers.json", extra=stale)
        (tmp_path / "prefs.js").write_text("")
        profile = ZenProfile(name="Default", path=tmp_path)
        importer = ZenSpaceImporter(profile)

        arc_export = make_arc_export([
            make_arc_space_data("Workspace"),
            make_arc_space_data("AI Projects"),
        ])
        importer.import_arc_spaces_as_containers(arc_export)

        containers = _load_containers(tmp_path / "containers.json")
        names = [c.get("name", "") for c in containers["identities"]]
        ids = [c["userContextId"] for c in containers["identities"]]
        assert "Workspace" not in [n for n, i in zip(names, ids) if i > 5], \
            "Stale 'Workspace' container (ID 6) must be removed"
        assert "AI Projects" not in [n for n, i in zip(names, ids) if i > 5], \
            "Stale 'AI Projects' container (ID 7) must be removed"

    def test_builtin_containers_preserved(self, space_importer, tmp_path):
        """Built-in containers (IDs 1-4) must never be removed."""
        arc_export = make_arc_export([make_arc_space_data("Personal", profile="Profile 2")])
        space_importer.import_arc_spaces_as_containers(arc_export)
        containers = _load_containers(tmp_path / "containers.json")
        ids = {c["userContextId"] for c in containers["identities"]}
        for builtin_id in (1, 2, 3, 4):
            assert builtin_id in ids, f"Built-in container ID {builtin_id} must not be removed"


# ---------------------------------------------------------------------------
# Fix — Smart container heuristic
# ---------------------------------------------------------------------------

class TestContainerHeuristic:
    def _mappings_for(self, tmp_path, arc_spaces_data):
        from zen_space_importer import ZenProfile, ZenSpaceImporter
        _make_containers_file(tmp_path / "containers.json")
        (tmp_path / "prefs.js").write_text("")
        profile = ZenProfile(name="Default", path=tmp_path)
        importer = ZenSpaceImporter(profile)
        return importer.import_arc_spaces_as_containers(make_arc_export(arc_spaces_data))

    def test_personal_keyword_in_name(self, tmp_path):
        """Any space with 'personal' (case-insensitive) in the name → Personal container."""
        for name in ("Personal", "personal", "My Personal Space", "PERSONAL_STUFF"):
            mappings = self._mappings_for(tmp_path, [make_arc_space_data(name)])
            assert mappings[name] == 1, f"'{name}' should map to Personal container"

    def test_highest_profile_fallback_when_no_personal_named_space(self, tmp_path):
        """When no space has 'personal' in the name, the highest-numbered profile → Personal."""
        spaces = [
            make_arc_space_data("Work Space",  profile="Profile 1"),
            make_arc_space_data("Side Project", profile="Profile 2"),  # highest = personal
        ]
        mappings = self._mappings_for(tmp_path, spaces)
        assert mappings["Side Project"] == 1, "Highest-numbered profile should get Personal container"
        assert mappings["Work Space"]   == 2

    def test_name_takes_precedence_over_profile_order(self, tmp_path):
        """Explicit 'personal' name must override the profile-order fallback."""
        spaces = [
            make_arc_space_data("Personal",   profile="Profile 1"),  # name wins
            make_arc_space_data("Work",        profile="Profile 2"),  # higher profile but not personal
        ]
        mappings = self._mappings_for(tmp_path, spaces)
        assert mappings["Personal"] == 1
        assert mappings["Work"]     == 2

    def test_all_work_when_no_personal_space(self, tmp_path):
        """If no space qualifies as personal (single profile), all go to Work."""
        spaces = [make_arc_space_data("Alpha", profile="Profile 1"),
                  make_arc_space_data("Beta",  profile="Profile 1")]
        mappings = self._mappings_for(tmp_path, spaces)
        assert all(v == 2 for v in mappings.values()), \
            "Single-profile setup with no personal space — all should be Work"
