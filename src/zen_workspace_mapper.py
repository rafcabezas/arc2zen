#!/usr/bin/env python3
"""
Zen Workspace Mapper

Helps identify and map real Zen workspace UUIDs to workspace names
so we can properly assign Arc spaces to the correct workspaces.
"""

import sqlite3
import json
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

class ZenWorkspaceMapper:
    """Maps Zen workspace UUIDs to workspace names and containers."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.places_db = zen_profile_path / "places.sqlite"
        self.prefs_file = zen_profile_path / "prefs.js"

    def get_active_workspace_uuid(self) -> Optional[str]:
        """Get the currently active workspace UUID from prefs.js."""
        try:
            with open(self.prefs_file, 'r') as f:
                content = f.read()

            # Find the zen.workspaces.active preference
            for line in content.split('\n'):
                if 'zen.workspaces.active' in line and 'user_pref' in line:
                    # Extract UUID from: user_pref("zen.workspaces.active", "{uuid}");
                    start = line.find('"', line.find('zen.workspaces.active')) + 1
                    end = line.find('"', start)
                    uuid = line[start:end]
                    return uuid if uuid.startswith('{') else None

            return None

        except Exception as e:
            logger.error(f"Failed to read active workspace: {e}")
            return None

    def get_workspace_uuids_from_pins(self) -> List[str]:
        """Get all workspace UUIDs that have pinned tabs assigned."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT workspace_uuid
                    FROM zen_pins
                    WHERE workspace_uuid IS NOT NULL
                """)

                return [row[0] for row in cursor.fetchall()]

        except Exception as e:
            logger.error(f"Failed to get workspace UUIDs from pins: {e}")
            return []

    def get_pinned_tabs_by_workspace(self) -> Dict[str, List[Dict]]:
        """Get pinned tabs grouped by workspace UUID."""
        try:
            with sqlite3.connect(self.places_db) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT workspace_uuid, title, url, container_id, position
                    FROM zen_pins
                    ORDER BY workspace_uuid, position
                """)

                workspace_tabs = {}
                for row in cursor.fetchall():
                    workspace_uuid = row[0] or "DEFAULT"
                    tab_info = {
                        "title": row[1],
                        "url": row[2],
                        "container_id": row[3],
                        "position": row[4]
                    }

                    if workspace_uuid not in workspace_tabs:
                        workspace_tabs[workspace_uuid] = []
                    workspace_tabs[workspace_uuid].append(tab_info)

                return workspace_tabs

        except Exception as e:
            logger.error(f"Failed to get pinned tabs by workspace: {e}")
            return {}

    def analyze_workspace_structure(self) -> Dict:
        """Analyze the current workspace structure."""
        active_uuid = self.get_active_workspace_uuid()
        workspace_uuids = self.get_workspace_uuids_from_pins()
        workspace_tabs = self.get_pinned_tabs_by_workspace()

        analysis = {
            "active_workspace_uuid": active_uuid,
            "discovered_workspace_uuids": workspace_uuids,
            "workspace_tab_counts": {
                uuid: len(tabs) for uuid, tabs in workspace_tabs.items()
            },
            "workspace_details": workspace_tabs
        }

        return analysis

    def create_workspace_mapping_guide(self, arc_spaces: List[str]) -> Dict:
        """Create a guide for manually mapping Arc spaces to Zen workspaces."""
        analysis = self.analyze_workspace_structure()

        guide = {
            "zen_workspace_analysis": analysis,
            "arc_spaces_to_map": arc_spaces,
            "instructions": [
                "1. Look at the workspace_tab_counts to identify your workspaces",
                "2. Check which workspace_uuid corresponds to each of your Arc spaces",
                "3. Update the mapping below based on your workspace names",
                "4. Run the import script with the correct UUID mappings"
            ],
            "suggested_mapping": {
                # This will be filled in manually based on user's Arc spaces
                # Example: {"Personal": "UUID_FOR_PERSONAL_WORKSPACE", "Work": "UUID_FOR_WORK_WORKSPACE"}
            },
            "available_workspace_uuids": analysis["discovered_workspace_uuids"] + ["DEFAULT"],
            "note": "DEFAULT means no workspace_uuid (NULL in database)"
        }

        return guide

