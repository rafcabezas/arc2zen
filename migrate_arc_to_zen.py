#!/usr/bin/env python3
"""
Arc to Zen Browser Migration Tool

Complete migration orchestrator that handles the full Arc → Zen bookmark migration process.
This is the main entry point for the migration.

Usage:
    python3 migrate_arc_to_zen.py [--dry-run] [--zen-profile NAME] [--arc-space NAME]
"""

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Import our modules
sys.path.append(str(Path(__file__).parent / "src"))
from arc_history_migrator import ArcHistoryMigrator
from arc_pinned_tab_extractor import ArcPinnedTabExtractor
from zen_bookmark_importer import ZenBookmarkImporter
from zen_pinned_tab_importer import ZenPinnedTabImporter
from zen_schema_analyzer import ZenSchemaAnalyzer
from zen_sessions_importer import ZenSessionsImporter
from zen_space_importer import ZenProfile, ZenSpaceImporter
from zen_workspace_importer import ZenWorkspaceImporter

# Optional: Import sessionstore manager for open tabs (requires lz4)
try:
    from zen_sessionstore_manager import ZenSessionstoreManager
    OPEN_TABS_AVAILABLE = True
except ImportError:
    OPEN_TABS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("lz4 library not found - open tabs import will be skipped. Install with: pip install lz4")

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
            if "WinError 2" not in str(e):
                logger.warning(f"Could not check if browsers are running: {e}")

        return running_browsers, len(running_browsers) > 0

    def run_migration(
        self,
        dry_run: bool = False,
        zen_profile_name: Optional[str] = None,
        arc_space_name: Optional[str] = None,
        import_open_tabs: bool = False,
        no_containers: bool = False,
        import_history: bool = False,
        include_default_essentials: bool = False,
        keep_root_tabs: bool = False,
    ) -> bool:
        """Run the complete Arc to Zen migration process."""

        print("🔄 Arc to Zen Browser Migration v1.2 (2025-09-29)")
        print("=" * 50)

        logger.info("Starting Arc to Zen migration")
        logger.info(
            f"Options: dry_run={dry_run}, zen_profile={zen_profile_name}, "
            f"arc_space={arc_space_name}, import_open_tabs={import_open_tabs}, "
            f"no_containers={no_containers}, import_history={import_history}, "
            f"include_default_essentials={include_default_essentials}, keep_root_tabs={keep_root_tabs}"
        )

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
        all_arc_spaces = arc_extractor.extract_pinned_tabs(
            skip_default_essentials=not include_default_essentials
        )

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
        total_open_tabs = sum(len(space.open_tabs) for space in arc_spaces)
        print(f"✅ Found {len(arc_spaces)} Arc space{'s' if len(arc_spaces) > 1 else ''} with {total_extracted} pinned tabs + {total_open_tabs} open tabs")
        for space in arc_spaces:
            print(f"  • {space.space_name}: {len(space.pinned_tabs)} pinned, {len(space.open_tabs)} open, {len(space.folders)} folders")

        # Warn about arc:// URLs that will be filtered out
        arc_urls = [
            t for space in arc_spaces
            for t in space.pinned_tabs
            if t.url.startswith('arc://')
        ]
        if arc_urls:
            print(f"\n⚠️  {len(arc_urls)} Arc-internal URL(s) will be skipped (arc:// links don't work in Zen):")
            for t in arc_urls:
                print(f"   ✗ {t.title} → {t.url}")



        print(f"\n📊 Total to migrate: {total_extracted} pinned tabs + {total_open_tabs} open tabs")

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

        # Detect Zen storage format: modern (zen-sessions.jsonlz4) vs legacy (SQLite tables)
        uses_sessions = (selected_zen_profile / "zen-sessions.jsonlz4").exists()

        if uses_sessions:
            # Zen 1.18+: Import spaces, pinned tabs, and folders into zen-sessions.jsonlz4
            print("\n📌 Step 4b: Importing pinned tabs and workspaces (modern format)...")
            sessions_importer = ZenSessionsImporter(selected_zen_profile)
            sessions_success = sessions_importer.import_arc_data(
                arc_export_data, container_mappings,
                dry_run=dry_run,
                drop_root_tabs=not keep_root_tabs,
            )
            pinned_success = sessions_success
            workspace_success = sessions_success
        else:
            # Legacy: Write to zen_pins and zen_workspaces SQLite tables
            print("\n📌 Step 4b: Importing as pinned tabs (legacy format)...")
            pinned_tab_importer = ZenPinnedTabImporter(selected_zen_profile)
            workspace_mappings = pinned_tab_importer.import_arc_pinned_tabs(arc_export_data, container_mappings, dry_run=dry_run)
            pinned_success = workspace_mappings is not None

            print("\n🏗️  Step 4c: Creating actual Zen workspaces...")
            workspace_importer = ZenWorkspaceImporter(selected_zen_profile)
            workspace_success = workspace_importer.import_arc_workspaces(arc_export_data, container_mappings, workspace_mappings, dry_run=dry_run)

        # Step 4d: Import as bookmarks (for backup/organization)
        print("\n📚 Step 4d: Importing as bookmarks...")
        bookmark_success = zen_importer.import_arc_bookmarks(arc_export_data, dry_run=dry_run)

        # Step 4e: Import browsing history
        print("\n📜 Step 4e: Importing browsing history...")
        history_success = True
        if not import_history:
            print("ℹ️  History skipped (use --history to include browsing history)")
        else:
            try:
                history_migrator = ArcHistoryMigrator(selected_zen_profile)
                history_stats = history_migrator.migrate(dry_run=dry_run)
                if dry_run:
                    print(f"🧪 Would migrate {history_stats['inserted']} URLs and {history_stats['visits']} visits")
                else:
                    print(f"✅ Migrated {history_stats['inserted']} new URLs, "
                          f"updated {history_stats['updated']} existing, "
                          f"{history_stats['visits']} visit records")
            except Exception as e:
                print(f"⚠️  History import error: {e}")
                logger.error(f"History import failed: {e}")
                history_success = False

        # Step 4f: Import open tabs to sessionstore
        print("\n🔄 Step 4f: Importing open tabs...")
        session_success = True
        total_open_tabs = sum(len(space.get('open_tabs', [])) for space in arc_export_data.get('spaces', []))

        if not import_open_tabs:
            print(f"ℹ️  Open tabs skipped (use --open-tabs to include {total_open_tabs} open tabs)")
        elif not OPEN_TABS_AVAILABLE:
            print("⚠️  Open tabs import unavailable (lz4 not installed). Run: pip install lz4")
        elif total_open_tabs > 0:
            try:
                session_manager = ZenSessionstoreManager(selected_zen_profile)
                if session_manager.create_workspaces_with_tabs(arc_export_data, container_mappings, dry_run=dry_run):
                    if dry_run:
                        print(f"🧪 Would import {total_open_tabs} open tabs")
                    else:
                        print(f"✅ Imported {total_open_tabs} open tabs")
                        print("   ⚠️  Restart Zen browser to see open tabs")
                else:
                    print("⚠️  Failed to import open tabs")
                    session_success = False
            except Exception as e:
                print(f"⚠️  Open tabs import error: {e}")
                logger.error(f"Open tabs import failed: {e}")
                session_success = False
        else:
            print("ℹ️  No open tabs to import")

        success = space_success and pinned_success and workspace_success and bookmark_success and session_success and history_success

        # Cleanup
        if self.temp_export_file.exists():
            self.temp_export_file.unlink()

        if success:
            if dry_run:
                print("\n✅ Dry run completed successfully!")
                print("💡 Run without --dry-run to perform actual migration.")
            else:
                print("\n🎉 Migration completed successfully!")
                print("📌 Your Arc pinned tabs are now in Zen as actual pinned tabs")
                print(f"🔄 Open tabs imported to Zen sessionstore ({total_open_tabs} tabs)")
                print(f"🏗️ Created {len(arc_spaces)} Zen workspaces for your Arc spaces")
                print("📁 Bookmarks also imported as backup under 'Unfiled Bookmarks'")
                print(f" Migrated {total_extracted} pinned tabs + {total_open_tabs} open tabs")

            logger.info(f"Migration completed successfully. Bookmarks migrated: {total_extracted}")
            return True
        else:
            print("\n❌ Migration failed!")
            logger.error("Migration failed")
            return False

    def show_summary(self):
        """Show migration summary and recommendations."""
        print("\n📋 Post-Migration Notes:")
        print("🎯 WORKSPACES & PINNED TABS:")
        print("• Zen workspaces created for each Arc space")
        print("• Pinned tabs imported directly into each workspace")
        print("• Folder hierarchy preserved with nested structure")
        print()
        print("📚 BOOKMARKS:")
        print("• Arc pinned tabs also imported as bookmarks for backup")
        print("• Bookmarks are organized by space in 'Unfiled Bookmarks'")
        print("• You can delete the bookmark folders if desired")
        print()
        print("💡 NEXT STEPS:")
        print("• Restart Zen browser to see your imported data")
        print("• A backup was created before import (restore if needed)")

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate pinned tabs from Arc browser to Zen browser",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 migrate_arc_to_zen.py                    # Full migration (pinned tabs + history)
  python3 migrate_arc_to_zen.py --open-tabs        # Also migrate open tabs to sessionstore
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
        '--open-tabs',
        action='store_true',
        default=False,
        help='Also migrate open (non-pinned) tabs to Zen sessionstore (requires lz4: pip install lz4)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging output'
    )

    parser.add_argument(
        '--history',
        action='store_true',
        default=False,
        help='Also migrate Arc browsing history to Zen'
    )

    parser.add_argument(
        '--no-containers',
        action='store_true',
        help='Do not assign any container to tabs (container ID 0 - regular browsing). By default, separate containers are created for each Arc space. You can manually assign containers later in Zen.'
    )

    parser.add_argument(
        '--include-default-essentials',
        action='store_true',
        default=False,
        help='Include Arc\'s Default-profile essential tabs (typically YouTube and Google Calendar) as Zen essentials. By default these are skipped.'
    )

    parser.add_argument(
        '--keep-root-tabs',
        action='store_true',
        default=False,
        help='Keep Arc pinned tabs that sit at the root of a space (no folder) as regular Zen pinned tabs. By default they are dropped to keep the sidebar clean (only essentials + folders are shown).'
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
            import_open_tabs=args.open_tabs,
            no_containers=args.no_containers,
            import_history=args.history,
            include_default_essentials=args.include_default_essentials,
            keep_root_tabs=args.keep_root_tabs,
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
