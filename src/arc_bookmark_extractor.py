#!/usr/bin/env python3
"""
Arc Bookmark Extractor Module

Extracts bookmarks and browsing history from Arc profile SQLite databases.
Handles the Chromium History database format used by Arc.
"""

import sqlite3
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ArcBookmark:
    """Represents a bookmark/history entry from Arc."""
    url: str
    title: str
    visit_count: int
    last_visit_time: datetime
    typed_count: int = 0
    favicon_url: Optional[str] = None
    profile_id: str = ""
    is_bookmarked: bool = False

    def __str__(self):
        return f"ArcBookmark(title='{self.title[:30]}...', url='{self.url[:50]}...', visits={self.visit_count})"

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        # Convert datetime to string for JSON serialization
        data['last_visit_time'] = self.last_visit_time.isoformat()
        return data

@dataclass
class ProfileBookmarks:
    """Represents all bookmarks from a single Arc profile."""
    profile_id: str
    profile_name: str
    bookmarks: List[ArcBookmark]
    total_history_entries: int

    def __str__(self):
        return f"ProfileBookmarks(profile='{self.profile_name}', bookmarks={len(self.bookmarks)}, history={self.total_history_entries})"


class ArcBookmarkExtractor:
    """Extracts bookmarks and history from Arc profile databases."""

    def __init__(self):
        self.home_dir = Path.home()
        self.arc_data_dir = self.home_dir / "Library/Application Support/Arc/User Data"

    def extract_profile_bookmarks(self, profile_path: Path, profile_id: str = "") -> Optional[ProfileBookmarks]:
        """Extract bookmarks from a single Arc profile."""
        history_db = profile_path / "History"

        if not history_db.exists():
            logger.warning(f"No History database found at {history_db}")
            return None

        try:
            # Connect to the History database with timeout
            conn = sqlite3.connect(f"file:{history_db}?mode=ro", uri=True, timeout=10.0)
            conn.row_factory = sqlite3.Row

            # Extract basic info
            profile_name = self._get_profile_display_name(profile_id or profile_path.name)

            # Get all bookmarks/history
            bookmarks = self._extract_bookmarks_from_db(conn, profile_id or profile_path.name)
            total_history = self._get_total_history_count(conn)

            conn.close()

            logger.info(f"✅ Extracted {len(bookmarks)} bookmarks from {profile_name}")

            return ProfileBookmarks(
                profile_id=profile_id or profile_path.name,
                profile_name=profile_name,
                bookmarks=bookmarks,
                total_history_entries=total_history
            )

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                logger.warning(f"Database locked for {profile_name} - Arc may be running. Skipping this profile.")
            else:
                logger.error(f"Database error for {profile_path}: {e}")
            return None
        except sqlite3.Error as e:
            logger.error(f"Database error for {profile_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error extracting from {profile_path}: {e}")
            return None

    def _extract_bookmarks_from_db(self, conn: sqlite3.Connection, profile_id: str) -> List[ArcBookmark]:
        """Extract bookmarks from the History database."""
        bookmarks = []

        try:
            # First check what columns are available
            cursor = conn.execute("PRAGMA table_info(urls)")
            columns = [row[1] for row in cursor.fetchall()]

            # Build query based on available columns
            base_columns = [
                'u.url',
                'u.title',
                'u.visit_count',
                'u.last_visit_time',
                'u.typed_count'
            ]

            if 'favicon_id' in columns:
                base_columns.append('u.favicon_id')

            query = f"""
            SELECT DISTINCT
                {', '.join(base_columns)}
            FROM urls u
            WHERE u.visit_count > 0
                AND u.url NOT LIKE 'chrome://%'
                AND u.url NOT LIKE 'chrome-extension://%'
                AND u.url NOT LIKE 'about:%'
            ORDER BY u.last_visit_time DESC
            LIMIT 1000
            """

            logger.debug(f"Using query: {query}")
            cursor = conn.execute(query)
            rows = cursor.fetchall()

            for row in rows:
                # Convert Chromium timestamp (microseconds since 1601-01-01)
                last_visit = self._chromium_time_to_datetime(row['last_visit_time'])

                bookmark = ArcBookmark(
                    url=row['url'],
                    title=row['title'] or row['url'],  # Use URL if no title
                    visit_count=row['visit_count'],
                    last_visit_time=last_visit,
                    typed_count=row['typed_count'] or 0,
                    profile_id=profile_id
                )
                bookmarks.append(bookmark)

            logger.debug(f"Extracted {len(bookmarks)} entries from database")

        except sqlite3.Error as e:
            logger.error(f"Error querying database: {e}")

        return bookmarks

    def _get_total_history_count(self, conn: sqlite3.Connection) -> int:
        """Get total number of history entries."""
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM urls")
            return cursor.fetchone()[0]
        except sqlite3.Error:
            return 0

    def _chromium_time_to_datetime(self, chromium_time: int) -> datetime:
        """Convert Chromium timestamp to Python datetime."""
        if chromium_time == 0:
            return datetime.now(timezone.utc)

        # Chromium uses microseconds since Windows epoch (1601-01-01)
        # Convert to Unix timestamp (seconds since 1970-01-01)
        windows_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
        unix_epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        epoch_diff = (unix_epoch - windows_epoch).total_seconds()

        unix_timestamp = (chromium_time / 1_000_000) - epoch_diff
        return datetime.fromtimestamp(unix_timestamp, timezone.utc)

    def _get_profile_display_name(self, profile_id: str) -> str:
        """Get human-readable name for profile."""
        if profile_id == "Default":
            return "Default Space"
        elif profile_id.startswith("Profile "):
            return f"Arc Space {profile_id.split()[-1]}"
        else:
            return profile_id

    def filter_bookmarks(self, bookmarks: List[ArcBookmark], min_visits: int = 2,
                        exclude_patterns: List[str] = None) -> List[ArcBookmark]:
        """Filter bookmarks by visit count and URL patterns."""
        if exclude_patterns is None:
            exclude_patterns = [
                'chrome-extension://',
                'chrome://',
                'about:',
                'moz-extension://',
                'data:',
                'javascript:'
            ]

        filtered = []
        for bookmark in bookmarks:
            # Skip if below minimum visit threshold
            if bookmark.visit_count < min_visits:
                continue

            # Skip excluded URL patterns
            if any(pattern in bookmark.url for pattern in exclude_patterns):
                continue

            # Skip if no meaningful title
            if not bookmark.title or bookmark.title == bookmark.url:
                if bookmark.visit_count < 5:  # Higher threshold for untitled
                    continue

            filtered.append(bookmark)

        logger.info(f"Filtered {len(bookmarks)} → {len(filtered)} bookmarks")
        return filtered

    def export_to_json(self, profile_bookmarks: List[ProfileBookmarks],
                      output_file: Path) -> bool:
        """Export extracted bookmarks to JSON file."""
        try:
            export_data = {
                'export_timestamp': datetime.now(timezone.utc).isoformat(),
                'total_profiles': len(profile_bookmarks),
                'profiles': []
            }

            for profile in profile_bookmarks:
                profile_data = {
                    'profile_id': profile.profile_id,
                    'profile_name': profile.profile_name,
                    'total_bookmarks': len(profile.bookmarks),
                    'total_history_entries': profile.total_history_entries,
                    'bookmarks': [bookmark.to_dict() for bookmark in profile.bookmarks]
                }
                export_data['profiles'].append(profile_data)

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            logger.info(f"✅ Exported bookmarks to {output_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to export to JSON: {e}")
            return False

    def get_extraction_summary(self, profile_bookmarks: List[ProfileBookmarks]) -> Dict:
        """Generate summary statistics for extraction."""
        total_bookmarks = sum(len(p.bookmarks) for p in profile_bookmarks)
        total_history = sum(p.total_history_entries for p in profile_bookmarks)

        return {
            'total_profiles': len(profile_bookmarks),
            'total_bookmarks_extracted': total_bookmarks,
            'total_history_entries': total_history,
            'profiles_summary': [
                {
                    'name': p.profile_name,
                    'bookmarks': len(p.bookmarks),
                    'history': p.total_history_entries
                }
                for p in profile_bookmarks
            ]
        }


