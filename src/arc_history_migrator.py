#!/usr/bin/env python3
"""
Arc History Migrator

Migrates Chrome-format browsing history (Arc) to Firefox-format (Zen).

Arc uses Chromium's History SQLite:
  urls(id, url, title, visit_count, typed_count, last_visit_time, hidden)
  visits(id, url→urls.id, visit_time, from_visit, transition, ...)

Zen uses Firefox's places.sqlite:
  moz_places(id, url, title, rev_host, visit_count, hidden, typed,
             frecency, last_visit_date, guid, url_hash, ...)
  moz_historyvisits(id, from_visit, place_id, visit_date, visit_type,
                    session, source, triggeringPlaceId)

Time format: Chrome = microseconds since 1601-01-01 (Windows epoch)
             Firefox = microseconds since 1970-01-01 (Unix epoch)
"""

import sqlite3
import uuid
import zlib
import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Microseconds between Windows epoch (1601-01-01) and Unix epoch (1970-01-01)
CHROME_TO_UNIX_OFFSET = 11_644_473_600_000_000

# Chrome core page transition type (lower 8 bits) → Firefox visit_type
_TRANSITION_MAP = {
    0: 1,  # LINK        → TRANSITION_LINK
    1: 2,  # TYPED       → TRANSITION_TYPED
    2: 3,  # AUTO_BOOKMARK → TRANSITION_BOOKMARK
    3: 4,  # AUTO_SUBFRAME → TRANSITION_EMBED
    4: 4,  # MANUAL_SUBFRAME → TRANSITION_EMBED
}


