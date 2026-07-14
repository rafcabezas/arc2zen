import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
from zen_sessions_importer import ZenSessionsImporter, read_mozlz4


class ZenSessionsImporterTest(unittest.TestCase):
    def test_space_keeps_arc_metadata_and_container(self):
        export = {"spaces": [{
            "space_name": "Work", "icon": "💼",
            "color": {"r": 0.5, "g": 0.25, "b": 1},
            "folders": [], "pinned_tabs": [],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            self.assertTrue(ZenSessionsImporter(profile).import_arc_data(export, {"Work": 7}))
            space = read_mozlz4(profile / "zen-sessions.jsonlz4")["spaces"][0]

        self.assertEqual(space["icon"], "💼")
        self.assertEqual(space["containerTabId"], 7)
        self.assertEqual(space["position"], 1000)
        self.assertEqual(space["theme"]["gradientColors"][0]["c"], [128, 64, 255])

    def test_essential_tab_uses_space_container(self):
        export = {"spaces": [{
            "space_name": "Work", "folders": [],
            "pinned_tabs": [{"url": "https://example.com", "is_essential": True}],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            importer = ZenSessionsImporter(profile)
            importer.import_arc_data(export, {"Work": 7})
            importer.import_arc_data(export, {"Work": 7})
            tabs = read_mozlz4(profile / "zen-sessions.jsonlz4")["tabs"]
            tab = tabs[0]
            prefs = (profile / "prefs.js").read_text()

        self.assertEqual(len(tabs), 1)
        self.assertTrue(tab["zenEssential"])
        self.assertIsNone(tab["zenWorkspace"])
        self.assertTrue(tab["zenDefaultUserContextId"])
        self.assertEqual(tab["userContextId"], 7)
        self.assertIn(
            'user_pref("zen.workspaces.separate-essentials", true);',
            prefs,
        )

    def test_reimport_replaces_matching_space(self):
        def export(url):
            return {"spaces": [{
                "space_name": "Work", "folders": [{"folder_id": "f", "title": "Folder"}],
                "pinned_tabs": [{"url": url, "title": url, "folder_path": ["Folder"]}],
            }]}

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            importer = ZenSessionsImporter(profile)
            self.assertTrue(importer.import_arc_data(export("https://old.example"), {"Work": 1}))
            self.assertTrue(importer.import_arc_data(export("https://new.example"), {"Work": 1}))
            data = read_mozlz4(profile / "zen-sessions.jsonlz4")

        self.assertEqual(len(data["spaces"]), 1)
        self.assertEqual(len(data["folders"]), 1)
        self.assertEqual(len(data["groups"]), 1)
        self.assertEqual(len(data["tabs"]), 2)  # pinned tab + folder placeholder
        self.assertEqual(data["tabs"][0]["entries"][0]["url"], "https://new.example")


if __name__ == "__main__":
    unittest.main()
