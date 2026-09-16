"""
Tests for arc_pinned_tab_extractor.py

Covers:
  Fix — Default profile essentials assigned to first space when --include-default-essentials
  Fix — Default profile essentials skipped by default
  Fix — Hardcoded company names removed from _assign_essential_tab_to_space
  Fix — profile field propagated to ArcSpace and export JSON
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from arc_pinned_tab_extractor import ArcPinnedTabExtractor, ArcSpace

# ---------------------------------------------------------------------------
# Minimal StorableSidebar.json builder
# ---------------------------------------------------------------------------

def _make_sidebar(spaces_info: list, topapps: list) -> dict:
    """Build a minimal StorableSidebar.json-like structure."""
    space_models = []
    for sp in spaces_info:
        space_models.append(sp["id"])
        space_models.append({
            "value": {
                "title": sp["name"],
                "profile": {"custom": {"_0": {"directoryBasename": sp["profile"]}}}
                           if sp.get("profile") else {},
            }
        })

    items = {}
    spaces_arr = []

    for sp in spaces_info:
        container_uuid = f"container-{sp['id']}"
        spaces_arr.append(sp["id"])
        spaces_arr.append({
            "containerIDs": ["pinned", container_uuid],
        })
        items[container_uuid] = {"childrenIds": [], "data": {}}

    for ta in topapps:
        ta_id = ta["id"]
        child_ids = []
        for i, tab in enumerate(ta["tabs"]):
            tid = f"tab-{ta_id}-{i}"
            items[tid] = {
                "data": {"tab": {"savedURL": tab["url"], "savedTitle": tab["title"]}},
                "parentID": ta_id,
            }
            child_ids.append(tid)
        profile_value = {}
        if ta.get("profile"):
            profile_value = {"custom": {"_0": {"directoryBasename": ta["profile"]}}}
        else:
            profile_value = {"default": {}}
        items[ta_id] = {
            "childrenIds": child_ids,
            "data": {
                "itemContainer": {
                    "containerType": {
                        "topApps": {
                            "_0": profile_value
                        }
                    }
                }
            },
        }

    flat_items = []
    for k, v in items.items():
        flat_items.append(k)
        flat_items.append(v)

    return {
        "firebaseSyncState": {
            "syncData": {
                "spaceModels": space_models,
            }
        },
        "sidebar": {
            "containers": [
                {},
                {
                    "spaces": spaces_arr,
                    "items": flat_items,
                },
            ]
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultProfileEssentials:
    def _run(self, sidebar: dict, skip_default: bool) -> list:
        extractor = ArcPinnedTabExtractor()
        spaces_info = {
            "space-1": {"name": "Workspace", "icon": None, "profile": "Profile 1", "color": None},
        }
        # Run with the correct flag
        extractor.skip_default_essentials = skip_default
        return extractor._extract_essential_tabs_distributed(sidebar, spaces_info)

    def test_default_essentials_skipped_when_flag_true(self):
        extractor = ArcPinnedTabExtractor()
        extractor.skip_default_essentials = True
        sidebar = _make_sidebar(
            [{"id": "s1", "name": "Workspace", "profile": "Profile 1"}],
            [{"id": "ta1", "profile": None, "tabs": [{"url": "https://youtube.com", "title": "YouTube"}]}],
        )
        spaces_info = {"s1": {"name": "Workspace", "icon": None, "profile": "Profile 1", "color": None}}
        result = extractor._extract_essential_tabs_distributed(sidebar, spaces_info)
        all_tabs = [t for tabs in result.values() for t in tabs]
        urls = [t.url for t in all_tabs if t.url != ""]
        assert "https://youtube.com" not in urls

    def test_default_essentials_assigned_to_first_space_when_flag_false(self):
        extractor = ArcPinnedTabExtractor()
        extractor.skip_default_essentials = False
        sidebar = _make_sidebar(
            [{"id": "s1", "name": "Workspace", "profile": "Profile 1"}],
            [{"id": "ta1", "profile": None, "tabs": [{"url": "https://youtube.com", "title": "YouTube"}]}],
        )
        spaces_info = {"s1": {"name": "Workspace", "icon": None, "profile": "Profile 1", "color": None}}
        result = extractor._extract_essential_tabs_distributed(sidebar, spaces_info)
        all_tabs = [t for tabs in result.values() for t in tabs]
        urls = [t.url for t in all_tabs]
        assert "https://youtube.com" in urls, \
            "YouTube must be imported when skip_default_essentials=False"

    def test_default_essential_marked_as_essential(self):
        extractor = ArcPinnedTabExtractor()
        extractor.skip_default_essentials = False
        sidebar = _make_sidebar(
            [{"id": "s1", "name": "Workspace", "profile": "Profile 1"}],
            [{"id": "ta1", "profile": None, "tabs": [{"url": "https://youtube.com", "title": "YouTube"}]}],
        )
        spaces_info = {"s1": {"name": "Workspace", "icon": None, "profile": "Profile 1", "color": None}}
        result = extractor._extract_essential_tabs_distributed(sidebar, spaces_info)
        all_tabs = [t for tabs in result.values() for t in tabs]
        for tab in all_tabs:
            assert tab.is_essential is True, "Every extracted topApps tab must have is_essential=True"


class TestEssentialTabDistribution:
    def test_profile1_essentials_go_to_first_matching_space(self):
        extractor = ArcPinnedTabExtractor()
        extractor.skip_default_essentials = True
        sidebar = _make_sidebar(
            [
                {"id": "s1", "name": "Workspace", "profile": "Profile 1"},
                {"id": "s2", "name": "AI Projects", "profile": "Profile 1"},
            ],
            [{"id": "ta1", "profile": "Profile 1",
              "tabs": [{"url": "https://asana.com", "title": "Asana"}]}],
        )
        spaces_info = {
            "s1": {"name": "Workspace", "icon": None, "profile": "Profile 1", "color": None},
            "s2": {"name": "AI Projects", "icon": None, "profile": "Profile 1", "color": None},
        }
        result = extractor._extract_essential_tabs_distributed(sidebar, spaces_info)
        # Only the primary (first) space gets the essentials — not all spaces
        assert "s1" in result
        assert "s2" not in result, \
            "Profile 1 essentials must only go to the first space, not all spaces sharing the profile"

    def test_profile2_essentials_go_to_personal_space(self):
        extractor = ArcPinnedTabExtractor()
        extractor.skip_default_essentials = True
        sidebar = _make_sidebar(
            [{"id": "sp", "name": "Personal", "profile": "Profile 2"}],
            [{"id": "ta2", "profile": "Profile 2",
              "tabs": [{"url": "https://gmail.com", "title": "Gmail"}]}],
        )
        spaces_info = {"sp": {"name": "Personal", "icon": None, "profile": "Profile 2", "color": None}}
        result = extractor._extract_essential_tabs_distributed(sidebar, spaces_info)
        assert "sp" in result
        urls = [t.url for t in result["sp"]]
        assert "https://gmail.com" in urls


class TestProfileFieldOnArcSpace:
    def test_profile_exported_to_json(self):
        """ArcSpace.profile must appear in the export_to_json output."""
        space = ArcSpace(
            space_id="s1", space_name="Work",
            pinned_tabs=[], folders=[], open_tabs=[],
            profile="Profile 1",
        )
        extractor = ArcPinnedTabExtractor()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            out = Path(f.name)
        extractor.export_to_json([space], out)
        data = json.loads(out.read_text())
        assert data["spaces"][0]["profile"] == "Profile 1"
        out.unlink()

    def test_arcspace_profile_field_defaults_to_none(self):
        space = ArcSpace("s1", "Work", [], [], [])
        assert space.profile is None


class TestAssignEssentialTabToSpaceGeneric:
    def test_generic_url_matching_works(self):
        """Space name appearing in URL should score positively."""
        extractor = ArcPinnedTabExtractor()
        items_lookup = {
            "tab1": {"data": {"tab": {"savedURL": "https://mycompany.com/dashboard", "savedTitle": "Dashboard"}}}
        }
        spaces_info = {
            "s1": {"name": "mycompany", "icon": None, "profile": "Profile 1", "color": None},
            "s2": {"name": "unrelated", "icon": None, "profile": "Profile 1", "color": None},
        }
        result = extractor._assign_essential_tab_to_space(["tab1"], items_lookup, spaces_info)
        assert result == "s1"

    def test_no_match_returns_orphaned(self):
        extractor = ArcPinnedTabExtractor()
        items_lookup = {
            "tab1": {"data": {"tab": {"savedURL": "https://example.com", "savedTitle": "Example"}}}
        }
        spaces_info = {
            "s1": {"name": "mycompany", "icon": None, "profile": "Profile 1", "color": None},
        }
        result = extractor._assign_essential_tab_to_space(["tab1"], items_lookup, spaces_info)
        assert result == "orphaned"
