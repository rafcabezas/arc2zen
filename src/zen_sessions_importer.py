#!/usr/bin/env python3
"""
Zen Sessions Importer

Imports Arc spaces, pinned tabs, and folders into Zen browser's
zen-sessions.jsonlz4 file. This is the modern storage format used
by Zen 1.18+ which replaced the legacy zen_pins/zen_workspaces SQLite tables.

Format: mozlz4 (8-byte magic + 4-byte LE size + lz4 block compressed JSON)
"""

import json
import re
import struct
import uuid
import time
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

try:
    import lz4.block
except ImportError:
    raise ImportError(
        "The 'lz4' package is required for Zen 1.18+ support.\n"
        "Install it with: pip install lz4"
    )

logger = logging.getLogger(__name__)

MOZLZ4_MAGIC = b'mozLz40\0'


def read_mozlz4(file_path: Path) -> dict:
    """Read and decompress a mozlz4 file, return parsed JSON."""
    with open(file_path, 'rb') as f:
        magic = f.read(8)
        if magic != MOZLZ4_MAGIC:
            raise ValueError(f"Not a mozlz4 file (magic: {magic!r})")
        size = struct.unpack('<I', f.read(4))[0]
        compressed = f.read()
    decompressed = lz4.block.decompress(compressed, uncompressed_size=size)
    return json.loads(decompressed.decode('utf-8'))


def write_mozlz4(file_path: Path, data: dict) -> None:
    """Serialize JSON data and write as mozlz4 file."""
    json_bytes = json.dumps(data, separators=(',', ':')).encode('utf-8')
    compressed = lz4.block.compress(json_bytes, store_size=False)
    with open(file_path, 'wb') as f:
        f.write(MOZLZ4_MAGIC)
        f.write(struct.pack('<I', len(json_bytes)))
        f.write(compressed)


