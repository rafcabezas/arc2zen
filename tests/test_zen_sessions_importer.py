import tempfile
import unittest
from pathlib import Path

from src.zen_sessions_importer import ZenSessionsImporter


class ZenSessionsImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _load_session_json(self):
        importer = ZenSessionsImporter(self.profile_dir)
        return importer.load_session_data()

    def test_import_creates_workspaces_and_tabs(self):
        importer = ZenSessionsImporter(self.profile_dir)
        arc_export_data = {
            "spaces": [
                {
                    "space_name": "Work",
                    "icon": "💼",
                    "color": {"r": 0.2, "g": 0.7, "b": 0.9},
                    "folders": [{"folder_id": "f1", "title": "Tools", "parent_id": "", "index": 0}],
                    "pinned_tabs": [
                        {
                            "title": "A",
                            "url": "https://a.example.com",
                            "folder_path": ["Tools"],
                            "tab_id": "t1",
                        },
                        {
                            "title": "B",
                            "url": "https://b.example.com",
                            "folder_path": [],
                            "tab_id": "t2",
                            "is_essential": True,
                        },
                    ],
                }
            ]
        }
        container_mappings = {"Work": 7}

        mappings = importer.import_arc_pinned_tabs(arc_export_data, container_mappings, dry_run=False)

        self.assertIn("Work", mappings)
        session_data = self._load_session_json()
        spaces = [s for s in session_data["spaces"] if s.get("name") == "Work"]
        self.assertEqual(1, len(spaces))
        self.assertEqual(7, spaces[0].get("containerTabId"))

        tabs = [t for t in session_data["tabs"] if t.get("pinned")]
        self.assertEqual(2, len(tabs))
        urls = {t["entries"][0]["url"] for t in tabs}
        self.assertSetEqual({"https://a.example.com", "https://b.example.com"}, urls)

    def test_import_deduplicates_existing_tab(self):
        importer = ZenSessionsImporter(self.profile_dir)
        workspace_uuid = "{11111111-1111-1111-1111-111111111111}"
        seed = {
            "spaces": [
                {
                    "uuid": workspace_uuid,
                    "name": "Work",
                    "containerTabId": 7,
                    "position": 1000,
                    "theme": None,
                    "hasCollapsedPinnedTabs": False,
                }
            ],
            "lastCollected": 0,
            "tabs": [
                {
                    "entries": [{"url": "https://a.example.com", "title": "A"}],
                    "lastAccessed": 0,
                    "pinned": True,
                    "hidden": False,
                    "groupId": None,
                    "zenWorkspace": workspace_uuid,
                    "zenSyncId": "seed",
                    "zenEssential": False,
                    "zenDefaultUserContextId": None,
                    "zenPinnedIcon": None,
                    "zenIsEmpty": False,
                    "zenHasStaticIcon": False,
                    "zenGlanceId": None,
                    "zenIsGlance": False,
                    "_zenPinnedInitialState": {"entry": {"url": "https://a.example.com", "title": "A"}, "image": None},
                    "zenLiveFolderItemId": None,
                    "searchMode": None,
                    "userContextId": 7,
                    "attributes": {},
                    "index": 1,
                    "userTypedValue": "",
                    "userTypedClear": 0,
                    "image": None,
                }
            ],
            "folders": [],
            "splitViewData": [],
            "groups": [],
        }
        importer.save_session_data(seed)

        arc_export_data = {
            "spaces": [
                {
                    "space_name": "Work",
                    "icon": None,
                    "color": None,
                    "folders": [],
                    "pinned_tabs": [{"title": "A", "url": "https://a.example.com", "folder_path": [], "tab_id": "t1"}],
                }
            ]
        }
        container_mappings = {"Work": 7}

        importer.import_arc_pinned_tabs(arc_export_data, container_mappings, dry_run=False)
        session_data = self._load_session_json()
        tabs = [t for t in session_data["tabs"] if t.get("pinned")]
        self.assertEqual(1, len(tabs))


if __name__ == "__main__":
    unittest.main()