def main():
    """CLI interface for Arc bookmark extraction."""
    print("📚 Arc Bookmark Extractor")
    print("=" * 40)

    extractor = ArcBookmarkExtractor()

    # Find Arc profiles
    from arc_profile_discovery import ArcProfileDiscovery
    discovery = ArcProfileDiscovery()
    profiles = discovery.discover_profiles()

    if not profiles:
        print("❌ No Arc profiles found!")
        return

    print(f"Found {len(profiles)} Arc profiles to extract from...")

    all_profile_bookmarks = []

    for profile in profiles:
        print(f"\n🔍 Extracting from {profile.display_name}...")

        if not profile.has_history:
            print("  ⚠️  No history database found, skipping")
            continue

        profile_bookmarks = extractor.extract_profile_bookmarks(
            profile.profile_path,
            profile.profile_id
        )

        if profile_bookmarks:
            # Filter bookmarks (minimum 2 visits)
            filtered = extractor.filter_bookmarks(profile_bookmarks.bookmarks, min_visits=2)
            profile_bookmarks.bookmarks = filtered
            all_profile_bookmarks.append(profile_bookmarks)
            print(f"  ✅ {len(filtered)} bookmarks extracted")
        else:
            print("  ❌ Failed to extract bookmarks")

    if not all_profile_bookmarks:
        print("\n❌ No bookmarks were extracted!")
        return

    # Export to JSON
    output_file = Path("arc_bookmarks_export.json")
    success = extractor.export_to_json(all_profile_bookmarks, output_file)

    if success:
        summary = extractor.get_extraction_summary(all_profile_bookmarks)

        print(f"\n📊 Extraction Summary:")
        print(f"  Total profiles: {summary['total_profiles']}")
        print(f"  Total bookmarks: {summary['total_bookmarks_extracted']}")
        print(f"  Total history entries: {summary['total_history_entries']}")
        print(f"\n💾 Exported to: {output_file.absolute()}")

        print(f"\n📋 Per-profile breakdown:")
        for profile_info in summary['profiles_summary']:
            print(f"  • {profile_info['name']}: {profile_info['bookmarks']} bookmarks")


if __name__ == "__main__":
    main()