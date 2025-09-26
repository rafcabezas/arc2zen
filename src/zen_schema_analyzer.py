#!/usr/bin/env python3
"""
Zen Browser Schema Analyzer

Analyzes Zen browser's places.sqlite database schema for bookmark import.
Based on Firefox's schema since Zen is Firefox-based.
"""

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ZenSchemaAnalyzer:
    """Analyzes Zen browser database schema."""

    def __init__(self):
        self.home_dir = Path.home()
        self.zen_data_dir = self.home_dir / "Library/Application Support/zen"

    def find_zen_profiles(self) -> List[Path]:
        """Find all Zen browser profile directories, sorted by modification time (newest first)."""
        profiles_dir = self.zen_data_dir / "Profiles"
        if not profiles_dir.exists():
            logger.error(f"Zen profiles directory not found: {profiles_dir}")
            return []

        profile_dirs = [p for p in profiles_dir.iterdir()
                       if p.is_dir() and not p.name.startswith('.')]

        # Sort by modification time, newest first (most likely to be active)
        profile_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        logger.info(f"Found {len(profile_dirs)} Zen profiles")
        return profile_dirs

    def analyze_places_schema(self, profile_path: Path) -> Optional[Dict]:
        """Analyze the places.sqlite schema from a Zen profile."""
        places_db = profile_path / "places.sqlite"

        if not places_db.exists():
            logger.warning(f"No places.sqlite found in {profile_path}")
            return None

        try:
            # Try to connect with a short timeout
            conn = sqlite3.connect(f"file:{places_db}?mode=ro", uri=True, timeout=1.0)

            schema_info = {
                'profile_path': str(profile_path),
                'tables': {},
                'bookmark_structure': None
            }

            # Get all tables
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            logger.info(f"Found {len(tables)} tables in places.sqlite")
            logger.debug(f"Tables: {tables}")

            # Analyze key tables for bookmarks
            key_tables = ['moz_bookmarks', 'moz_places', 'moz_bookmarks_deleted']

            for table in tables:
                if table in key_tables or 'bookmark' in table.lower():
                    try:
                        cursor = conn.execute(f"PRAGMA table_info({table})")
                        columns = [(row[1], row[2]) for row in cursor.fetchall()]  # name, type
                        schema_info['tables'][table] = columns

                        # Get row count
                        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cursor.fetchone()[0]
                        schema_info['tables'][f'{table}_count'] = count

                        logger.info(f"  {table}: {len(columns)} columns, {count} rows")

                    except sqlite3.Error as e:
                        logger.warning(f"Could not analyze table {table}: {e}")

            # Try to understand bookmark structure
            if 'moz_bookmarks' in schema_info['tables']:
                schema_info['bookmark_structure'] = self._analyze_bookmark_structure(conn)

            conn.close()
            return schema_info

        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower():
                logger.warning(f"Zen database locked - Zen may be running")
            else:
                logger.error(f"Database error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error analyzing schema: {e}")
            return None

    def _analyze_bookmark_structure(self, conn: sqlite3.Connection) -> Dict:
        """Analyze Firefox/Zen bookmark structure."""
        structure = {}

        try:
            # Get bookmark types
            cursor = conn.execute("""
                SELECT DISTINCT type, COUNT(*) as count
                FROM moz_bookmarks
                GROUP BY type
                ORDER BY count DESC
            """)

            structure['bookmark_types'] = dict(cursor.fetchall())

            # Get folder structure
            cursor = conn.execute("""
                SELECT b.title, b.type, COUNT(*) as children
                FROM moz_bookmarks b
                WHERE b.type = 2  -- folders
                GROUP BY b.title, b.type
                ORDER BY children DESC
                LIMIT 10
            """)

            structure['top_folders'] = cursor.fetchall()

            # Sample bookmark data
            cursor = conn.execute("""
                SELECT b.title, p.url, b.type, b.parent
                FROM moz_bookmarks b
                LEFT JOIN moz_places p ON b.fk = p.id
                WHERE b.title IS NOT NULL
                LIMIT 5
            """)

            structure['sample_bookmarks'] = cursor.fetchall()

        except sqlite3.Error as e:
            logger.warning(f"Error analyzing bookmark structure: {e}")
            structure['error'] = str(e)

        return structure

    def get_firefox_bookmark_schema(self) -> Dict:
        """Return known Firefox bookmark schema for reference."""
        return {
            'moz_bookmarks': {
                'columns': [
                    ('id', 'INTEGER PRIMARY KEY'),
                    ('type', 'INTEGER'),  # 1=bookmark, 2=folder, 3=separator
                    ('fk', 'INTEGER'),    # foreign key to moz_places
                    ('parent', 'INTEGER'), # parent folder id
                    ('position', 'INTEGER'),
                    ('title', 'LONGVARCHAR'),
                    ('keyword_id', 'INTEGER'),
                    ('folder_type', 'TEXT'),
                    ('dateAdded', 'INTEGER'),
                    ('lastModified', 'INTEGER'),
                    ('guid', 'TEXT'),
                    ('syncStatus', 'INTEGER'),
                    ('syncChangeCounter', 'INTEGER')
                ],
                'description': 'Main bookmarks table with hierarchy'
            },
            'moz_places': {
                'columns': [
                    ('id', 'INTEGER PRIMARY KEY'),
                    ('url', 'LONGVARCHAR'),
                    ('title', 'LONGVARCHAR'),
                    ('rev_host', 'LONGVARCHAR'),
                    ('visit_count', 'INTEGER'),
                    ('hidden', 'INTEGER'),
                    ('typed', 'INTEGER'),
                    ('frecency', 'INTEGER'),
                    ('last_visit_date', 'INTEGER'),
                    ('guid', 'TEXT'),
                    ('foreign_count', 'INTEGER'),
                    ('url_hash', 'INTEGER'),
                    ('description', 'TEXT'),
                    ('preview_image_url', 'TEXT'),
                    ('origin_id', 'INTEGER')
                ],
                'description': 'Places/URLs referenced by bookmarks'
            }
        }


