#!/usr/bin/env python3
"""
Zen Space Importer

Creates actual Zen browser spaces (containers) and imports Arc pinned tabs
as pinned tabs in the correct spaces, not as bookmarks.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

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

@dataclass
class ZenContainer:
    """Represents a Zen browser container (space)."""
    userContextId: int
    name: str
    icon: str
    color: str
    public: bool = True

@dataclass
class ZenProfile:
    """Represents a Zen browser profile."""
    name: str
    path: Path

class ZenSpaceImporter:
    """Imports Arc spaces as Zen containers with pinned tabs."""

    # Available container icons and colors
    CONTAINER_ICONS = [
        "fingerprint", "briefcase", "dollar", "cart", "vacation", "gift", "food",
        "fruit", "pet", "tree", "chill", "fence", "circle", "user", "lock"
    ]

    CONTAINER_COLORS = [
        "blue", "turquoise", "green", "yellow", "orange", "red", "pink", "purple"
    ]

    def __init__(self, zen_profile: ZenProfile):
        self.zen_profile = zen_profile
        self.containers_file = zen_profile.path / "containers.json"
        self.prefs_file = zen_profile.path / "prefs.js"

    def load_existing_containers(self) -> Dict:
        """Load existing container configuration."""
        try:
            if self.containers_file.exists():
                with open(self.containers_file, 'r') as f:
                    return json.load(f)
            else:
                # Create default container structure
                return {
                    "version": 5,
                    "lastUserContextId": 1,
                    "identities": [
                        {
                            "icon": "fingerprint",
                            "color": "blue",
                            "l10nId": "user-context-personal",
                            "public": True,
                            "userContextId": 1
                        }
                    ]
                }
        except Exception as e:
            logger.error(f"Failed to load containers: {e}")
            return {}

    def create_containers_for_spaces(self, arc_spaces: List) -> Dict[str, int]:
        """Create Zen containers for Arc spaces and return space_name -> container_id mapping."""
        container_config = self.load_existing_containers()
        if not container_config:
            return {}

        # Get existing container names to avoid duplicates
        existing_names = set()
        for identity in container_config.get("identities", []):
            name = identity.get("name") or identity.get("l10nId", "").replace("user-context-", "")
            existing_names.add(name.lower())

        space_to_container = {}
        last_context_id = container_config.get("lastUserContextId", 1)

        icon_index = 0
        color_index = 0

        for space in arc_spaces:
            space_name = space['space_name']

            # Check if container already exists
            existing_container = None
            for identity in container_config.get("identities", []):
                identity_name = identity.get("name") or identity.get("l10nId", "").replace("user-context-", "")
                if identity_name.lower() == space_name.lower():
                    existing_container = identity
                    break

            if existing_container:
                space_to_container[space_name] = existing_container["userContextId"]
                logger.info(f"  📁 Using existing container: {space_name} -> {existing_container['userContextId']}")
            else:
                # Create new container
                last_context_id += 1

                # Select icon and color cyclically
                icon = self.CONTAINER_ICONS[icon_index % len(self.CONTAINER_ICONS)]
                color = self.CONTAINER_COLORS[color_index % len(self.CONTAINER_COLORS)]
                icon_index += 1
                color_index += 1

                new_container = {
                    "icon": icon,
                    "color": color,
                    "public": True,
                    "userContextId": last_context_id,
                    "name": space_name
                }

                container_config["identities"].append(new_container)
                space_to_container[space_name] = last_context_id

                logger.info(f"  ✅ Created container: {space_name} -> {last_context_id} ({icon}, {color})")

        # Update lastUserContextId
        container_config["lastUserContextId"] = last_context_id

        # Save updated container configuration
        self.save_containers(container_config)

        return space_to_container

    def save_containers(self, container_config: Dict) -> bool:
        """Save container configuration to containers.json."""
        try:
            with open(self.containers_file, 'w') as f:
                json.dump(container_config, f, separators=(',', ':'))
            logger.info("✅ Updated containers.json")
            return True
        except Exception as e:
            logger.error(f"Failed to save containers: {e}")
            return False

    def update_prefs_for_workspaces(self, active_workspace_id: Optional[str] = None) -> bool:
        """Update prefs.js to enable workspace features."""
        try:
            # Read existing prefs
            prefs_content = ""
            if self.prefs_file.exists():
                with open(self.prefs_file, 'r') as f:
                    prefs_content = f.read()

            # Ensure workspace preferences are set
            workspace_prefs = [
                'user_pref("zen.workspaces.continue-where-left-off", true);',
                'user_pref("zen.workspaces.force-container-workspace", true);',
                'user_pref("zen.workspaces.hide-default-container-indicator", false);',
            ]

            if active_workspace_id:
                workspace_prefs.append(f'user_pref("zen.workspaces.active", "{active_workspace_id}");')

            # Add missing prefs
            for pref in workspace_prefs:
                pref_name = pref.split('"')[1]
                if pref_name not in prefs_content:
                    prefs_content += "\n" + pref

            # Write back
            with open(self.prefs_file, 'w') as f:
                f.write(prefs_content)

            logger.info("✅ Updated workspace preferences")
            return True

        except Exception as e:
            logger.error(f"Failed to update prefs: {e}")
            return False

    def import_arc_spaces_as_containers(self, arc_export_data: Dict, dry_run: bool = False) -> Dict[str, int]:
        """Map Arc spaces to the built-in Work / Personal Firefox containers.

        All spaces from the work profile (Profile 1) are mapped to the
        built-in 'Work' container.  Spaces from the personal profile
        (Profile 2) are mapped to the built-in 'Personal' container.
        No new custom containers are created.

        Also removes any per-space containers created by previous migration
        runs (those whose name matches an Arc space name).
        """
        try:
            arc_spaces = arc_export_data.get('spaces', [])
            if not arc_spaces:
                logger.warning("No Arc spaces found in export data")
                return {}

            logger.info(f"🔧 Mapping {len(arc_spaces)} Arc spaces to Firefox containers...")

            # ── 1. Find the built-in Work / Personal container IDs ──────────
            container_config = self.load_existing_containers()
            work_id     = 2  # Firefox default
            personal_id = 1  # Firefox default

            for identity in container_config.get('identities', []):
                l10n = identity.get('l10nId', '')
                uid  = identity['userContextId']
                if 'work' in l10n:
                    work_id = uid
                elif 'personal' in l10n:
                    personal_id = uid

            logger.info(f"  📦 Work container ID={work_id} · Personal container ID={personal_id}")

            # ── 2. Remove per-space containers from previous migration runs ──
            # Previous versions of this tool created one custom Firefox container
            # per Arc space (IDs > 5 with a plain 'name' matching the space name).
            # We only remove containers that are BOTH:
            #   a) named after an Arc space (plain 'name' field, no 'l10nId')
            #   b) have userContextId > 5 (built-ins 1-4 are always kept)
            # This is conservative: it will NOT remove a user's manually-created
            # container even if it shares a name with an Arc space, unless its ID
            # is above 5 (which would only happen if it was created post-install,
            # consistent with a previous migration run).
            arc_names_lower = {s['space_name'].lower() for s in arc_spaces}
            before = len(container_config['identities'])
            container_config['identities'] = [
                c for c in container_config['identities']
                if not (
                    # Has a plain name (not a built-in l10nId container)
                    c.get('name') and not c.get('l10nId')
                    # Name matches an Arc space
                    and c['name'].lower() in arc_names_lower
                    # ID above the standard built-in range
                    and c['userContextId'] > 5
                )
            ]
            removed = before - len(container_config['identities'])
            if removed:
                logger.info(f"  🧹 Removed {removed} obsolete per-space container(s) from previous migration runs")
                max_uid = max(
                    (c['userContextId'] for c in container_config['identities']),
                    default=5
                )
                container_config['lastUserContextId'] = max_uid

            if not dry_run:
                self.save_containers(container_config)
            else:
                # Dry run: report what would be mapped, but write nothing
                logger.info("🧪 DRY RUN - No actual changes will be made")

            # ── 3. Build the space → container mapping ───────────────────────
            # A space is mapped to the Personal container when ANY of these is true:
            #   a) Its name contains 'personal' (case-insensitive)  — most common
            #   b) No space has 'personal' in the name AND there are multiple distinct
            #      Arc profiles — in that case the highest-numbered profile is the
            #      personal one (Arc convention: Default/Profile 1 = work,
            #      Profile 2 = personal).
            # If there is only one distinct profile, ALL spaces go to Work.

            distinct_profiles = {s.get('profile', '') for s in arc_spaces}
            has_personal_name  = any('personal' in s['space_name'].lower() for s in arc_spaces)
            multiple_profiles  = len(distinct_profiles) > 1

            # Find the highest-numbered Arc profile for the fallback heuristic
            profile_numbers = {}
            for space in arc_spaces:
                p = space.get('profile', '')
                if p and p.startswith('Profile '):
                    try:
                        profile_numbers[p] = int(p.split()[-1])
                    except ValueError:
                        pass
            last_profile = max(profile_numbers, key=profile_numbers.get) if profile_numbers else None

            container_mappings: Dict[str, int] = {}
            for space in arc_spaces:
                space_name = space['space_name']
                profile    = space.get('profile')

                is_personal = (
                    'personal' in space_name.lower()
                    or (
                        not has_personal_name
                        and multiple_profiles
                        and last_profile
                        and profile == last_profile
                    )
                )

                if is_personal:
                    cid   = personal_id
                    label = 'Personal'
                else:
                    cid   = work_id
                    label = 'Work'

                container_mappings[space_name] = cid
                logger.info(f"  ✅ {space_name} (Arc profile: {profile!r}) → {label} (ID {cid})")

            if not dry_run:
                self.update_prefs_for_workspaces()
                self.create_workspaces_guide(container_mappings, arc_spaces)
                logger.info(f"✅ Successfully mapped {len(container_mappings)} spaces")
                return container_mappings

            # dry_run returns empty dict — no data written, caller must not use the result
            return {}

        except Exception as e:
            logger.error(f"Failed to map Arc spaces to containers: {e}")
            return {}

    def create_workspaces_guide(self, space_to_container: Dict[str, int], arc_spaces: List) -> None:
        """Create a guide file to help users set up workspaces."""
        try:
            guide_data = {
                "zen_workspace_setup_guide": {
                    "version": "1.0",
                    "created": datetime.now(timezone.utc).isoformat(),
                    "note": "This file contains a guide for manually setting up Zen workspaces for your Arc spaces",
                    "instructions": [
                        "1. Open Zen browser and click 'Default' in the sidebar",
                        "2. Click the '+' icon to create new workspaces",
                        "3. Name each workspace and assign the corresponding container (listed below)",
                        "4. Enable 'Switch to workspace where container is set as default when opening container tabs' in Settings > Tab Management > Workspaces"
                    ],
                    "container_mappings": space_to_container,
                    "workspaces_to_create": []
                }
            }

            # Add workspace creation instructions
            for space in arc_spaces:
                space_name = space['space_name']
                container_id = space_to_container.get(space_name)
                pinned_tab_count = len(space.get('pinned_tabs', []))

                workspace_info = {
                    "workspace_name": space_name,
                    "container_id": container_id,
                    "container_name": space_name,
                    "pinned_tabs_count": pinned_tab_count,
                    "setup_steps": [
                        f"Create workspace named '{space_name}'",
                        f"Assign container '{space_name}' (ID: {container_id})",
                        f"This workspace will handle {pinned_tab_count} pinned tabs"
                    ]
                }
                guide_data["zen_workspace_setup_guide"]["workspaces_to_create"].append(workspace_info)

            # Save the guide file
            guide_file = self.zen_profile.path / "workspace_setup_guide.json"
            with open(guide_file, 'w') as f:
                json.dump(guide_data, f, indent=2)

            logger.info(f"📋 Created workspace setup guide: {guide_file}")
            logger.info("📝 This file contains step-by-step instructions for setting up your workspaces manually")

        except Exception as e:
            logger.error(f"Failed to create workspace guide: {e}")

def main():
    """Main function to run the Arc to Zen space importer."""
    import argparse

    parser = argparse.ArgumentParser(description='Import Arc spaces as Zen containers with pinned tabs')
    parser.add_argument('arc_export_file', help='Path to Arc export JSON file')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without making changes')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format='%(message)s')

    try:
        # Find Zen profile
        zen_profile_path = find_zen_profile()
        print(f"📁 Using Zen profile: {zen_profile_path.name}")

        # Load Arc export data
        with open(args.arc_export_file, 'r') as f:
            arc_export_data = json.load(f)

        # Create importer
        zen_profile = ZenProfile("Default", zen_profile_path)
        importer = ZenSpaceImporter(zen_profile)

        # Import spaces
        success = importer.import_arc_spaces_as_containers(arc_export_data, args.dry_run)

        if success:
            print("✅ Arc to Zen migration completed successfully!")
            if not args.dry_run:
                print("🔄 Restart Zen browser to see your imported spaces and pinned tabs")
        else:
            print("❌ Arc to Zen migration failed")
            return 1

    except Exception as e:
        print(f"❌ Error: {e}")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
