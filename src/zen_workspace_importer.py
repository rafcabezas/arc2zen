#!/usr/bin/env python3
"""
Zen Workspace Importer

Creates actual Zen workspaces for each Arc space and properly assigns pinned tabs.
"""

import sqlite3
import uuid
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

logger = logging.getLogger(__name__)

class ZenWorkspaceImporter:
    """Creates Zen workspaces and properly assigns pinned tabs."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.places_db = zen_profile_path / "places.sqlite"
        self.prefs_file = zen_profile_path / "prefs.js"

    def get_existing_workspaces(self) -> Dict[str, Dict]:
        """Get existing workspaces from zen_workspaces table."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT uuid, name, container_id, position
                    FROM zen_workspaces
                """)

                workspaces = {}
                for uuid_str, name, container_id, position in cursor.fetchall():
                    workspaces[uuid_str] = {
                        'name': name,
                        'container_id': container_id,
                        'position': position
                    }

                return workspaces

        except Exception as e:
            logger.error(f"Failed to get existing workspaces: {e}")
            return {}

    def _map_arc_icon_to_zen(self, arc_icon: Optional[str]) -> Optional[str]:
        """Map Arc emoji icons to Zen icon strings.

        Zen appears to support Unicode emojis directly for custom workspaces,
        so we return the original Arc emoji instead of mapping to string identifiers.
        """
        return arc_icon  # Use the original Arc emoji directly

    def create_workspace(self, name: str, container_id: int, position: int = 1000,
                        icon: Optional[str] = None) -> Optional[str]:
        """Create a new workspace in zen_workspaces table."""
        workspace_uuid = "{" + str(uuid.uuid4()) + "}"
        timestamp = int(datetime.now().timestamp() * 1000)

        # Map Arc icon to Zen icon if provided
        zen_icon = self._map_arc_icon_to_zen(icon)

        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO zen_workspaces (
                        uuid, name, container_id, position, created_at, updated_at, icon
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (workspace_uuid, name, container_id, position, timestamp, timestamp, zen_icon))

                # Add to changes table
                cursor.execute("""
                    INSERT OR REPLACE INTO zen_workspaces_changes (uuid, timestamp)
                    VALUES (?, ?)
                """, (workspace_uuid, timestamp))

                icon_info = f" with icon: {zen_icon}" if zen_icon else ""
                logger.info(f"✅ Created workspace: {name} ({workspace_uuid}){icon_info}")
                return workspace_uuid

        except Exception as e:
            logger.error(f"Failed to create workspace '{name}': {e}")
            return None

    def update_workspace_icon(self, workspace_uuid: str, icon: Optional[str]) -> bool:
        """Update the icon for an existing workspace."""
        if not icon:
            return True  # Nothing to update

        # Map Arc icon to Zen icon
        zen_icon = self._map_arc_icon_to_zen(icon)
        timestamp = int(datetime.now().timestamp() * 1000)

        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE zen_workspaces
                    SET icon = ?, updated_at = ?
                    WHERE uuid = ?
                """, (zen_icon, timestamp, workspace_uuid))

                # Add to changes table
                cursor.execute("""
                    INSERT OR REPLACE INTO zen_workspaces_changes (uuid, timestamp)
                    VALUES (?, ?)
                """, (workspace_uuid, timestamp))

                logger.info(f"🎨 Updated icon for workspace {workspace_uuid}: {zen_icon}")
                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to update workspace icon for {workspace_uuid}: {e}")
            return False

    def update_pinned_tabs_workspace(self, old_workspace_uuid: str, new_workspace_uuid: str) -> bool:
        """Update pinned tabs to use the new workspace UUID."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE zen_pins
                    SET workspace_uuid = ?
                    WHERE workspace_uuid = ?
                """, (new_workspace_uuid, old_workspace_uuid))

                # Update changes table
                cursor.execute("""
                    INSERT OR REPLACE INTO zen_pins_changes (uuid, timestamp)
                    SELECT uuid, ? FROM zen_pins WHERE workspace_uuid = ?
                """, (int(datetime.now().timestamp() * 1000), new_workspace_uuid))

                conn.commit()
                logger.info(f"📌 Updated pinned tabs from {old_workspace_uuid} to {new_workspace_uuid}")
                return True

        except Exception as e:
            logger.error(f"Failed to update pinned tabs workspace: {e}")
            return False

    def set_active_workspace(self, workspace_uuid: str) -> bool:
        """Set the active workspace in prefs.js."""
        try:
            # Read current prefs
            prefs_content = self.prefs_file.read_text()

            # Find and replace the active workspace preference
            import re
            pattern = r'user_pref\("zen\.workspaces\.active", "[^"]*"\)'
            replacement = f'user_pref("zen.workspaces.active", "{workspace_uuid}")'

            if re.search(pattern, prefs_content):
                new_content = re.sub(pattern, replacement, prefs_content)
            else:
                # Add the preference if it doesn't exist
                new_content = prefs_content.rstrip() + f'\nuser_pref("zen.workspaces.active", "{workspace_uuid}");\n'

            # Write back
            self.prefs_file.write_text(new_content)
            logger.info(f"🎯 Set active workspace to: {workspace_uuid}")
            return True

        except Exception as e:
            logger.error(f"Failed to set active workspace: {e}")
            return False

    def import_arc_workspaces(self, arc_export_data: Dict, container_mappings: Dict[str, int],
                            workspace_mappings: Dict[str, str] = None, dry_run: bool = False) -> bool:
        """Import Arc spaces as actual Zen workspaces.

        Args:
            workspace_mappings: Optional mapping of space names to temporary workspace UUIDs
                              from pinned tab import. If provided, uses these mappings directly.
        """
        try:
            logger.info("🏗️ Creating Zen workspaces for Arc spaces...")

            if dry_run:
                logger.info("🧪 DRY RUN - No database changes will be made")
                return True

            # Get existing workspaces
            existing_workspaces = self.get_existing_workspaces()
            logger.info(f"Found {len(existing_workspaces)} existing workspaces")

            # Create or use existing workspace mappings
            final_workspace_mappings = {}
            temp_to_final_mappings = {}
            position = 1000  # Start position for new workspaces

            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                space_icon = space.get('icon')  # Get icon from Arc data
                container_id = container_mappings.get(space_name, 1)

                # Check if workspace already exists
                existing_uuid = None
                for uuid_str, workspace_info in existing_workspaces.items():
                    if workspace_info['name'] == space_name:
                        existing_uuid = uuid_str
                        break

                if existing_uuid:
                    logger.info(f"  ✅ Using existing workspace: {space_name}")
                    final_workspace_mappings[space_name] = existing_uuid
                    # Update icon for existing workspace
                    if space_icon:
                        self.update_workspace_icon(existing_uuid, space_icon)
                else:
                    # Create new workspace with icon
                    workspace_uuid = self.create_workspace(space_name, container_id, position, space_icon)
                    if workspace_uuid:
                        final_workspace_mappings[space_name] = workspace_uuid
                        position += 100  # Increment position for next workspace
                    else:
                        logger.warning(f"  ❌ Failed to create workspace for: {space_name}")

            # Map temporary workspace UUIDs to final workspace UUIDs
            if workspace_mappings:
                # Use the provided mappings from pinned tab import
                for space_name, temp_uuid in workspace_mappings.items():
                    final_uuid = final_workspace_mappings.get(space_name)
                    if final_uuid:
                        temp_to_final_mappings[temp_uuid] = final_uuid
                        logger.info(f"  📌 Mapping {temp_uuid} -> {final_uuid} ({space_name})")
            else:
                # Fallback: try to find temporary workspace UUIDs from database
                with sqlite3.connect(self.places_db) as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT DISTINCT workspace_uuid FROM zen_pins
                        WHERE workspace_uuid NOT IN (SELECT uuid FROM zen_workspaces)
                    """)

                    temp_workspace_uuids = [row[0] for row in cursor.fetchall()]

                    # Try to map temporary UUIDs to final workspace UUIDs by space name
                    for temp_uuid in temp_workspace_uuids:
                        # Try to find which space this temporary UUID belongs to
                        cursor.execute("""
                            SELECT title FROM zen_pins
                            WHERE workspace_uuid = ? AND is_group = 1
                            LIMIT 1
                        """, (temp_uuid,))

                        result = cursor.fetchone()
                        if result:
                            space_name = result[0]
                            final_uuid = final_workspace_mappings.get(space_name)
                            if final_uuid:
                                temp_to_final_mappings[temp_uuid] = final_uuid
                                logger.info(f"  📌 Mapping {temp_uuid} -> {final_uuid} ({space_name})")

            # Update pinned tabs to use correct workspace UUIDs
            for temp_uuid, final_uuid in temp_to_final_mappings.items():
                self.update_pinned_tabs_workspace(temp_uuid, final_uuid)

            # Set the first workspace as active
            if final_workspace_mappings:
                first_workspace_uuid = list(final_workspace_mappings.values())[0]
                self.set_active_workspace(first_workspace_uuid)

            logger.info(f"✅ Successfully created {len(final_workspace_mappings)} workspaces")
            return True

        except Exception as e:
            logger.error(f"Failed to import Arc workspaces: {e}")
            return False

    def clear_temporary_workspaces(self) -> bool:
        """Clear workspaces created during import (for re-import)."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()

                # Find workspaces that might be temporary (created by our import)
                cursor.execute("""
                    SELECT uuid FROM zen_workspaces
                    WHERE name LIKE 'Arc Import%' OR name LIKE 'Temporary%'
                """)

                temp_workspaces = [row[0] for row in cursor.fetchall()]

                if temp_workspaces:
                    placeholders = ",".join(["?" for _ in temp_workspaces])

                    # Delete from zen_workspaces
                    cursor.execute(f"""
                        DELETE FROM zen_workspaces WHERE uuid IN ({placeholders})
                    """, temp_workspaces)

                    # Delete from zen_workspaces_changes
                    cursor.execute(f"""
                        DELETE FROM zen_workspaces_changes WHERE uuid IN ({placeholders})
                    """, temp_workspaces)

                    conn.commit()
                    logger.info(f"🧹 Cleared {len(temp_workspaces)} temporary workspaces")

                return True

        except Exception as e:
            logger.error(f"Failed to clear temporary workspaces: {e}")
            return False