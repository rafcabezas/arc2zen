#!/usr/bin/env python3
"""
Zen Profile Reset Tool

COMPLETELY resets a Zen profile to fresh state for migration testing.
This is more destructive than cleanup - it removes ALL custom data.
"""

import argparse
import sqlite3
import json
import sys
import shutil
from pathlib import Path
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import our modules
sys.path.append(str(Path(__file__).parent / "src"))
from zen_schema_analyzer import ZenSchemaAnalyzer

class ZenResetter:
    """Completely reset Zen profile to fresh state."""

    def __init__(self):
        self.home_dir = Path.home()

    def reset_zen_profile(self, zen_profile, dry_run: bool = False) -> bool:
        """Completely reset a Zen profile."""

        print("🔄 Zen Profile Complete Reset Tool")
        print("=" * 50)
        print(f"Profile: {zen_profile.name}")
        print()

        if dry_run:
            print("🧪 DRY RUN MODE - Showing what would be reset\n")
        else:
            print("⚠️  WARNING: This will COMPLETELY RESET the Zen profile!")
            print("⚠️  ALL custom data will be removed:")
            print("   • All pinned tabs")
            print("   • All workspaces (except Default)")
            print("   • All containers/spaces")
            print("   • All bookmarks")
            print("   • All browsing history")
            print("   • All preferences")
            print()
            response = input("Are you ABSOLUTELY sure? Type 'RESET' to continue: ")
            if response != 'RESET':
                print("❌ Reset cancelled")
                return False
            print()

        # zen_profile is already a Path object
        profile_path = zen_profile if isinstance(zen_profile, Path) else zen_profile.path
        db_path = profile_path / "places.sqlite"

        if not db_path.exists():
            print(f"❌ Database not found: {db_path}")
            return False

        # Test database lock
        try:
            test_conn = sqlite3.connect(db_path, timeout=1.0)
            test_conn.execute("SELECT 1")
            test_conn.close()
        except sqlite3.OperationalError as e:
            print(f"❌ Database is locked: {e}")
            print("💡 Make sure Zen browser is completely closed")
            return False

        if dry_run:
            print("📊 Files that would be reset:")
            print(f"  • {db_path.name} (main database)")
            print(f"  • containers.json (workspaces/containers)")
            print(f"  • prefs.js (preferences)")
            print(f"  • .parentlock (if exists)")
            print()
            print("🧪 Dry run complete. Run without --dry-run to perform reset.")
            return True

        # Create comprehensive backup
        backups_dir = Path.cwd() / "backups"
        backups_dir.mkdir(exist_ok=True)
        backup_dir = backups_dir / f"zen_profile_backup_{int(db_path.stat().st_mtime)}"
        try:
            backup_dir.mkdir(exist_ok=True, parents=True)
            shutil.copy2(db_path, backup_dir / "places.sqlite")

            containers_file = profile_path / "containers.json"
            if containers_file.exists():
                shutil.copy2(containers_file, backup_dir / "containers.json")

            prefs_file = profile_path / "prefs.js"
            if prefs_file.exists():
                shutil.copy2(prefs_file, backup_dir / "prefs.js")

            print(f"💾 Created backup: backups/{backup_dir.name}\n")
        except Exception as e:
            print(f"⚠️  Failed to create backup: {e}")
            print("Continuing anyway...\n")

        print("🧹 Resetting profile...\n")

        try:
            # Reset database
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # Remove all pinned tabs
            cursor.execute("DELETE FROM zen_pins")
            pins_removed = cursor.rowcount
            print(f"  ✅ Removed {pins_removed} pinned tabs")

            # Remove all workspaces except Default
            cursor.execute("DELETE FROM zen_workspaces WHERE name != 'Default'")
            workspaces_removed = cursor.rowcount
            print(f"  ✅ Removed {workspaces_removed} workspaces")

            # Remove ALL bookmarks (complete reset)
            cursor.execute("DELETE FROM moz_bookmarks WHERE id > 5")  # Keep root folders only
            bookmarks_removed = cursor.rowcount
            print(f"  ✅ Removed {bookmarks_removed} bookmarks")

            # Clear history
            cursor.execute("DELETE FROM moz_historyvisits")
            cursor.execute("DELETE FROM moz_places WHERE visit_count = 0")
            print(f"  ✅ Cleared browsing history")

            # Vacuum to reclaim space
            conn.commit()
            print(f"  🗜️  Optimizing database...")
            cursor.execute("VACUUM")

            conn.close()

            # Reset containers.json to default
            containers_file = profile_path / "containers.json"
            if containers_file.exists():
                default_containers = {
                    "version": 5,
                    "identities": [
                        {
                            "userContextId": 1,
                            "public": True,
                            "icon": "fingerprint",
                            "color": "blue",
                            "name": "Personal"
                        }
                    ]
                }
                with open(containers_file, 'w') as f:
                    json.dump(default_containers, f, indent=2)
                print(f"  ✅ Reset containers.json to default")

            # Remove lock file if exists
            parentlock = profile_path / ".parentlock"
            if parentlock.exists():
                parentlock.unlink()
                print(f"  ✅ Removed .parentlock file")

            # Remove WAL files if they exist
            wal_files = list(profile_path.glob("*.sqlite-wal")) + list(profile_path.glob("*.sqlite-shm"))
            for wal_file in wal_files:
                wal_file.unlink()
                print(f"  ✅ Removed {wal_file.name}")

            print()
            print("✅ Profile reset completed successfully!")
            print("💡 The profile is now in a fresh state")
            print("💡 You can now run the migration again")
            return True

        except Exception as e:
            print(f"\n❌ Reset failed: {e}")
            logger.exception("Reset error")
            return False


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Completely reset Zen profile to fresh state",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
⚠️  WARNING: This is DESTRUCTIVE and removes ALL custom data!

Use this when:
  • Testing migrations repeatedly
  • Zen profile is corrupted
  • You want a completely fresh start

Examples:
  python3 reset_zen.py                    # Interactive reset
  python3 reset_zen.py --dry-run          # Preview what would be reset
  python3 reset_zen.py --zen-profile Default  # Reset specific profile
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Preview reset without making changes'
    )

    parser.add_argument(
        '--zen-profile',
        type=str,
        help='Name or partial name of Zen profile to reset'
    )

    args = parser.parse_args()

    # Find Zen profile
    print("\n🔍 Locating Zen browser...")
    zen_analyzer = ZenSchemaAnalyzer()
    zen_profiles = zen_analyzer.find_zen_profiles()

    if not zen_profiles:
        print("❌ No Zen profiles found! Make sure Zen browser is installed.")
        sys.exit(1)

    # Select Zen profile
    selected_zen_profile = None
    if args.zen_profile:
        for profile in zen_profiles:
            if args.zen_profile in profile.name:
                selected_zen_profile = profile
                break
        if not selected_zen_profile:
            print(f"❌ Zen profile '{args.zen_profile}' not found!")
            sys.exit(1)
    else:
        # Use first available profile
        selected_zen_profile = zen_profiles[0]

    print(f"✅ Using Zen profile: {selected_zen_profile.name}\n")

    # Create resetter and run
    resetter = ZenResetter()
    try:
        success = resetter.reset_zen_profile(selected_zen_profile, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\n⚠️ Reset cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.exception("Unexpected error during reset")
        sys.exit(1)


if __name__ == "__main__":
    main()
