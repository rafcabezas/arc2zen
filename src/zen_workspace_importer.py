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

    def _convert_rgb_to_zen_theme(self, color: Optional[dict]) -> tuple:
        """Convert Arc RGB color to Zen theme format.

        Uses actual measured Arc color values to match the exact appearance.
        Arc Personal green measured as 0xbbf6da (RGB: 187, 246, 218).

        Args:
            color: Dict with 'r', 'g', 'b' keys (values 0-1)

        Returns:
            Tuple of (theme_type, theme_colors_json) or (None, None)
        """
        if not color or 'r' not in color or 'g' not in color or 'b' not in color:
            return None, None

        r, g, b = color['r'], color['g'], color['b']

        # Arc applies a specific visual transformation to create its subtle appearance
        # Measured examples used to reverse-engineer the formula:
        # Personal (0,0.841,0.404) → 0xbbf6da (187,246,218)
        # WillowTree (0.914,0.703,0) → 0xfbe496 (251,228,150)

        # Arc's transformation appears to create a light pastel by:
        # 1. Setting a high baseline luminosity
        # 2. Adding color tint proportional to original color
        # 3. Different scaling for different color channels

        # Calibrated values based on measured Arc colors
        base_r, base_g, base_b = 185, 225, 150  # Base tint values
        scale_r, scale_g, scale_b = 72, 25, 170  # Color scaling factors

        # Apply Arc's color transformation formula
        final_r = base_r + (r * scale_r)
        final_g = base_g + (g * scale_g)
        final_b = base_b + (b * scale_b)

        # Convert to 0-255 range and clamp
        r_255 = max(0, min(255, int(final_r)))
        g_255 = max(0, min(255, int(final_g)))
        b_255 = max(0, min(255, int(final_b)))

        # Create Zen theme color object to match Arc's appearance
        theme_colors = [{
            "c": [r_255, g_255, b_255],
            "isCustom": False,
            "algorithm": "floating",
            "isPrimary": True,
            "lightness": "75",  # Moderate lightness to show the blended color
            "position": {"x": 228, "y": 253},
            "type": "explicit-lightness"
        }]

        import json
        return "gradient", json.dumps(theme_colors)

    def create_workspace(self, name: str, container_id: int, position: int = 1000,
                        icon: Optional[str] = None, color: Optional[dict] = None) -> Optional[str]:
        """Create a new workspace in zen_workspaces table."""
        workspace_uuid = "{" + str(uuid.uuid4()) + "}"
        timestamp = int(datetime.now().timestamp() * 1000)

        # Map Arc icon and color to Zen format if provided
        zen_icon = self._map_arc_icon_to_zen(icon)
        theme_type, theme_colors = self._convert_rgb_to_zen_theme(color)

        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()

                # Create workspace with icon and theme/color support
                cursor.execute("""
                    INSERT INTO zen_workspaces (
                        uuid, name, container_id, position, created_at, updated_at, icon,
                        theme_type, theme_colors, theme_opacity, theme_rotation, theme_texture
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (workspace_uuid, name, container_id, position, timestamp, timestamp, zen_icon,
                      theme_type, theme_colors, 1.0, 0, 0))

                # Add to changes table
                cursor.execute("""
                    INSERT OR REPLACE INTO zen_workspaces_changes (uuid, timestamp)
                    VALUES (?, ?)
                """, (workspace_uuid, timestamp))

                icon_info = f" with icon: {zen_icon}" if zen_icon else ""
                color_info = f" and theme: {theme_type}" if theme_type else ""
                logger.info(f"✅ Created workspace: {name} ({workspace_uuid}){icon_info}{color_info}")
                return workspace_uuid

        except Exception as e:
            logger.error(f"Failed to create workspace '{name}': {e}")
            return None

    def update_workspace_icon_and_color(self, workspace_uuid: str, icon: Optional[str], color: Optional[dict]) -> bool:
        """Update the icon and color theme for an existing workspace."""
        if not icon and not color:
            return True  # Nothing to update

        # Map Arc icon and color to Zen format
        zen_icon = self._map_arc_icon_to_zen(icon) if icon else None
        theme_type, theme_colors = self._convert_rgb_to_zen_theme(color) if color else (None, None)
        timestamp = int(datetime.now().timestamp() * 1000)

        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()

                # Build dynamic UPDATE query based on what needs to be updated
                updates = []
                params = []

                if zen_icon:
                    updates.append("icon = ?")
                    params.append(zen_icon)

                if theme_type and theme_colors:
                    updates.append("theme_type = ?")
                    updates.append("theme_colors = ?")
                    updates.append("theme_opacity = ?")
                    updates.append("theme_rotation = ?")
                    updates.append("theme_texture = ?")
                    params.extend([theme_type, theme_colors, 1.0, 0, 0])

                updates.append("updated_at = ?")
                params.append(timestamp)
                params.append(workspace_uuid)  # For WHERE clause

                if updates:
                    query = f"UPDATE zen_workspaces SET {', '.join(updates)} WHERE uuid = ?"
                    cursor.execute(query, params)

                    # Add to changes table
                    cursor.execute("""
                        INSERT OR REPLACE INTO zen_workspaces_changes (uuid, timestamp)
                        VALUES (?, ?)
                    """, (workspace_uuid, timestamp))

                    icon_info = f" icon: {zen_icon}" if zen_icon else ""
                    theme_info = f" theme: {theme_type}" if theme_type else ""
                    logger.info(f"🎨 Updated workspace {workspace_uuid}:{icon_info}{theme_info}")
                    conn.commit()
                    return True

        except Exception as e:
            logger.error(f"Failed to update workspace icon/color for {workspace_uuid}: {e}")
            return False

    def update_workspace_icon(self, workspace_uuid: str, icon: Optional[str]) -> bool:
        """Update the icon for an existing workspace."""
        return self.update_workspace_icon_and_color(workspace_uuid, icon, None)

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
                space_color = space.get('color')  # Get color from Arc data
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
                    # Update icon and color for existing workspace
                    if space_icon or space_color:
                        self.update_workspace_icon_and_color(existing_uuid, space_icon, space_color)
                else:
                    # Create new workspace with icon and color
                    workspace_uuid = self.create_workspace(space_name, container_id, position, space_icon, space_color)
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