"""
Tests for carrying Arc space colors into Zen

Covers:
  Fix — spaces read from the local sidebar keep their color (it was always None)
  Fix — zen-sessions workspaces get the color as their theme gradient
"""
import json

import pytest

from arc_pinned_tab_extractor import ArcPinnedTabExtractor, arc_space_color
from tests.conftest import HAS_LZ4, make_arc_export, make_arc_space_data, read_mozlz4

BLUE = {"red": 0.094, "green": 0.514, "blue": 0.722, "alpha": 1, "colorSpace": "extendedSRGB"}


def theme(mid_tone):
    return {"primaryColorPalette": {"midTone": mid_tone}}


class TestArcSpaceColor:
    def test_mid_tone(self):
        assert arc_space_color(theme(BLUE)) == {"r": 0.094, "g": 0.514, "b": 0.722}

    def test_extended_srgb_is_clamped(self):
        assert arc_space_color(theme({"red": -0.1, "green": 1.2, "blue": 0.5})) == \
            {"r": 0, "g": 1, "b": 0.5}

    def test_no_color(self):
        assert arc_space_color(None) is None
        assert arc_space_color({}) is None
        assert arc_space_color({"primaryColorPalette": {}}) is None

    def test_local_sidebar_space_keeps_color(self, tmp_path):
        """With an empty sync cache, spaces come from the local sidebar."""
        space = {"id": "s1", "title": "Work", "containerIDs": ["pinned", "c1"],
                 "customInfo": {"windowTheme": theme(BLUE)}}
        sidebar = {"firebaseSyncState": {"syncData": {"spaceModels": []}},
                   "sidebar": {"containers": [{}, {
                       "spaces": ["s1", space],
                       "items": ["c1", {"id": "c1", "childrenIds": [], "data": {}}]}]}}
        path = tmp_path / "StorableSidebar.json"
        path.write_text(json.dumps(sidebar), encoding="utf-8")
        extractor = ArcPinnedTabExtractor()
        extractor.arc_sidebar_file = path
        [work] = extractor.extract_pinned_tabs()
        assert work.color == {"r": 0.094, "g": 0.514, "b": 0.722}


@pytest.mark.skipif(not HAS_LZ4, reason="lz4 not installed")
class TestZenSessionsColor:
    def test_color_becomes_workspace_gradient(self, tmp_profile, empty_sessions):
        from zen_sessions_importer import ZenSessionsImporter
        importer = ZenSessionsImporter(tmp_profile)
        export = make_arc_export([dict(make_arc_space_data("Work"),
                                       color={"r": 0.094, "g": 0.514, "b": 0.722}),
                                  make_arc_space_data("Plain")])
        importer.import_arc_data(export, {"Work": 0, "Plain": 0})
        themes = {s["name"]: s["theme"] for s in read_mozlz4(importer.sessions_file)["spaces"]}
        assert [dot["c"] for dot in themes["Work"]["gradientColors"]] == [[24, 131, 184]]
        assert themes["Plain"]["gradientColors"] == []