def main():
    """CLI interface for Zen schema analysis."""
    print("🔍 Zen Browser Schema Analyzer")
    print("=" * 40)

    analyzer = ZenSchemaAnalyzer()
    profiles = analyzer.find_zen_profiles()

    if not profiles:
        print("❌ No Zen profiles found!")
        return

    # Try to analyze the first available profile
    schema_info = None
    for profile in profiles:
        print(f"\n📁 Analyzing profile: {profile.name}")
        schema_info = analyzer.analyze_places_schema(profile)
        if schema_info:
            break

    if not schema_info:
        print("\n❌ Could not analyze any Zen databases (likely locked)")
        print("\n📚 Using known Firefox schema reference:")

        reference_schema = analyzer.get_firefox_bookmark_schema()
        for table_name, info in reference_schema.items():
            print(f"\n📊 {table_name}:")
            print(f"  Purpose: {info['description']}")
            print(f"  Columns ({len(info['columns'])}):")
            for col_name, col_type in info['columns']:
                print(f"    • {col_name}: {col_type}")

        return

    # Display schema analysis
    print(f"\n📊 Schema Analysis Results:")
    print(f"  Profile: {Path(schema_info['profile_path']).name}")
    print(f"  Tables: {len(schema_info['tables'])} analyzed")

    for table_name, columns in schema_info['tables'].items():
        if isinstance(columns, list):  # actual table info
            count = schema_info['tables'].get(f'{table_name}_count', 'unknown')
            print(f"\n  📋 {table_name} ({count} rows):")
            for col_name, col_type in columns:
                print(f"    • {col_name}: {col_type}")

    # Show bookmark structure if available
    if schema_info.get('bookmark_structure'):
        structure = schema_info['bookmark_structure']
        print(f"\n🏗️ Bookmark Structure:")

        if 'bookmark_types' in structure:
            print("  Types:")
            for type_id, count in structure['bookmark_types'].items():
                type_name = {1: 'bookmark', 2: 'folder', 3: 'separator'}.get(type_id, f'unknown({type_id})')
                print(f"    • {type_name}: {count}")

        if 'top_folders' in structure:
            print("  Top folders:")
            for title, type_id, children in structure['top_folders']:
                print(f"    • {title}: {children} items")


if __name__ == "__main__":
    main()