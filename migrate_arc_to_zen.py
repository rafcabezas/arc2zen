#!/usr/bin/env python3
"""
Arc to Zen Browser Migration Tool

Complete migration orchestrator that handles the full Arc → Zen bookmark migration process.
This is the main entry point for the migration.

Usage:
    python3 migrate_arc_to_zen.py [--dry-run] [--min-visits 2] [--zen-profile NAME]
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional
import logging
import os

# Import our modules
sys.path.append(str(Path(__file__).parent / "src"))
from arc_pinned_tab_extractor import ArcPinnedTabExtractor
from zen_schema_analyzer import ZenSchemaAnalyzer
from zen_bookmark_importer import ZenBookmarkImporter
from zen_space_importer import ZenSpaceImporter, ZenProfile
from zen_pinned_tab_importer import ZenPinnedTabImporter
from zen_workspace_importer import ZenWorkspaceImporter

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('arc_to_zen_migration.log')
    ]
)
logger = logging.getLogger(__name__)


class Arc2ZenMigrator:
    """Main migration orchestrator."""

    def __init__(self):
        self.home_dir = Path.home()
        self.temp_export_file = Path("arc_pinned_tabs_export.json")

    def check_browsers_running(self) -> tuple[list[str], bool]:
        """Check if Arc or Zen browsers are currently running.

        Returns:
            tuple: (list of running browsers, any_running)
        """
        running_browsers = []

        try:
            # Check for actual Arc browser processes (be specific to avoid false positives)

            if os.name == "nt":
                result = subprocess.run(
                    ['powershell', 'Get-Process -Name "Arc" -erroraction silentlycontinue | Select-Object id'],
                    capture_output=True,
                    text=True,
                )
            else:
                result = subprocess.run(
                    ['pgrep', '-f', '/Applications/Arc.app'],
                    capture_output=True,
                    text=True
                )
            if result.returncode == 0 and result.stdout.strip():
                running_browsers.append('Arc')

            # Check for Zen browser processes (multiple possible locations)
            zen_paths = [
                '/Applications/Zen Browser.app',
                '/Applications/zen.app',
                '/usr/local/bin/zen',
                'zen-browser',  # For AppImage/Flatpak installations
                'Zen' # For Windows
            ]

            for zen_path in zen_paths:
                if os.name == "nt":
                    result = subprocess.run(
                        ['powershell', f'Get-Process -Name "{zen_path}" -erroraction silentlycontinue | Select-Object id'],
                        capture_output=True,
                        text=True
                    )
                else:
                    result = subprocess.run(
                        ['pgrep', '-f', zen_path],
                        capture_output=True,
                        text=True
                    )
                if result.returncode == 0 and result.stdout.strip():
                    if 'Zen' not in running_browsers:
                        running_browsers.append('Zen')
                    break

        except Exception as e:
            # Ignore false windows file not found error
            if not "WinError 2" in str(e):
                logger.warning(f"Could not check if browsers are running: {e}")

        return running_browsers, len(running_browsers) > 0

    def run_migration(self, dry_run: bool = False, zen_profile_name: Optional[str] = None, arc_space_name: Optional[str] = None, no_containers: bool = False) -> bool:
        """Run the complete Arc to Zen migration process."""

        print("🔄 Arc to Zen Browser Migration v1.2 (2025-09-29)")
        print("=" * 50)

        logger.info("Starting Arc to Zen migration")
        logger.info(f"Options: dry_run={dry_run}, zen_profile={zen_profile_name}, arc_space={arc_space_name}, no_containers={no_containers}")

        # Clean up any previous export file to prevent caching issues
        if self.temp_export_file.exists():
            self.temp_export_file.unlink()
            logger.info("🧹 Removed previous export file")

        # Check if Arc or Zen browsers are running (required for reliable migration)
        running_browsers, any_running = self.check_browsers_running()
        if any_running:
            browsers_list = " and ".join(running_browsers)
            print(f"❌ ERROR: {browsers_list} browser{'s' if len(running_browsers) > 1 else ''} currently running!")
            print("   Please close ALL browsers completely before running the migration.")
            print("   This prevents:")
            print("   • Arc: Intermittent extraction issues due to sync/file changes")
            print("   • Zen: Database lock errors during import")
            print("   💡 Tip: Make sure to quit browsers entirely, not just close windows.")
            return False

        # Step 1: Extract Arc pinned tabs
        print("\n📌 Step 1: Extracting Arc pinned tabs...")
        arc_extractor = ArcPinnedTabExtractor()
        all_arc_spaces = arc_extractor.extract_pinned_tabs()

        if not all_arc_spaces:
            print("❌ No Arc pinned tabs found! Make sure Arc browser is installed.")
            return False

        # Filter by space name if specified
        if arc_space_name:
            print(f"\n🔍 Filtering for Arc space: '{arc_space_name}'")
            arc_spaces = []
            for space in all_arc_spaces:
                # Case-insensitive partial matching for convenience
                if arc_space_name.lower() in space.space_name.lower():
                    arc_spaces.append(space)

            if not arc_spaces:
                print(f"❌ No Arc space found matching '{arc_space_name}'")
                print("\n📋 Available Arc spaces:")
                for space in all_arc_spaces:
                    print(f"  • {space.space_name}")
                return False

            if len(arc_spaces) > 1:
                print(f"⚠️  Multiple spaces match '{arc_space_name}':")
                for space in arc_spaces:
                    print(f"  • {space.space_name}")
                print("💡 Consider using a more specific space name.")
        else:
            arc_spaces = all_arc_spaces

        total_extracted = sum(len(space.pinned_tabs) for space in arc_spaces)
        print(f"✅ Found {len(arc_spaces)} Arc space{'s' if len(arc_spaces) > 1 else ''} with {total_extracted} pinned tabs")
        for space in arc_spaces:
            print(f"  • {space.space_name}: {len(space.pinned_tabs)} tabs, {len(space.folders)} folders")

        print(f"\n📊 Total pinned tabs to migrate: {total_extracted}")

        # Step 2: Export to temporary file
        print("\n💾 Step 2: Preparing export data...")
        success = arc_extractor.export_to_json(arc_spaces, self.temp_export_file)
        if not success:
            print("❌ Failed to create export file!")
            return False

        # Step 3: Find Zen profile
        print("\n🎯 Step 3: Locating Zen browser...")
        zen_analyzer = ZenSchemaAnalyzer()
        zen_profiles = zen_analyzer.find_zen_profiles()

        if not zen_profiles:
            print("❌ No Zen profiles found! Make sure Zen browser is installed.")
            return False

        # Select Zen profile
        selected_zen_profile = None
        if zen_profile_name:
            for profile in zen_profiles:
                if zen_profile_name in profile.name:
                    selected_zen_profile = profile
                    break
            if not selected_zen_profile:
                print(f"❌ Zen profile '{zen_profile_name}' not found!")
                return False
        else:
            # Use first available profile
            selected_zen_profile = zen_profiles[0]

        print(f"✅ Using Zen profile: {selected_zen_profile.name}")

        # Step 4: Import to Zen
        print("\n📥 Step 4: Importing to Zen browser...")

        # Load export data
        with open(self.temp_export_file, 'r', encoding="utf-8") as f:
            arc_export_data = json.load(f)

        if dry_run:
            print("🧪 DRY RUN MODE - No changes will be made")

        # Check if Zen is running
        zen_importer = ZenBookmarkImporter(selected_zen_profile)
        if not zen_importer.check_zen_database():
            print("❌ Cannot access Zen database!")
            print("💡 Make sure Zen browser is completely closed and try again.")
            return False

        # Create database backup before any modifications
        if not dry_run:
            print("\n💾 Creating database backup...")
            if not zen_importer.backup_database():
                print("⚠️ Failed to backup database, but continuing...")
            else:
                print("✅ Database backup created successfully")

        # Step 4a: Create Zen workspaces (containers)
        print("\n🏗️  Step 4a: Creating Zen workspaces...")
        zen_profile = ZenProfile(name=selected_zen_profile.name, path=selected_zen_profile)
        zen_space_importer = ZenSpaceImporter(zen_profile)

        # Import spaces and get container mappings
        if no_containers:
            print("  🚫 No container assignment (regular browsing context)")
            # Use container ID 0 (no container) for all spaces
            container_mappings = {}
            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                container_mappings[space_name] = 0  # No container
            space_success = True
        else:
            print("  🔒 Creating separate containers for each Arc space (cookie isolation)")
            container_mappings = zen_space_importer.import_arc_spaces_as_containers(arc_export_data, dry_run=dry_run)

            # In dry run, container_mappings will be empty, but that's expected
            if dry_run:
                space_success = True
                # Create mock container mappings for dry run
                container_mappings = {}
                for space in arc_export_data.get('spaces', []):
                    space_name = space['space_name']
                    container_mappings[space_name] = 1  # Default container
            else:
                space_success = container_mappings is not None and len(container_mappings) > 0

            if not space_success:
                print("⚠️ Failed to create Zen workspaces, but continuing with import...")
                # Use default container mappings as fallback
                container_mappings = {}
                for space in arc_export_data.get('spaces', []):
                    space_name = space['space_name']
                    container_mappings[space_name] = 1  # Default container

        # Step 4b: Import as pinned tabs (actual pinned tabs, not bookmarks)
        print("\n📌 Step 4b: Importing as pinned tabs...")
        pinned_tab_importer = ZenPinnedTabImporter(selected_zen_profile)
        workspace_mappings = pinned_tab_importer.import_arc_pinned_tabs(arc_export_data, container_mappings, dry_run=dry_run)
        # For dry run, workspace_mappings is empty dict, but that's expected
        pinned_success = workspace_mappings is not None  # Success if we got workspace mappings (even empty for dry run)

        # Step 4c: Create actual Zen workspaces for each Arc space
        print("\n🏗️  Step 4c: Creating actual Zen workspaces...")
        workspace_importer = ZenWorkspaceImporter(selected_zen_profile)
        workspace_success = workspace_importer.import_arc_workspaces(arc_export_data, container_mappings, workspace_mappings, dry_run=dry_run)

        # Step 4d: Import as bookmarks (for backup/organization)
        print("\n📚 Step 4d: Importing as bookmarks...")
        bookmark_success = zen_importer.import_arc_bookmarks(arc_export_data, dry_run=dry_run)

        success = space_success and pinned_success and workspace_success and bookmark_success

        # Cleanup
        if self.temp_export_file.exists():
            self.temp_export_file.unlink()

        if success:
            if dry_run:
                print("\n✅ Dry run completed successfully!")
                print("💡 Run without --dry-run to perform actual migration.")
            else:
                print("\n🎉 Migration completed successfully!")
                print(f"📌 Your Arc pinned tabs are now in Zen as actual pinned tabs")
                print(f"🏗️ Created {len(arc_spaces)} Zen workspaces for your Arc spaces")
                print(f"📁 Bookmarks also imported as backup under 'Unfiled Bookmarks'")
                print(f"📊 Migrated {total_extracted} pinned tabs from {len(arc_spaces)} Arc spaces")

            logger.info(f"Migration completed successfully. Bookmarks migrated: {total_extracted}")
            return True
        else:
            print("\n❌ Migration failed!")
            logger.error("Migration failed")
            return False

    def show_summary(self):
        """Show migration summary and recommendations."""
        print("\n📋 Post-Migration Notes:")
        print("🎯 WORKSPACES:")
        print("• Zen workspaces (containers) created for each Arc space")
        print("• You can now access workspaces via the Zen sidebar")
        print("• Each workspace maintains separate tabs and history")
        print()
        print("📚 BOOKMARKS:")
        print("• Arc pinned tabs also imported as bookmarks for backup")
        print("• Bookmarks are organized by space in 'Unfiled Bookmarks'")
        print("• Original folder structure preserved (e.g., 'Finances' folder)")
        print()
        print("💡 NEXT STEPS:")
        print("• Open each workspace and manually pin your important tabs")
        print("• You can now delete the bookmark folders if desired")
        print("• A database backup was created before import")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate pinned tabs from Arc browser to Zen browser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 migrate_arc_to_zen.py                    # Full migration
  python3 migrate_arc_to_zen.py --dry-run          # Test run only
  python3 migrate_arc_to_zen.py --zen-profile Default  # Specific Zen profile
  python3 migrate_arc_to_zen.py --arc-space Personal  # Migrate only Personal space
  python3 migrate_arc_to_zen.py --arc-space Work --dry-run  # Test migrate Work space
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Perform a test run without making actual changes'
    )


    parser.add_argument(
        '--zen-profile',
        type=str,
        help='Name or partial name of Zen profile to import to'
    )

    parser.add_argument(
        '--arc-space',
        type=str,
        help='Name or partial name of Arc space to migrate (case-insensitive). If not specified, all spaces are migrated.'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging output'
    )

    parser.add_argument(
        '--no-containers',
        action='store_true',
        help='Do not assign any container to tabs (container ID 0 - regular browsing). By default, separate containers are created for each Arc space. You can manually assign containers later in Zen.'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create migrator and run
    migrator = Arc2ZenMigrator()

    try:
        success = migrator.run_migration(
            dry_run=args.dry_run,
            zen_profile_name=args.zen_profile,
            arc_space_name=args.arc_space,
            no_containers=args.no_containers
        )

        if success and not args.dry_run:
            migrator.show_summary()

        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\n⚠️ Migration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.exception("Unexpected error during migration")
        sys.exit(1)


if __name__ == "__main__":
    main()
