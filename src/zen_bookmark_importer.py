#!/usr/bin/env python3
"""
Zen Browser Bookmark Importer

Imports Arc browser bookmarks into Zen browser's places.sqlite database.
Creates folder structure for each Arc space.
"""

import sqlite3
import json
import time
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import hashlib

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ZenBookmark:
    """Represents a bookmark to be imported into Zen."""
    url: str
    title: str
    folder_id: int
    visit_count: int = 1
    last_visit_date: int = 0

@dataclass
class ZenFolder:
    """Represents a bookmark folder in Zen."""
    title: str
    parent_id: int
    folder_id: Optional[int] = None


class ZenBookmarkImporter:
    """Imports bookmarks into Zen browser database."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile_path = zen_profile_path
        self.places_db = zen_profile_path / "places.sqlite"

        # Firefox bookmark type constants
        self.TYPE_BOOKMARK = 1
        self.TYPE_FOLDER = 2
        self.TYPE_SEPARATOR = 3

        # Standard Firefox folder GUIDs
        self.ROOT_GUID = "root________"
        self.MENU_GUID = "menu________"
        self.TOOLBAR_GUID = "toolbar_____"
        self.UNFILED_GUID = "unfiled_____"
        self.MOBILE_GUID = "mobile______"

    def check_zen_database(self) -> bool:
        """Check if Zen database exists and is accessible."""
        if not self.places_db.exists():
            logger.error(f"Zen places.sqlite not found: {self.places_db}")
            return False

        try:
            # Test connection
            conn = sqlite3.connect(f"file:{self.places_db}?mode=ro", uri=True, timeout=1.0)
            conn.close()
            logger.info("✅ Zen database accessible")
            return True
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                logger.error("❌ Zen database is locked - please close Zen browser first")
            else:
                logger.error(f"❌ Database error: {e}")
            return False

    def backup_database(self) -> bool:
        """Create backup of Zen database before import."""
        import os
        import shutil

        # Create backup in current working directory
        backup_filename = f"zen_database_backup_{int(time.time())}.sqlite"
        backup_path = Path(os.getcwd()) / backup_filename

        try:
            shutil.copy2(self.places_db, backup_path)
            logger.info(f"✅ Database backed up to: {backup_path.name}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to backup database: {e}")
            return False

    def import_arc_bookmarks(self, arc_export_data: Dict, dry_run: bool = False) -> bool:
        """Import Arc bookmarks into Zen database."""
        if not self.check_zen_database():
            return False

        if not dry_run:
            if not self.backup_database():
                return False

        try:
            conn = sqlite3.connect(self.places_db, timeout=30.0)
            conn.row_factory = sqlite3.Row

            if dry_run:
                logger.info("🧪 DRY RUN - No actual changes will be made")
            else:
                # Begin transaction
                conn.execute("BEGIN EXCLUSIVE")

            imported_count = 0
            skipped_count = 0

            # Process each Arc space
            for space_data in arc_export_data.get('spaces', []):
                if not space_data['pinned_tabs']:
                    logger.info(f"⚪ Skipping {space_data['space_name']} - no pinned tabs")
                    continue

                logger.info(f"📁 Importing {space_data['space_name']} ({len(space_data['pinned_tabs'])} pinned tabs)")

                # Create folder for this Arc space
                folder_id = self._create_arc_space_folder(
                    conn, space_data['space_name'], dry_run
                )

                if folder_id is None:
                    logger.error(f"❌ Failed to create folder for {space_data['space_name']}")
                    continue

                # First, create folder structure
                folder_map = {folder_id: folder_id}  # Root folder mapping
                for folder_data in space_data.get('folders', []):
                    folder_path = folder_data.get('title', 'Untitled Folder')
                    subfolder_id = self._create_subfolder(conn, folder_path, folder_id, dry_run)
                    if subfolder_id:
                        folder_map[folder_data['folder_id']] = subfolder_id

                # Import pinned tabs to appropriate folders
                for tab_data in space_data['pinned_tabs']:
                    # Determine target folder based on folder_path
                    target_folder_id = folder_id  # Default to space root folder

                    folder_path = tab_data.get('folder_path', [])
                    if folder_path:
                        # Try to find matching folder by name
                        for folder_data in space_data.get('folders', []):
                            if folder_data.get('title') in folder_path:
                                target_folder_id = folder_map.get(folder_data['folder_id'], folder_id)
                                break

                    if self._import_single_bookmark(conn, tab_data, target_folder_id, dry_run):
                        imported_count += 1
                    else:
                        skipped_count += 1

            if not dry_run:
                conn.commit()
                logger.info("✅ Transaction committed successfully")
            else:
                conn.rollback()

            conn.close()

            logger.info(f"📊 Import Summary:")
            logger.info(f"  ✅ Imported: {imported_count} bookmarks")
            logger.info(f"  ⚪ Skipped: {skipped_count} bookmarks")

            return True

        except Exception as e:
            logger.error(f"❌ Import failed: {e}")
            if 'conn' in locals():
                conn.rollback()
                conn.close()
            return False

    def _create_arc_space_folder(self, conn: sqlite3.Connection, space_name: str, dry_run: bool = False) -> Optional[int]:
        """Create a folder for an Arc space under 'unfiled' bookmarks."""
        try:
            # Get the unfiled bookmarks folder ID
            cursor = conn.execute("SELECT id FROM moz_bookmarks WHERE guid = ?", (self.UNFILED_GUID,))
            row = cursor.fetchone()
            if not row:
                logger.error("Could not find unfiled bookmarks folder")
                return None

            unfiled_id = row[0]

            # Check if folder already exists
            cursor = conn.execute(
                "SELECT id FROM moz_bookmarks WHERE parent = ? AND title = ? AND type = ?",
                (unfiled_id, space_name, self.TYPE_FOLDER)
            )
            existing = cursor.fetchone()
            if existing:
                logger.info(f"  📁 Folder '{space_name}' already exists")
                return existing[0]

            if dry_run:
                logger.info(f"  📁 Would create folder: {space_name}")
                return 999  # Dummy ID for dry run

            # Get next position in unfiled folder
            cursor = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
                (unfiled_id,)
            )
            position = cursor.fetchone()[0]

            # Create folder
            folder_guid = self._generate_guid()
            now_timestamp = self._now_microseconds()

            cursor = conn.execute("""
                INSERT INTO moz_bookmarks
                (type, parent, position, title, dateAdded, lastModified, guid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                self.TYPE_FOLDER, unfiled_id, position, space_name,
                now_timestamp, now_timestamp, folder_guid
            ))

            # Get the created folder ID
            folder_id = cursor.lastrowid
            logger.info(f"  ✅ Created folder '{space_name}' (ID: {folder_id})")
            return folder_id

        except sqlite3.Error as e:
            logger.error(f"Failed to create folder '{space_name}': {e}")
            return None

    def _create_subfolder(self, conn: sqlite3.Connection, folder_name: str, parent_id: int, dry_run: bool = False) -> Optional[int]:
        """Create a subfolder under the given parent folder."""
        try:
            # Check if folder already exists
            cursor = conn.execute(
                "SELECT id FROM moz_bookmarks WHERE parent = ? AND title = ? AND type = ?",
                (parent_id, folder_name, self.TYPE_FOLDER)
            )
            existing = cursor.fetchone()
            if existing:
                logger.info(f"    📁 Subfolder '{folder_name}' already exists")
                return existing[0]

            if dry_run:
                logger.info(f"    📁 Would create subfolder: {folder_name}")
                return 999  # Dummy ID for dry run

            # Get next position in parent folder
            cursor = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
                (parent_id,)
            )
            position = cursor.fetchone()[0]

            # Generate GUID for the folder
            folder_guid = self._generate_guid()

            # Current timestamp in microseconds
            now_timestamp = int(time.time() * 1_000_000)

            # Insert the folder
            cursor = conn.execute("""
                INSERT INTO moz_bookmarks
                (type, parent, position, title, dateAdded, lastModified, guid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                self.TYPE_FOLDER, parent_id, position, folder_name,
                now_timestamp, now_timestamp, folder_guid
            ))

            # Get the created folder ID
            folder_id = cursor.lastrowid
            logger.info(f"    ✅ Created subfolder '{folder_name}' (ID: {folder_id})")
            return folder_id

        except sqlite3.Error as e:
            logger.error(f"Failed to create subfolder '{folder_name}': {e}")
            return None

    def _import_single_bookmark(self, conn: sqlite3.Connection, bookmark_data: Dict,
                              folder_id: int, dry_run: bool = False) -> bool:
        """Import a single bookmark into the specified folder."""
        try:
            url = bookmark_data['url']
            title = bookmark_data['title']

            # Check if URL already exists in moz_places
            cursor = conn.execute("SELECT id FROM moz_places WHERE url = ?", (url,))
            existing_place = cursor.fetchone()

            if existing_place:
                place_id = existing_place[0]
                if dry_run:
                    logger.debug(f"    Would reuse existing place for: {title[:50]}")
                else:
                    # Update visit count if higher
                    new_visit_count = bookmark_data.get('visit_count', 1)
                    conn.execute("""
                        UPDATE moz_places
                        SET visit_count = MAX(visit_count, ?),
                            last_visit_date = MAX(last_visit_date, ?)
                        WHERE id = ?
                    """, (new_visit_count, self._parse_visit_time(bookmark_data.get('last_visit_time')), place_id))
            else:
                if dry_run:
                    logger.debug(f"    Would create new place for: {title[:50]}")
                    place_id = 999  # Dummy ID
                else:
                    # Create new place
                    place_guid = self._generate_guid()
                    place_id = self._create_place(conn, url, title, bookmark_data, place_guid)

            # Check if bookmark already exists in this folder
            cursor = conn.execute("""
                SELECT id FROM moz_bookmarks
                WHERE fk = ? AND parent = ? AND type = ?
            """, (place_id, folder_id, self.TYPE_BOOKMARK))

            if cursor.fetchone():
                logger.debug(f"    Bookmark already exists: {title[:50]}")
                return False

            if dry_run:
                logger.debug(f"    Would create bookmark: {title[:50]}")
                return True

            # Create bookmark
            self._create_bookmark(conn, place_id, folder_id, title)
            return True

        except Exception as e:
            logger.error(f"Failed to import bookmark '{bookmark_data.get('title', 'Unknown')}': {e}")
            return False

    def _create_place(self, conn: sqlite3.Connection, url: str, title: str,
                     bookmark_data: Dict, place_guid: str) -> int:
        """Create a new place (URL) in moz_places."""
        # Calculate frecency (simplified)
        visit_count = bookmark_data.get('visit_count', 1)
        frecency = min(visit_count * 100, 2000)  # Cap at 2000

        # Generate URL hash
        url_hash = self._hash_url(url)

        # Reverse host for sorting
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            rev_host = ".".join(reversed(parsed.netloc.split(".")))
        except:
            rev_host = ""

        last_visit = self._parse_visit_time(bookmark_data.get('last_visit_time'))

        cursor = conn.execute("""
            INSERT INTO moz_places
            (url, title, rev_host, visit_count, frecency, last_visit_date, guid, url_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            url, title, rev_host, visit_count, frecency, last_visit,
            place_guid, url_hash
        ))

        return cursor.lastrowid

    def _create_bookmark(self, conn: sqlite3.Connection, place_id: int, folder_id: int, title: str):
        """Create a bookmark entry in moz_bookmarks."""
        # Get next position in folder
        cursor = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 FROM moz_bookmarks WHERE parent = ?",
            (folder_id,)
        )
        position = cursor.fetchone()[0]

        bookmark_guid = self._generate_guid()
        now_timestamp = self._now_microseconds()

        conn.execute("""
            INSERT INTO moz_bookmarks
            (type, fk, parent, position, title, dateAdded, lastModified, guid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.TYPE_BOOKMARK, place_id, folder_id, position, title,
            now_timestamp, now_timestamp, bookmark_guid
        ))

    def _generate_guid(self) -> str:
        """Generate a Firefox-style GUID."""
        return str(uuid.uuid4()).replace('-', '')[:12]

    def _now_microseconds(self) -> int:
        """Get current time in microseconds (Firefox format)."""
        return int(time.time() * 1_000_000)

    def _parse_visit_time(self, visit_time_str: Optional[str]) -> int:
        """Parse visit time string to Firefox timestamp."""
        if not visit_time_str:
            return self._now_microseconds()

        try:
            dt = datetime.fromisoformat(visit_time_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1_000_000)
        except:
            return self._now_microseconds()

    def _hash_url(self, url: str) -> int:
        """Generate hash for URL (simplified Firefox method)."""
        return hash(url.encode('utf-8')) & 0xFFFFFFFF


def main():
    """CLI interface for Zen bookmark import."""
    print("📥 Zen Browser Bookmark Importer")
    print("=" * 40)

    # Find Zen profile
    home_dir = Path.home()
    zen_profiles_dir = home_dir / "Library/Application Support/zen/Profiles"

    if not zen_profiles_dir.exists():
        print("❌ Zen profiles directory not found!")
        return

    # Find active profile (assume first one for now)
    profiles = [p for p in zen_profiles_dir.iterdir() if p.is_dir()]
    if not profiles:
        print("❌ No Zen profiles found!")
        return

    zen_profile = profiles[0]  # Use first profile
    print(f"📁 Using Zen profile: {zen_profile.name}")

    # Load Arc export data
    export_file = Path("arc_bookmarks_export.json")
    if not export_file.exists():
        print(f"❌ Arc export file not found: {export_file}")
        print("Run arc_bookmark_extractor.py first!")
        return

    with open(export_file, 'r') as f:
        arc_data = json.load(f)

    total_bookmarks = sum(len(p['bookmarks']) for p in arc_data['profiles'])
    print(f"📚 Found {total_bookmarks} Arc bookmarks to import")

    # Create importer
    importer = ZenBookmarkImporter(zen_profile)

    # Ask user for confirmation
    response = input("\n🤔 Proceed with import? (y/N): ").strip().lower()
    if response != 'y':
        print("❌ Import cancelled")
        return

    # Perform dry run first
    print("\n🧪 Performing dry run...")
    if not importer.import_arc_bookmarks(arc_data, dry_run=True):
        print("❌ Dry run failed!")
        return

    # Ask for final confirmation
    response = input("\n✅ Dry run successful. Proceed with actual import? (y/N): ").strip().lower()
    if response != 'y':
        print("❌ Import cancelled")
        return

    # Perform actual import
    print("\n📥 Importing bookmarks...")
    if importer.import_arc_bookmarks(arc_data, dry_run=False):
        print("\n🎉 Import completed successfully!")
        print("You can now open Zen browser to see your Arc bookmarks in the 'Unfiled Bookmarks' section.")
    else:
        print("\n❌ Import failed!")


if __name__ == "__main__":
    main()