class ZenSessionsImporter:
    """Imports Arc spaces, pinned tabs, and folders into zen-sessions.jsonlz4."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.sessions_file = zen_profile_path / "zen-sessions.jsonlz4"
        self._id_counter = 0

    # --- ID generation ---

    def _generate_space_uuid(self) -> str:
        return "{" + str(uuid.uuid4()) + "}"

    def _generate_sync_id(self) -> str:
        """Generate a TIMESTAMP-NUMBER format ID (used for zenSyncId and folder id)."""
        self._id_counter += 1
        return f"{int(time.time() * 1000)}-{self._id_counter}"

    # --- Backup ---

    def _backup_sessions(self) -> bool:
        if not self.sessions_file.exists():
            return True
        try:
            timestamp = int(datetime.now().timestamp())
            backup_path = self.zen_profile / f"zen-sessions.jsonlz4.backup.{timestamp}"
            shutil.copy2(self.sessions_file, backup_path)
            logger.info(f"  Backed up zen-sessions.jsonlz4 to {backup_path.name}")
            return True
        except Exception as e:
            logger.error(f"Failed to backup zen-sessions.jsonlz4: {e}")
            return False

    # --- Read existing data ---

    def _read_existing(self) -> dict:
        if self.sessions_file.exists():
            try:
                return read_mozlz4(self.sessions_file)
            except Exception as e:
                logger.warning(f"Could not read existing zen-sessions.jsonlz4: {e}")

        return {
            "spaces": [],
            "tabs": [],
            "folders": [],
            "groups": [],
            "splitViewData": [],
            "lastCollected": int(time.time() * 1000),
        }

    # --- Build structures ---

    def _build_space(self, space_data: dict, container_id: int, position: int) -> dict:
        color = space_data.get("color")
        theme = None
        if color:
            rgb = [round(max(0, min(1, color.get(key, 0))) * 255) for key in ("r", "g", "b")]
            theme = {
                "type": "gradient",
                "gradientColors": [{"c": rgb, "isCustom": False, "algorithm": "floating",
                                    "isPrimary": True, "lightness": "75",
                                    "position": {"x": 228, "y": 253},
                                    "type": "explicit-lightness"}],
                "opacity": 1,
                "rotation": None,
                "texture": 0,
            }
        return {
            "uuid": self._generate_space_uuid(),
            "name": space_data["space_name"],
            "icon": space_data.get("icon") or "📁",
            "theme": theme,
            "containerTabId": container_id,
            "position": position,
            "hasCollapsedPinnedTabs": False,
        }

    def _build_tab(self, tab_data: dict, workspace_uuid: str, container_id: int,
                   index: int, folder_id: Optional[str] = None) -> dict:
        url = tab_data.get("url", "")
        title = tab_data.get("title", "")
        is_essential = tab_data.get("is_essential", False)
        timestamp_ms = int(time.time() * 1000)

        tab = {
            "entries": [{"url": url, "title": title}],
            "lastAccessed": timestamp_ms,
            "pinned": True,
            "hidden": False,
            # Zen essential tabs are global to their container, not workspace tabs.
            "zenWorkspace": None if is_essential else workspace_uuid,
            "zenSyncId": self._generate_sync_id(),
            "zenEssential": is_essential,
            "zenDefaultUserContextId": True if is_essential else None,
            "zenPinnedIcon": None,
            "zenIsEmpty": False,
            "zenHasStaticIcon": False,
            "zenGlanceId": None,
            "zenIsGlance": False,
            "_zenPinnedInitialState": {
                "entry": {"url": url, "title": title},
                "image": None,
            },
            "searchMode": None,
            "userContextId": container_id if is_essential else 0,
            "attributes": {},
            "index": index,
        }

        if folder_id:
            tab["groupId"] = folder_id

        return tab

    def _build_folder(self, folder_data: dict, workspace_uuid: str,
                      parent_folder_id: Optional[str] = None) -> dict:
        return {
            "pinned": True,
            "splitViewGroup": False,
            "id": self._generate_sync_id(),
            "name": folder_data.get("title", "Untitled"),
            "collapsed": False,
            "saveOnWindowClose": True,
            "parentId": parent_folder_id,
            "prevSiblingInfo": None,
            "emptyTabIds": [],
            "userIcon": "",
            "workspaceId": workspace_uuid,
        }

    # --- Core import logic ---

    def _process_space(self, space_data: dict, container_id: int, position: int) -> Tuple[dict, List[dict], List[dict]]:
        """Process a single Arc space into Zen space, folders, and tabs.

        Returns (space_dict, folders_list, tabs_list).
        """
        space = self._build_space(space_data, container_id, position)
        workspace_uuid = space["uuid"]

        # Build folders with hierarchy
        arc_folders = space_data.get("folders", [])
        arc_folder_id_to_zen_id: Dict[str, str] = {}
        zen_folders: List[dict] = []

        # Sort folders by index to preserve Arc ordering
        sorted_folders = sorted(arc_folders, key=lambda f: f.get("index", 0))

        # Map arc folder_id to data for parent lookups
        arc_folder_lookup = {f["folder_id"]: f for f in sorted_folders if f.get("folder_id")}

        def create_folder_recursive(folder_data: dict) -> str:
            """Create a folder and its parents first. Returns zen folder ID."""
            arc_id = folder_data.get("folder_id", "")
            if arc_id in arc_folder_id_to_zen_id:
                return arc_folder_id_to_zen_id[arc_id]

            # Create parent first if needed
            parent_zen_id = None
            parent_arc_id = folder_data.get("parent_id", "")
            if parent_arc_id and parent_arc_id in arc_folder_lookup:
                parent_zen_id = create_folder_recursive(arc_folder_lookup[parent_arc_id])

            zen_folder = self._build_folder(folder_data, workspace_uuid, parent_zen_id)
            zen_folders.append(zen_folder)

            if arc_id:
                arc_folder_id_to_zen_id[arc_id] = zen_folder["id"]

            # Also map by title for folder_path lookups from tabs
            arc_folder_id_to_zen_id[folder_data.get("title", "")] = zen_folder["id"]

            return zen_folder["id"]

        for folder_data in sorted_folders:
            create_folder_recursive(folder_data)

        # Build tabs preserving Arc order
        pinned_tabs = space_data.get("pinned_tabs", [])
        zen_tabs: List[dict] = []

        for i, tab_data in enumerate(pinned_tabs):
            url = tab_data.get("url", "")
            if not url:
                continue

            # Resolve folder assignment
            folder_id = None
            folder_path = tab_data.get("folder_path", [])
            if folder_path:
                # Use the immediate parent folder (last element in path)
                immediate_parent = folder_path[-1]
                folder_id = arc_folder_id_to_zen_id.get(immediate_parent)

            zen_tab = self._build_tab(
                tab_data, workspace_uuid, container_id,
                index=i + 1, folder_id=folder_id,
            )
            zen_tabs.append(zen_tab)

        # Create about:blank placeholder tab per folder — Zen requires this for validation
        timestamp_ms = int(time.time() * 1000)
        for folder in zen_folders:
            placeholder_sync_id = self._generate_sync_id()
            placeholder = {
                "entries": [{"url": "about:blank", "triggeringPrincipal_base64": "{\"3\":{}}"}],
                "lastAccessed": timestamp_ms,
                "pinned": True,
                "hidden": False,
                "groupId": folder["id"],
                "zenWorkspace": None,
                "zenSyncId": placeholder_sync_id,
                "zenEssential": False,
                "zenDefaultUserContextId": None,
                "zenPinnedIcon": None,
                "zenIsEmpty": True,
                "zenHasStaticIcon": False,
                "zenGlanceId": None,
                "zenIsGlance": False,
                "searchMode": None,
                "userContextId": 0,
                "attributes": {},
                "index": 1,
            }
            zen_tabs.append(placeholder)
            folder["emptyTabIds"] = [placeholder_sync_id]

        return space, zen_folders, zen_tabs

    def _merge_with_existing(self, existing: dict, new_spaces: List[dict],
                             new_tabs: List[dict], new_folders: List[dict]) -> dict:
        """Replace matching Arc spaces while preserving unrelated Zen data."""
        replacing_names = {space["name"] for space in new_spaces}
        replaced_uuids = {
            space["uuid"] for space in existing.get("spaces", [])
            if space.get("name") in replacing_names
        }
        replaced_folder_ids = {
            folder["id"] for folder in existing.get("folders", [])
            if folder.get("workspaceId") in replaced_uuids
        }
        replaced_essential_urls = {
            tab["entries"][0].get("url") for tab in new_tabs
            if tab.get("zenEssential") and tab.get("entries")
        }
        if replaced_uuids:
            logger.info(f"  Replacing {len(replaced_uuids)} existing Arc space(s)")

        merged = dict(existing)
        merged["spaces"] = [
            space for space in existing.get("spaces", [])
            if space.get("uuid") not in replaced_uuids
        ] + new_spaces
        merged["tabs"] = [
            tab for tab in existing.get("tabs", [])
            if tab.get("zenWorkspace") not in replaced_uuids
            and tab.get("groupId") not in replaced_folder_ids
            and not (tab.get("zenEssential") and replaced_essential_urls)
        ] + new_tabs
        merged["folders"] = [
            folder for folder in existing.get("folders", [])
            if folder.get("workspaceId") not in replaced_uuids
        ] + new_folders
        merged["splitViewData"] = existing.get("splitViewData", [])
        merged["lastCollected"] = int(time.time() * 1000)

        # Zen injects groups into sessionstore. Keep unrelated groups, rebuild
        # migrated groups from the replacement folders.
        all_groups = [
            group for group in existing.get("groups", [])
            if group.get("id") not in replaced_folder_ids
        ]
        existing_group_ids = {group["id"] for group in all_groups}
        for folder in new_folders:
            if folder["id"] not in existing_group_ids:
                all_groups.append({
                    "pinned": True,
                    "splitView": False,
                    "id": folder["id"],
                    "name": folder["name"],
                    "color": "zen-workspace-color",
                    "collapsed": folder.get("collapsed", False),
                    "saveOnWindowClose": True,
                })
        merged["groups"] = all_groups

        return merged

    # --- Sessionstore sync ---

    def _sync_sessionstore(self, merged: dict) -> None:
        """Sync zen-sessions data into sessionstore as a supplementary safety net.

        zen-sessions.jsonlz4 is the authoritative source — on startup, Zen's
        #restoreWindowData() injects its groups/tabs/folders/spaces into Firefox's
        initialState, overwriting whatever was in the sessionstore. This sync
        is belt-and-suspenders: it pre-populates the sessionstore so that even
        if Zen's injection is skipped (e.g. crash recovery path), Firefox still
        has the data it needs to create tab-group DOM elements.
        """
        # Build group entries from all folders
        folder_groups = []
        for folder in merged.get("folders", []):
            folder_groups.append({
                "pinned": True,
                "splitView": False,
                "id": folder["id"],
                "name": folder["name"],
                "color": "zen-workspace-color",
                "collapsed": folder.get("collapsed", False),
                "saveOnWindowClose": True,
            })

        # Try sessionstore files in priority order
        ss_files = [
            self.zen_profile / "sessionstore.jsonlz4",
            self.zen_profile / "sessionstore-backups" / "recovery.jsonlz4",
            self.zen_profile / "sessionstore-backups" / "recovery.baklz4",
        ]

        for ss_path in ss_files:
            if not ss_path.exists():
                continue
            try:
                ss_data = read_mozlz4(ss_path)
            except Exception:
                continue

            updated = False

            # Update every window (open and closed)
            for win_list_key in ("windows", "_closedWindows"):
                for win in ss_data.get(win_list_key, []):
                    # Replace pinned tabs with zen-sessions tabs (ensures consistent zenSyncId + groupId)
                    unpinned = [t for t in win.get("tabs", []) if not t.get("pinned")]
                    win["tabs"] = list(merged.get("tabs", [])) + unpinned

                    # Replace groups with folder groups
                    win["groups"] = list(folder_groups)

                    # Also sync folders and spaces into window data
                    win["folders"] = list(merged.get("folders", []))
                    win["spaces"] = list(merged.get("spaces", []))
                    win["splitViewData"] = list(merged.get("splitViewData", []))

                    updated = True

            if updated:
                try:
                    write_mozlz4(ss_path, ss_data)
                    logger.info(f"  Synced {len(merged['tabs'])} tabs + {len(folder_groups)} groups to {ss_path.name}")
                except Exception as e:
                    logger.warning(f"  Could not sync to {ss_path.name}: {e}")

    # --- Public API ---

    def _enable_container_essentials(self) -> None:
        """Keep Arc Essentials in their original space containers."""
        prefs_path = self.zen_profile / "prefs.js"
        setting = 'user_pref("zen.workspaces.separate-essentials", true);\n'
        try:
            prefs = prefs_path.read_text() if prefs_path.exists() else ""
            prefs, count = re.subn(
                r'user_pref\("zen\.workspaces\.separate-essentials",\s*(?:true|false)\);',
                setting.rstrip(), prefs,
            )
            if not count:
                prefs += setting
            prefs_path.write_text(prefs)
        except OSError as error:
            logger.warning(f"Could not enable global Essentials: {error}")

    def import_arc_data(self, arc_export_data: dict, container_mappings: dict,
                        dry_run: bool = False) -> bool:
        """Import Arc spaces, pinned tabs, and folders into zen-sessions.jsonlz4.

        Args:
            arc_export_data: Parsed Arc export JSON with 'spaces' array.
            container_mappings: Dict mapping space_name -> container userContextId.
            dry_run: If True, log what would happen without writing.

        Returns:
            True on success, False on failure.
        """
        try:
            logger.info("Importing Arc data into zen-sessions.jsonlz4...")

            existing = self._read_existing()
            next_position = max((space.get("position", 0) for space in existing.get("spaces", [])), default=0) + 1000
            all_new_spaces = []
            all_new_tabs = []
            all_new_folders = []

            for space_data in arc_export_data.get("spaces", []):
                space_name = space_data["space_name"]
                container_id = container_mappings.get(space_name, 0)
                space, folders, tabs = self._process_space(space_data, container_id, next_position)
                next_position += 1000
                all_new_spaces.append(space)
                all_new_folders.extend(folders)
                all_new_tabs.extend(tabs)

                logger.info(
                    f"  {space_name}: {len(tabs)} pinned tabs, "
                    f"{len(folders)} folders -> workspace {space['uuid']}"
                )

            if dry_run:
                logger.info(
                    f"DRY RUN: Would import {len(all_new_spaces)} spaces, "
                    f"{len(all_new_tabs)} tabs, {len(all_new_folders)} folders"
                )
                return True

            # Backup existing file
            if not self._backup_sessions():
                logger.warning("Could not backup zen-sessions.jsonlz4, continuing anyway...")

            # Merge with existing data read before building workspace positions
            merged = self._merge_with_existing(
                existing, all_new_spaces, all_new_tabs, all_new_folders
            )

            # Write
            write_mozlz4(self.sessions_file, merged)

            # Sync folder groups to sessionstore so Firefox creates tab-group DOM elements
            self._sync_sessionstore(merged)
            if any(
                tab.get("is_essential")
                for space in arc_export_data.get("spaces", [])
                for tab in space.get("pinned_tabs", [])
            ):
                self._enable_container_essentials()

            logger.info(
                f"Successfully imported {len(all_new_spaces)} spaces, "
                f"{len(all_new_tabs)} pinned tabs, {len(all_new_folders)} folders"
            )
            logger.info("Restart Zen browser to see your imported data")
            return True

        except Exception as e:
            logger.error(f"Failed to import Arc data into zen-sessions: {e}")
            return False