def find_zen_profile() -> Path:
    """Find the active Zen profile directory."""
    profiles_dir = Path("~/Library/Application Support/zen/Profiles").expanduser()

    if not profiles_dir.exists():
        raise FileNotFoundError(f"Zen profiles directory not found: {profiles_dir}")

    # Look for the most recently modified profile (likely the active one)
    profiles = list(profiles_dir.glob("*.Default*"))
    if not profiles:
        raise FileNotFoundError("No Zen profiles found")

    # Sort by modification time, newest first
    profiles.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    return profiles[0]

def main():
    """Analyze current Zen workspace structure."""
    try:
        zen_profile = find_zen_profile()
        print(f"📁 Using Zen profile: {zen_profile.name}")
        mapper = ZenWorkspaceMapper(zen_profile)

        print("🔍 Analyzing Zen Workspace Structure...")

        # Get analysis
        analysis = mapper.analyze_workspace_structure()

        print(f"\n📋 Workspace Analysis:")
        print(f"  Active Workspace UUID: {analysis['active_workspace_uuid']}")
        print(f"  Discovered Workspace UUIDs: {len(analysis['discovered_workspace_uuids'])}")

        for uuid in analysis['discovered_workspace_uuids']:
            tab_count = analysis['workspace_tab_counts'].get(uuid, 0)
            print(f"    • {uuid}: {tab_count} pinned tabs")

        default_count = analysis['workspace_tab_counts'].get('DEFAULT', 0)
        if default_count > 0:
            print(f"    • DEFAULT (no workspace): {default_count} pinned tabs")

        print(f"\n📝 Detailed breakdown:")
        for workspace_uuid, tabs in analysis['workspace_details'].items():
            print(f"\n  {workspace_uuid}:")
            for tab in tabs[:3]:  # Show first 3 tabs
                print(f"    - {tab['title']} (container {tab['container_id']})")
            if len(tabs) > 3:
                print(f"    ... and {len(tabs)-3} more")

        # Create mapping guide - spaces will be provided by user or extracted from Arc data
        print("\n📝 Please provide the names of your Arc spaces (comma-separated):")
        print("   Example: Personal, Work, Projects, Banking")
        print("   Or leave empty to use spaces detected from Arc data")

        user_input = input("Arc space names: ").strip()
        if user_input:
            arc_spaces = [space.strip() for space in user_input.split(",")]
        else:
            # Try to extract spaces from Arc data
            from arc_pinned_tab_extractor import ArcPinnedTabExtractor
            extractor = ArcPinnedTabExtractor()
            arc_data = extractor.extract_pinned_tabs()
            arc_spaces = [space.space_name for space in arc_data]
            if not arc_spaces:
                print("⚠️  Could not detect Arc spaces. Using default placeholder names.")
                arc_spaces = ["Personal", "Work", "Projects", "Other"]

        guide = mapper.create_workspace_mapping_guide(arc_spaces)

        # Save guide
        guide_file = zen_profile / "workspace_uuid_mapping.json"
        with open(guide_file, 'w') as f:
            json.dump(guide, f, indent=2)

        print(f"\n💾 Created workspace mapping guide: {guide_file}")
        print("\n💡 Next steps:")
        print("  1. Identify which UUID corresponds to each of your Arc spaces")
        print("  2. Update the mapping in workspace_uuid_mapping.json")
        print("  3. Re-run the import script with correct workspace assignments")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n💡 Troubleshooting:")
        print("  • Make sure Zen browser is installed and has been run at least once")
        print("  • Check that the profile directory exists")
        print("  • Try running Zen browser and creating a workspace first")

if __name__ == "__main__":
    main()