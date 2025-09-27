#!/usr/bin/env python3
"""
Zen Pinned Tab Importer

Imports Arc pinned tabs directly into Zen's zen_pins database table,
creating proper folder hierarchy and workspace assignments.
"""

import sqlite3
import uuid
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime
import json

logger = logging.getLogger(__name__)

@dataclass
class ZenPinnedTab:
    """Represents a pinned tab in Zen."""
    uuid: str
    title: str
    url: str
    container_id: int
    workspace_uuid: str
    position: int
    is_essential: bool = False
    is_group: bool = False
    parent_uuid: Optional[str] = None
    edited_title: bool = False
    is_folder_collapsed: bool = False
    folder_icon: Optional[str] = None

@dataclass
class ZenFolder:
    """Represents a folder in Zen's pinned tabs."""
    uuid: str
    title: str
    container_id: int
    workspace_uuid: str
    position: int
    parent_uuid: Optional[str] = None
    is_collapsed: bool = False
    icon: Optional[str] = None

class ZenPinnedTabImporter:
    """Imports Arc pinned tabs into Zen's zen_pins database."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.places_db = zen_profile_path / "places.sqlite"

    def get_workspace_uuids(self) -> Dict[int, str]:
        """Get workspace UUIDs for each container from existing pinned tabs."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT container_id, workspace_uuid
                    FROM zen_pins
                    WHERE workspace_uuid IS NOT NULL
                """)

                mappings = {}
                for container_id, workspace_uuid in cursor.fetchall():
                    mappings[container_id] = workspace_uuid

                return mappings

        except Exception as e:
            logger.error(f"Failed to get workspace UUIDs: {e}")
            return {}

    def create_workspace_uuid_mappings(self, container_mappings: Dict[str, int]) -> Dict[str, str]:
        """Create new workspace UUIDs for each Arc space."""
        workspace_mappings = {}

        for space_name, container_id in container_mappings.items():
            # Always create new workspace UUIDs for imported Arc spaces
            workspace_uuid = "{" + str(uuid.uuid4()) + "}"
            workspace_mappings[space_name] = workspace_uuid
            logger.info(f"  📁 Creating new workspace for {space_name}: {workspace_uuid}")

        return workspace_mappings

    def get_next_position(self, workspace_uuid: str) -> int:
        """Get the next position for a pinned tab in a workspace."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT MAX(position) FROM zen_pins WHERE workspace_uuid = ?
                """, (workspace_uuid,))

                result = cursor.fetchone()
                return (result[0] or 0) + 1

        except Exception as e:
            logger.error(f"Failed to get next position: {e}")
            return 1

    def create_folder(self, title: str, container_id: int, workspace_uuid: str,
                     position: int, parent_uuid: Optional[str] = None) -> str:
        """Create a folder in zen_pins and return its UUID."""
        folder_uuid = "{" + str(uuid.uuid4()) + "}"
        timestamp = int(datetime.now().timestamp() * 1000)

        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO zen_pins (
                        uuid, title, url, container_id, workspace_uuid, position,
                        is_essential, is_group, folder_parent_uuid, created_at, updated_at,
                        edited_title, is_folder_collapsed, folder_icon
                    ) VALUES (?, ?, NULL, ?, ?, ?, 0, 1, ?, ?, ?, 0, 0, NULL)
                """, (folder_uuid, title, container_id, workspace_uuid, position,
                      parent_uuid, timestamp, timestamp))

                # Add to changes table
                cursor.execute("""
                    INSERT OR REPLACE INTO zen_pins_changes (uuid, timestamp)
                    VALUES (?, ?)
                """, (folder_uuid, timestamp))

                conn.commit()
                return folder_uuid

        except Exception as e:
            logger.error(f"Failed to create folder '{title}': {e}")
            return ""

    def create_pinned_tab(self, tab: ZenPinnedTab) -> bool:
        """Create a pinned tab in zen_pins."""
        timestamp = int(datetime.now().timestamp() * 1000)

        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO zen_pins (
                        uuid, title, url, container_id, workspace_uuid, position,
                        is_essential, is_group, folder_parent_uuid, created_at, updated_at,
                        edited_title, is_folder_collapsed, folder_icon
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0, NULL)
                """, (tab.uuid, tab.title, tab.url, tab.container_id, tab.workspace_uuid,
                      tab.position, int(tab.is_essential), tab.parent_uuid,
                      timestamp, timestamp, int(tab.edited_title)))

                # Add to changes table
                cursor.execute("""
                    INSERT OR REPLACE INTO zen_pins_changes (uuid, timestamp)
                    VALUES (?, ?)
                """, (tab.uuid, timestamp))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to create pinned tab '{tab.title}': {e}")
            return False

    def build_folder_hierarchy(self, space_name: str, pinned_tabs: List[Dict],
                              container_id: int, workspace_uuid: str) -> Dict[str, str]:
        """Build folder hierarchy and return path -> uuid mapping."""
        folder_uuids = {}
        position = self.get_next_position(workspace_uuid)

        # DO NOT create root folder for the space - tabs go directly to workspace root
        # folder_uuids[""] = None  # Root level (no folder)

        # Collect all unique folder paths
        all_paths = set()
        for tab in pinned_tabs:
            folder_path = tab.get('folder_path', [])
            for i in range(len(folder_path)):
                path = "/".join(folder_path[:i+1])
                all_paths.add(path)

        # Create folders in Arc order (preserve tab ordering for folder creation)
        # Collect folder paths in the order they appear in tabs
        ordered_paths = []
        seen_paths = set()
        for tab in pinned_tabs:
            folder_path = tab.get('folder_path', [])
            for i in range(len(folder_path)):
                path = "/".join(folder_path[:i+1])
                if path not in seen_paths:
                    ordered_paths.append(path)
                    seen_paths.add(path)

        for path in ordered_paths:
            if path in folder_uuids:
                continue

            path_parts = path.split("/")
            folder_name = path_parts[-1]
            parent_path = "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
            parent_uuid = folder_uuids.get(parent_path)  # None for root level

            folder_uuid = self.create_folder(folder_name, container_id, workspace_uuid, position, parent_uuid)
            if folder_uuid:
                folder_uuids[path] = folder_uuid
                position += 1
                logger.info(f"    📁 Created folder: {folder_name}")

        return folder_uuids

    def get_existing_folders(self, workspace_uuid: str) -> Dict[str, str]:
        """Get existing folders from the database for a workspace."""
        existing_folders = {}
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT uuid, title FROM zen_pins
                    WHERE workspace_uuid = ? AND is_group = 1
                """, (workspace_uuid,))

                for row in cursor.fetchall():
                    folder_uuid, folder_title = row
                    existing_folders[folder_title] = folder_uuid

        except Exception as e:
            logger.error(f"Failed to get existing folders: {e}")

        return existing_folders

    def create_exported_folders(self, folders: List[Dict], container_id: int, workspace_uuid: str) -> Dict[str, str]:
        """Create folders directly from exported folder data, preserving Arc order and hierarchy."""
        folder_uuids = {}
        base_position = self.get_next_position(workspace_uuid)

        # Get existing folders first
        existing_folders = self.get_existing_folders(workspace_uuid)
        folder_uuids.update(existing_folders)

        # Sort folders by their index to preserve Arc ordering
        sorted_folders = sorted(folders, key=lambda f: f.get('index', 0))

        # Create folders in two passes to handle parent-child relationships
        # First pass: create all folders and map their IDs
        folder_id_to_data = {}
        for folder_data in sorted_folders:
            folder_id = folder_data.get('folder_id', '')
            if folder_id:
                folder_id_to_data[folder_id] = folder_data

        # Second pass: create folders in dependency order (parents before children)
        created_folders = set()
        position = base_position

        def create_folder_with_hierarchy(folder_data):
            nonlocal position
            folder_id = folder_data.get('folder_id', '')
            folder_title = folder_data.get('title', 'Untitled Folder')

            # Skip if already exists in database
            if folder_title in existing_folders:
                # Map the existing folder
                folder_uuids[folder_title] = existing_folders[folder_title]
                if folder_id:
                    folder_uuids[folder_id] = existing_folders[folder_title]
                logger.info(f"    📁 Using existing folder: {folder_title}")
                return

            # Skip if already created in this run
            if folder_id in created_folders:
                return

            folder_parent_id = folder_data.get('parent_id', '')

            # Determine parent UUID
            parent_uuid = None
            if folder_parent_id and folder_parent_id in folder_uuids:
                # Parent is another folder
                parent_uuid = folder_uuids[folder_parent_id]
            elif folder_parent_id and folder_parent_id in folder_id_to_data:
                # Parent folder exists but hasn't been created yet - create it first
                create_folder_with_hierarchy(folder_id_to_data[folder_parent_id])
                parent_uuid = folder_uuids.get(folder_parent_id)

            # Create the folder
            folder_uuid = self.create_folder(folder_title, container_id, workspace_uuid, position, parent_uuid)

            if folder_uuid:
                # Map by folder_id for parent-child lookups
                if folder_id:
                    folder_uuids[folder_id] = folder_uuid
                # Also map by title for tab folder_path matching
                folder_uuids[folder_title] = folder_uuid

                created_folders.add(folder_id)
                position += 1

                parent_info = f" (child of {folder_id_to_data.get(folder_parent_id, {}).get('title', 'unknown')})" if parent_uuid else ""
                logger.info(f"    📁 Created folder: {folder_title}{parent_info}")

        # Create all folders with proper hierarchy
        for folder_data in sorted_folders:
            create_folder_with_hierarchy(folder_data)

        return folder_uuids

    def import_arc_pinned_tabs(self, arc_export_data: Dict, container_mappings: Dict[str, int],
                              dry_run: bool = False) -> Dict[str, str]:
        """Import Arc pinned tabs as Zen pinned tabs.

        Returns:
            Dict mapping space names to temporary workspace UUIDs
        """
        try:
            logger.info("📌 Importing Arc pinned tabs into Zen pinned tab system...")

            if dry_run:
                logger.info("🧪 DRY RUN - No database changes will be made")

            # Create workspace mappings
            workspace_mappings = self.create_workspace_uuid_mappings(container_mappings)

            total_tabs = 0
            total_folders = 0

            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                pinned_tabs = space.get('pinned_tabs', [])
                folders = space.get('folders', [])

                # Both tabs and folders are already in correct Arc sidebar order from extraction
                # No sorting needed - preserve original extraction order

                container_id = container_mappings.get(space_name, 1)
                workspace_uuid = workspace_mappings.get(space_name)

                if not workspace_uuid:
                    logger.warning(f"No workspace UUID for space: {space_name}")
                    continue

                logger.info(f"  📁 Processing {space_name}: {len(pinned_tabs)} tabs, {len(folders)} folders (preserving Arc sidebar order)")

                if dry_run:
                    total_tabs += len(pinned_tabs)
                    total_folders += len(folders)
                    continue

                # Create folders directly from exported folder data (preserving Arc order)
                folder_uuids = self.create_exported_folders(folders, container_id, workspace_uuid)
                total_folders += len(folder_uuids)

                # Import pinned tabs using preserved Arc ordering
                base_position = self.get_next_position(workspace_uuid)

                for i, tab_data in enumerate(pinned_tabs):
                    folder_path = tab_data.get('folder_path', [])

                    # For tabs without folders, parent_uuid should be None (workspace root)
                    # For tabs with folders, use the UUID of the last folder in the path (immediate parent)
                    parent_uuid = None
                    if folder_path:
                        # Get the immediate parent folder (last element in the path)
                        immediate_parent = folder_path[-1]
                        parent_uuid = folder_uuids.get(immediate_parent)

                        # If immediate parent not found, try to find any existing folder in the path
                        if not parent_uuid:
                            for folder_name in reversed(folder_path):
                                parent_uuid = folder_uuids.get(folder_name)
                                if parent_uuid:
                                    break

                    # Use sequential position to preserve Arc ordering
                    # Since tabs are already sorted by Arc index, enumerate maintains order
                    position = base_position + i

                    # Check if this is an Essential tab (from Arc's top toolbar)
                    is_essential = tab_data.get('is_essential', False)

                    tab = ZenPinnedTab(
                        uuid="{" + str(uuid.uuid4()) + "}",
                        title=tab_data['title'],
                        url=tab_data['url'],
                        container_id=container_id,
                        workspace_uuid=workspace_uuid,
                        position=position,
                        is_essential=is_essential,
                        parent_uuid=parent_uuid
                    )

                    if self.create_pinned_tab(tab):
                        total_tabs += 1

                logger.info(f"    ✅ Imported {len(pinned_tabs)} pinned tabs")

            if dry_run:
                logger.info(f"🧪 Would import {total_tabs} pinned tabs and create {total_folders} folders")
                return {}
            else:
                logger.info(f"✅ Successfully imported {total_tabs} pinned tabs and {total_folders} folders")
                logger.info("🔄 Restart Zen browser to see your imported pinned tabs")

            return workspace_mappings

        except Exception as e:
            logger.error(f"Failed to import Arc pinned tabs: {e}")
            return {}

    def clear_imported_pins(self, workspace_uuids: List[str]) -> bool:
        """Clear previously imported pins for re-import."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                placeholders = ",".join(["?" for _ in workspace_uuids])
                cursor.execute(f"""
                    DELETE FROM zen_pins WHERE workspace_uuid IN ({placeholders})
                """, workspace_uuids)

                cursor.execute(f"""
                    DELETE FROM zen_pins_changes WHERE uuid IN (
                        SELECT uuid FROM zen_pins WHERE workspace_uuid IN ({placeholders})
                    )
                """, workspace_uuids)

                conn.commit()
                logger.info("🧹 Cleared existing imported pins")
                return True

        except Exception as e:
            logger.error(f"Failed to clear imported pins: {e}")
            return False