class ArcHistoryMigrator:
    """Migrates Arc (Chromium) browsing history to Zen (Firefox) places.sqlite."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile_path = zen_profile_path
        self.zen_db = zen_profile_path / "places.sqlite"

    def find_arc_history_paths(self) -> list:
        """Return all Arc History databases that contain rows."""
        arc_data = Path.home() / "Library/Application Support/Arc/User Data"
        if not arc_data.exists():
            return []
        paths = []
        for profile_dir in sorted(arc_data.iterdir()):
            history = profile_dir / "History"
            if not (history.exists() and history.stat().st_size > 0):
                continue
            try:
                conn = sqlite3.connect(f"file:{history}?mode=ro", uri=True)
                count = conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
                conn.close()
                if count > 0:
                    paths.append(history)
                    logger.info(f"  Found Arc history: {profile_dir.name} ({count} URLs)")
            except Exception as e:
                logger.warning(f"  Skipping {history.parent.name}: {e}")
        return paths

    def migrate(self, dry_run: bool = False) -> dict:
        """
        Migrate all Arc history into Zen's places.sqlite.
        Returns {"inserted": N, "updated": N, "visits": N}.
        """
        arc_paths = self.find_arc_history_paths()
        if not arc_paths:
            logger.warning("No Arc history databases found")
            return {"inserted": 0, "updated": 0, "visits": 0}

        if dry_run:
            return self._dry_run_stats(arc_paths)

        stats = {"inserted": 0, "updated": 0, "visits": 0}
        zen_conn = sqlite3.connect(str(self.zen_db))
        zen_conn.execute("PRAGMA journal_mode=WAL")
        try:
            # Cache existing URLs to avoid duplicates
            existing = {}  # url → (place_id, visit_count, last_visit_date)
            for row in zen_conn.execute(
                "SELECT id, url, visit_count, last_visit_date FROM moz_places"
            ):
                existing[row[1]] = (row[0], row[2] or 0, row[3] or 0)

            for arc_path in arc_paths:
                self._migrate_one(arc_path, zen_conn, existing, stats)

            zen_conn.commit()
        except Exception:
            zen_conn.rollback()
            raise
        finally:
            zen_conn.close()

        return stats

    # ------------------------------------------------------------------ #

    def _dry_run_stats(self, arc_paths: list) -> dict:
        total_urls = 0
        total_visits = 0
        for path in arc_paths:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            total_urls += conn.execute("SELECT COUNT(*) FROM urls").fetchone()[0]
            total_visits += conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
            conn.close()
        logger.info(
            f"DRY RUN: Would migrate {total_urls} URLs and {total_visits} visits "
            f"from {len(arc_paths)} Arc profile(s)"
        )
        return {"inserted": total_urls, "updated": 0, "visits": total_visits}

    def _migrate_one(
        self,
        arc_path: Path,
        zen_conn: sqlite3.Connection,
        existing: dict,
        stats: dict,
    ):
        arc_conn = sqlite3.connect(f"file:{arc_path}?mode=ro", uri=True)
        arc_conn.row_factory = sqlite3.Row
        try:
            arc_urls = {
                row["id"]: dict(row)
                for row in arc_conn.execute(
                    "SELECT id, url, title, visit_count, typed_count, last_visit_time, hidden "
                    "FROM urls"
                )
            }
            arc_visits = arc_conn.execute(
                "SELECT url, visit_time, transition FROM visits ORDER BY visit_time"
            ).fetchall()
        finally:
            arc_conn.close()

        # arc_id → zen place_id
        id_map = {}

        for arc_id, row in arc_urls.items():
            url = row["url"]
            title = row["title"] or ""
            arc_last = _chrome_to_firefox(row["last_visit_time"])

            if url in existing:
                zen_id, zen_count, zen_last = existing[url]
                new_count = zen_count + row["visit_count"]
                new_last = max(zen_last, arc_last)
                zen_conn.execute(
                    "UPDATE moz_places SET visit_count=?, last_visit_date=? WHERE id=?",
                    (new_count, new_last, zen_id),
                )
                existing[url] = (zen_id, new_count, new_last)
                id_map[arc_id] = zen_id
                stats["updated"] += 1
            else:
                zen_id = self._insert_place(zen_conn, url, title, row, arc_last)
                existing[url] = (zen_id, row["visit_count"], arc_last)
                id_map[arc_id] = zen_id
                stats["inserted"] += 1

        for visit in arc_visits:
            zen_place_id = id_map.get(visit[0])
            if zen_place_id is None:
                continue
            visit_date = _chrome_to_firefox(visit[1])
            visit_type = _TRANSITION_MAP.get(visit[2] & 0xFF, 1)
            zen_conn.execute(
                "INSERT INTO moz_historyvisits "
                "(place_id, visit_date, visit_type, session, source) "
                "VALUES (?, ?, ?, 0, 0)",
                (zen_place_id, visit_date, visit_type),
            )
            stats["visits"] += 1

    def _insert_place(
        self,
        conn: sqlite3.Connection,
        url: str,
        title: str,
        arc_row: dict,
        last_visit: int,
    ) -> int:
        visit_count = arc_row["visit_count"]
        typed = 1 if arc_row["typed_count"] > 0 else 0
        frecency = min(visit_count * 100, 2000)
        cursor = conn.execute(
            """INSERT INTO moz_places
               (url, title, rev_host, visit_count, hidden, typed,
                frecency, last_visit_date, guid, url_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                url,
                title,
                _rev_host(url),
                visit_count,
                arc_row["hidden"],
                typed,
                frecency,
                last_visit,
                _make_guid(),
                _hash_url(url),
            ),
        )
        return cursor.lastrowid


# ------------------------------------------------------------------ #
# Helpers


def _chrome_to_firefox(chrome_time: int) -> int:
    return chrome_time - CHROME_TO_UNIX_OFFSET


def _rev_host(url: str) -> str:
    try:
        host = urlparse(url).netloc.split(":")[0]
        return ".".join(reversed(host.split("."))) if host else ""
    except Exception:
        return ""


def _hash_url(url: str) -> int:
    return zlib.crc32(url.encode("utf-8")) & 0xFFFFFFFF


def _make_guid() -> str:
    return str(uuid.uuid4()).replace("-", "")[:12]
