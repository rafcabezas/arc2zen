#!/usr/bin/env python3
"""
Zen Sessions Importer

Imports Arc pinned tabs into Zen's current session storage file:
`zen-sessions.jsonlz4`.
"""

from __future__ import annotations

import ctypes
import json
import logging
import random
import time
import uuid
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class MozLz4Codec:
    """Encode/decode Mozilla LZ4 (`mozLz40\\0`) session files."""

    HEADER = b"mozLz40\0"

    def __init__(self):
        self._lz4_block = None
        self._lib = None
        self._try_load_python_lz4()
        if self._lz4_block is None:
            self._try_load_ctypes_lz4()

    def _try_load_python_lz4(self):
        try:
            import lz4.block  # type: ignore

            self._lz4_block = lz4.block
        except Exception:
            self._lz4_block = None

    def _try_load_ctypes_lz4(self):
        candidates = [
            "liblz4.dylib",
            "/opt/homebrew/lib/liblz4.dylib",
            "liblz4.so.1",
            "liblz4.so",
            "lz4.dll",
        ]
        for candidate in candidates:
            try:
                self._lib = ctypes.CDLL(candidate)
                break
            except OSError:
                continue

        if self._lib is None:
            return

        self._lib.LZ4_compressBound.argtypes = [ctypes.c_int]
        self._lib.LZ4_compressBound.restype = ctypes.c_int
        self._lib.LZ4_compress_default.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.LZ4_compress_default.restype = ctypes.c_int
        self._lib.LZ4_decompress_safe.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self._lib.LZ4_decompress_safe.restype = ctypes.c_int

    def _compress(self, raw: bytes) -> bytes:
        if self._lz4_block is not None:
            return self._lz4_block.compress(raw, store_size=False)

        if self._lib is None:
            raise RuntimeError("No LZ4 backend available (python-lz4 or liblz4)")

        input_size = len(raw)
        src = ctypes.create_string_buffer(raw, input_size)
        bound = int(self._lib.LZ4_compressBound(input_size))
        dst = ctypes.create_string_buffer(bound)
        compressed_size = int(
            self._lib.LZ4_compress_default(
                ctypes.cast(src, ctypes.c_void_p),
                ctypes.cast(dst, ctypes.c_void_p),
                input_size,
                bound,
            )
        )
        if compressed_size <= 0:
            raise RuntimeError("LZ4 compression failed")
        return dst.raw[:compressed_size]

    def _decompress(self, payload: bytes, expected_size: int) -> bytes:
        if self._lz4_block is not None:
            return self._lz4_block.decompress(payload, uncompressed_size=expected_size)

        if self._lib is None:
            raise RuntimeError("No LZ4 backend available (python-lz4 or liblz4)")

        src = ctypes.create_string_buffer(payload, len(payload))
        dst = ctypes.create_string_buffer(expected_size)
        decoded_size = int(
            self._lib.LZ4_decompress_safe(
                ctypes.cast(src, ctypes.c_void_p),
                ctypes.cast(dst, ctypes.c_void_p),
                len(payload),
                expected_size,
            )
        )
        if decoded_size < 0:
            raise RuntimeError("LZ4 decompression failed")
        return dst.raw[:decoded_size]

    def encode_mozlz4(self, raw: bytes) -> bytes:
        compressed = self._compress(raw)
        return self.HEADER + len(raw).to_bytes(4, "little") + compressed

    def decode_mozlz4(self, blob: bytes) -> bytes:
        if not blob.startswith(self.HEADER):
            raise ValueError("Not a Mozilla LZ4 file")
        expected_size = int.from_bytes(blob[8:12], "little")
        return self._decompress(blob[12:], expected_size)


