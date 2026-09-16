"""
Shared fixtures and helpers for arc2zen tests.
"""
import json
import struct
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Make src/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    import lz4.block
    HAS_LZ4 = True
except ImportError:
    HAS_LZ4 = False


# ---------------------------------------------------------------------------
# mozlz4 helpers (duplicated here so tests have no circular imports)
# ---------------------------------------------------------------------------

MOZLZ4_MAGIC = b"mozLz40\0"


def write_mozlz4(path: Path, data: Dict) -> None:
    json_bytes = json.dumps(data).encode("utf-8")
    compressed = lz4.block.compress(json_bytes, store_size=False)
    with open(path, "wb") as f:
        f.write(MOZLZ4_MAGIC)
        f.write(struct.pack("<I", len(json_bytes)))
        f.write(compressed)


def read_mozlz4(path: Path) -> Dict:
    with open(path, "rb") as f:
        magic = f.read(8)
        assert magic == MOZLZ4_MAGIC
        size = struct.unpack("<I", f.read(4))[0]
        return json.loads(lz4.block.decompress(f.read(), uncompressed_size=size))


# ---------------------------------------------------------------------------
# Factory helpers — build minimal but valid data structures
# ---------------------------------------------------------------------------

def make_space(name: str, container_tab_id: int = 0) -> Dict:
    return {
        "uuid": "{" + str(uuid.uuid4()) + "}",
        "name": name,
        "theme": {"type": "gradient", "gradientColors": [], "opacity": 0.5, "texture": 0},
        "containerTabId": container_tab_id,
        "hasCollapsedPinnedTabs": False,
    }


def make_tab(
    url: str,
    title: str,
    workspace_uuid: str,
    *,
    is_essential: bool = False,
    is_empty: bool = False,
    container_id: int = 0,
    group_id: Optional[str] = None,
) -> Dict:
    tab: Dict[str, Any] = {
        "entries": [{"url": url, "title": title}],
        "pinned": True,
        "hidden": False,
        "zenWorkspace": workspace_uuid,
        "zenSyncId": "1234567890-1",
        "zenEssential": is_essential,
        "zenIsEmpty": is_empty,
        "userContextId": container_id,
        "attributes": {},
        "index": 1,
    }
    if group_id:
        tab["groupId"] = group_id
    return tab


def make_folder(name: str, workspace_uuid: str, folder_id: Optional[str] = None) -> Dict:
    return {
        "id": folder_id or ("{" + str(uuid.uuid4()) + "}"),
        "name": name,
        "pinned": True,
        "workspaceId": workspace_uuid,
        "collapsed": False,
        "saveOnWindowClose": True,
        "parentId": None,
        "emptyTabIds": [],
        "splitViewGroup": False,
        "prevSiblingInfo": None,
        "userIcon": "",
    }


def make_sessions_file(
    path: Path,
    spaces: Optional[List[Dict]] = None,
    tabs: Optional[List[Dict]] = None,
    folders: Optional[List[Dict]] = None,
    groups: Optional[List[Dict]] = None,
) -> None:
    data = {
        "lastCollected": 0,
        "spaces": spaces or [],
        "tabs": tabs or [],
        "folders": folders or [],
        "groups": groups or [],
        "splitViewData": [],
    }
    write_mozlz4(path, data)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_profile(tmp_path: Path) -> Path:
    """A temporary directory that looks like a minimal Zen profile."""
    (tmp_path / "places.sqlite").touch()
    return tmp_path


@pytest.fixture()
def sessions_file(tmp_profile: Path) -> Path:
    return tmp_profile / "zen-sessions.jsonlz4"


@pytest.fixture()
def empty_sessions(sessions_file: Path) -> Path:
    make_sessions_file(sessions_file)
    return sessions_file


# ---------------------------------------------------------------------------
# Arc export data factory
# ---------------------------------------------------------------------------

def make_arc_export(spaces: Optional[List[Dict]] = None) -> Dict:
    return {"spaces": spaces or []}


def make_arc_space_data(
    space_name: str,
    profile: str = "Profile 1",
    pinned_tabs: Optional[List[Dict]] = None,
    folders: Optional[List[Dict]] = None,
) -> Dict:
    return {
        "space_name": space_name,
        "profile": profile,
        "pinned_tabs": pinned_tabs or [],
        "folders": folders or [],
        "open_tabs": [],
    }


def make_arc_tab_data(
    url: str,
    title: str,
    *,
    is_essential: bool = False,
    folder_path: Optional[List[str]] = None,
    tab_id: Optional[str] = None,
) -> Dict:
    return {
        "url": url,
        "title": title,
        "is_essential": is_essential,
        "folder_path": folder_path or [],
        "tab_id": tab_id or str(uuid.uuid4()),
        "space_id": "test-space",
        "space_name": "Test",
        "parent_id": "",
        "index": 0,
    }