class ZenSessionsImporter:
    """Imports Arc pinned tabs into Zen's `zen-sessions.jsonlz4` file."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.session_file = zen_profile_path / "zen-sessions.jsonlz4"
        self.codec = MozLz4Codec()
        self._id_counter = random.randint(1000, 9999)
        self.last_stats = {
            "spaces_created": 0,
            "folders_created": 0,
            "tabs_imported": 0,
            "tabs_skipped": 0,
        }

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _new_runtime_id(self) -> str:
        self._id_counter += 1
        return f"{self._now_ms()}-{self._id_counter}"

    def _new_uuid(self) -> str:
        return "{" + str(uuid.uuid4()) + "}"

    def _default_session_data(self) -> Dict:
        return {
            "spaces": [],
            "lastCollected": self._now_ms(),
            "tabs": [],
            "folders": [],
            "splitViewData": [],
            "groups": [],
        }

    def _normalize_session_data(self, data: Dict) -> Dict:
        normalized = dict(data)
        if not isinstance(normalized.get("spaces"), list):
            normalized["spaces"] = []
        if not isinstance(normalized.get("tabs"), list):
            normalized["tabs"] = []
        if not isinstance(normalized.get("folders"), list):
            normalized["folders"] = []
        if not isinstance(normalized.get("splitViewData"), list):
            normalized["splitViewData"] = []
        if not isinstance(normalized.get("groups"), list):
            normalized["groups"] = []
        if "lastCollected" not in normalized:
            normalized["lastCollected"] = self._now_ms()
        return normalized

    def load_session_data(self) -> Dict:
        if not self.session_file.exists():
            return self._default_session_data()

        try:
            raw = self.session_file.read_bytes()
            decoded = self.codec.decode_mozlz4(raw)
            data = json.loads(decoded.decode("utf-8"))
            return self._normalize_session_data(data)
        except Exception as e:
            logger.warning(f"Failed to read existing zen-sessions file, using defaults: {e}")
            return self._default_session_data()

    def save_session_data(self, data: Dict):
        normalized = self._normalize_session_data(data)
        payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        encoded = self.codec.encode_mozlz4(payload)
        self.session_file.write_bytes(encoded)

    def _extract_tab_url_title(self, tab: Dict) -> Tuple[Optional[str], str]:
        entries = tab.get("entries") or []
        if not entries:
            return None, ""
        entry = entries[0] or {}
        url = entry.get("url")
        title = entry.get("title") or tab.get("title") or url or ""
        return url, title

    def _space_theme(self, color: Optional[Dict]) -> Optional[Dict]:
        if not color:
            return None
        r = max(0, min(255, int(float(color.get("r", 0)) * 255)))
        g = max(0, min(255, int(float(color.get("g", 0)) * 255)))
        b = max(0, min(255, int(float(color.get("b", 0)) * 255)))
        return {
            "type": "gradient",
            "gradientColors": [
                {
                    "c": [r, g, b],
                    "isCustom": False,
                    "algorithm": "floating",
                    "isPrimary": True,
                    "lightness": "75",
                    "position": {"x": 228, "y": 253},
                    "type": "explicit-lightness",
                }
            ],
            "opacity": 1.0,
            "texture": 0,
        }

    def _build_space_entry(self, name: str, container_id: int, icon: Optional[str], color: Optional[Dict], position: int):
        entry = {
            "uuid": self._new_uuid(),
            "name": name,
            "containerTabId": container_id,
            "position": position,
            "theme": self._space_theme(color),
            "hasCollapsedPinnedTabs": False,
        }
        if icon:
            entry["icon"] = icon
        return entry

    def _build_tab_entry(
        self,
        url: str,
        title: str,
        workspace_uuid: str,
        container_id: int,
        group_id: Optional[str],
        is_essential: bool,
    ) -> Dict:
        entry = {"url": url, "title": title, "triggeringPrincipal_base64": "{\"3\":{}}"}
        now = self._now_ms()
        sync_id = self._new_runtime_id()
        return {
            "entries": [entry],
            "lastAccessed": now,
            "pinned": True,
            "hidden": False,
            "groupId": group_id,
            "zenWorkspace": workspace_uuid,
            "zenSyncId": sync_id,
            "zenEssential": bool(is_essential),
            "zenDefaultUserContextId": None,
            "zenPinnedIcon": None,
            "zenIsEmpty": False,
            "zenHasStaticIcon": False,
            "zenGlanceId": None,
            "zenIsGlance": False,
            "_zenPinnedInitialState": {"entry": entry, "image": None},
            "zenLiveFolderItemId": None,
            "searchMode": None,
            "userContextId": container_id,
            "attributes": {},
            "index": 1,
            "userTypedValue": "",
            "userTypedClear": 0,
            "image": None,
        }

    def _index_existing_folders(self, data: Dict):
        by_key: Dict[Tuple[str, Optional[str], str], str] = {}
        by_id: Dict[str, Dict] = {}
        path_to_id: Dict[Tuple[str, str], str] = {}

        for folder in data.get("folders", []):
            folder_id = folder.get("id")
            workspace_id = folder.get("workspaceId")
            name = folder.get("name")
            if not folder_id or not workspace_id or not name:
                continue
            by_id[folder_id] = folder
            by_key[(workspace_id, folder.get("parentId"), name)] = folder_id

        memo: Dict[str, str] = {}

        def resolve_path(folder_id: str) -> Optional[str]:
            if folder_id in memo:
                return memo[folder_id]
            folder = by_id.get(folder_id)
            if not folder:
                return None
            name = folder.get("name")
            parent = folder.get("parentId")
            if parent:
                parent_path = resolve_path(parent)
                if parent_path:
                    memo[folder_id] = f"{parent_path}/{name}"
                else:
                    memo[folder_id] = str(name)
            else:
                memo[folder_id] = str(name)
            return memo[folder_id]

        for folder_id, folder in by_id.items():
            path = resolve_path(folder_id)
            if path:
                path_to_id[(folder["workspaceId"], path)] = folder_id

        return by_key, path_to_id

    def _create_folder(self, data: Dict, workspace_uuid: str, name: str, parent_id: Optional[str]) -> str:
        folder_id = self._new_runtime_id()
        now = self._now_ms()
        folder = {
            "pinned": True,
            "splitViewGroup": False,
            "id": folder_id,
            "name": name,
            "collapsed": False,
            "saveOnWindowClose": True,
            "parentId": parent_id,
            "prevSiblingInfo": None,
            "emptyTabIds": [],
            "userIcon": "",
            "workspaceId": workspace_uuid,
        }
        group = {
            "pinned": True,
            "splitView": False,
            "id": folder_id,
            "name": name,
            "color": "zen-workspace-color",
            "collapsed": False,
            "saveOnWindowClose": True,
            "saved": True,
            "closedAt": now,
            "windowClosedId": 0,
            "tabs": [],
        }
        data["folders"].append(folder)
        data["groups"].append(group)
        self.last_stats["folders_created"] += 1
        return folder_id

    def import_arc_pinned_tabs(
        self, arc_export_data: Dict, container_mappings: Dict[str, int], dry_run: bool = False
    ) -> Optional[Dict[str, str]]:
        try:
            self.last_stats = {
                "spaces_created": 0,
                "folders_created": 0,
                "tabs_imported": 0,
                "tabs_skipped": 0,
            }
            data = self.load_session_data()

            spaces = data.get("spaces", [])
            by_space_name = {space.get("name"): space for space in spaces if space.get("name")}
            next_position = max([int(space.get("position", 0)) for space in spaces] or [0]) + 100

            workspace_mappings: Dict[str, str] = {}
            for arc_space in arc_export_data.get("spaces", []):
                space_name = arc_space["space_name"]
                existing_space = by_space_name.get(space_name)
                if existing_space:
                    workspace_mappings[space_name] = existing_space["uuid"]
                    continue
                if dry_run:
                    workspace_mappings[space_name] = self._new_uuid()
                    self.last_stats["spaces_created"] += 1
                    continue
                new_space = self._build_space_entry(
                    space_name,
                    int(container_mappings.get(space_name, 0)),
                    arc_space.get("icon"),
                    arc_space.get("color"),
                    next_position,
                )
                next_position += 100
                spaces.append(new_space)
                by_space_name[space_name] = new_space
                workspace_mappings[space_name] = new_space["uuid"]
                self.last_stats["spaces_created"] += 1

            folder_by_key, folder_by_path = self._index_existing_folders(data)

            existing_tab_keys = set()
            for tab in data.get("tabs", []):
                if not tab.get("pinned"):
                    continue
                workspace_uuid = tab.get("zenWorkspace")
                url, title = self._extract_tab_url_title(tab)
                if workspace_uuid and url:
                    existing_tab_keys.add((workspace_uuid, url, title))

            for arc_space in arc_export_data.get("spaces", []):
                space_name = arc_space["space_name"]
                workspace_uuid = workspace_mappings.get(space_name)
                if not workspace_uuid:
                    continue
                container_id = int(container_mappings.get(space_name, 0))

                # Build folder structures first so tabs can reference groupId.
                local_path_to_folder: Dict[str, str] = {}
                sorted_folders = sorted(arc_space.get("folders", []), key=lambda f: f.get("index", 0))
                arc_id_to_session_id: Dict[str, str] = {}
                arc_id_to_path: Dict[str, str] = {}
                for folder in sorted_folders:
                    folder_name = folder.get("title")
                    if not folder_name:
                        continue
                    arc_folder_id = folder.get("folder_id")
                    parent_arc_id = folder.get("parent_id")
                    parent_session_id = arc_id_to_session_id.get(parent_arc_id) if parent_arc_id else None
                    parent_path = arc_id_to_path.get(parent_arc_id, "") if parent_arc_id else ""
                    path = f"{parent_path}/{folder_name}".strip("/")

                    key = (workspace_uuid, parent_session_id, folder_name)
                    if key in folder_by_key:
                        folder_id = folder_by_key[key]
                    elif dry_run:
                        folder_id = self._new_runtime_id()
                        self.last_stats["folders_created"] += 1
                    else:
                        folder_id = self._create_folder(data, workspace_uuid, folder_name, parent_session_id)
                        folder_by_key[key] = folder_id

                    if arc_folder_id:
                        arc_id_to_session_id[arc_folder_id] = folder_id
                        arc_id_to_path[arc_folder_id] = path
                    local_path_to_folder[path] = folder_id
                    folder_by_path[(workspace_uuid, path)] = folder_id

                for tab_data in arc_space.get("pinned_tabs", []):
                    url = tab_data.get("url")
                    title = tab_data.get("title") or url or ""
                    if not url:
                        self.last_stats["tabs_skipped"] += 1
                        continue

                    tab_key = (workspace_uuid, url, title)
                    if tab_key in existing_tab_keys:
                        self.last_stats["tabs_skipped"] += 1
                        continue

                    folder_path = tab_data.get("folder_path") or []
                    group_id = None
                    if folder_path:
                        folder_path_str = "/".join(folder_path)
                        group_id = local_path_to_folder.get(folder_path_str) or folder_by_path.get(
                            (workspace_uuid, folder_path_str)
                        )

                    if not dry_run:
                        tab = self._build_tab_entry(
                            url=url,
                            title=title,
                            workspace_uuid=workspace_uuid,
                            container_id=container_id,
                            group_id=group_id,
                            is_essential=bool(tab_data.get("is_essential")),
                        )
                        data["tabs"].append(tab)

                    existing_tab_keys.add(tab_key)
                    self.last_stats["tabs_imported"] += 1

            if dry_run:
                logger.info(
                    "🧪 Dry run: would create %d spaces, %d folders, import %d tabs (skip %d)",
                    self.last_stats["spaces_created"],
                    self.last_stats["folders_created"],
                    self.last_stats["tabs_imported"],
                    self.last_stats["tabs_skipped"],
                )
                return workspace_mappings

            data["lastCollected"] = self._now_ms()
            self.save_session_data(data)
            logger.info(
                "✅ Session import: created %d spaces, %d folders, imported %d tabs (skip %d)",
                self.last_stats["spaces_created"],
                self.last_stats["folders_created"],
                self.last_stats["tabs_imported"],
                self.last_stats["tabs_skipped"],
            )
            return workspace_mappings

        except Exception as e:
            logger.error(f"Failed to import Arc pinned tabs into zen-sessions.jsonlz4: {e}")
            return None
