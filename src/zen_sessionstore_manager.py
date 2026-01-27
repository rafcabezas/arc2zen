#!/usr/bin/env python3
"""
Zen Sessionstore Manager

Creates or modifies Zen's sessionstore to add Arc pinned tabs as actual open tabs
in their respective workspaces with proper container assignments.
"""

import json
import lz4.block
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
import logging
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class ZenTab:
    """Represents a tab in Zen browser."""
    url: str
    title: str
    userContextId: int
    workspace_uuid: str

@dataclass
class ZenWorkspace:
    """Represents a Zen workspace."""
    uuid: str
    name: str
    container_id: int
    tabs: List[ZenTab]

class ZenSessionstoreManager:
    """Manages Zen browser sessionstore for workspace and tab creation."""

    def __init__(self, zen_profile_path: Path):
        self.zen_profile = zen_profile_path
        self.sessionstore_file = zen_profile_path / "sessionstore.jsonlz4"
        self.zen_sessions_file = zen_profile_path / "zen-sessions.jsonlz4"
        self.sessionstore_backup_dir = zen_profile_path / "sessionstore-backups"

    def get_workspace_mappings_from_db(self) -> Dict[str, Dict[str, Any]]:
        """Read workspace UUIDs from database."""
        import sqlite3
        workspace_mappings = {}

        try:
            places_db = self.zen_profile / "places.sqlite"
            with sqlite3.connect(str(places_db)) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT uuid, name, container_id
                    FROM zen_workspaces
                    ORDER BY position
                """)

                for uuid_val, name, container_id in cursor.fetchall():
                    workspace_mappings[name] = {
                        'uuid': uuid_val,
                        'container_id': container_id
                    }

            logger.info(f"✅ Read {len(workspace_mappings)} workspace mappings from database")
            return workspace_mappings
        except Exception as e:
            logger.error(f"Failed to read workspace mappings: {e}")
            return {}

    def decode_sessionstore(self, file_path: Path) -> Dict:
        """Decode Mozilla LZ4 compressed sessionstore file."""
        try:
            with open(file_path, 'rb') as f:
                data = f.read()

            logger.info(f"DEBUG decode: File size: {len(data)} bytes")

            # Check Mozilla LZ4 header
            if not data.startswith(b'mozLz40\0'):
                raise ValueError("Not a Mozilla LZ4 file")

            # Read uncompressed size from bytes 8-12
            uncompressed_size = int.from_bytes(data[8:12], 'little')
            logger.info(f"DEBUG decode: Expected uncompressed size: {uncompressed_size} bytes")

            # Skip header (8 bytes) and length (4 bytes)
            compressed_data = data[12:]
            logger.info(f"DEBUG decode: Compressed data size: {len(compressed_data)} bytes")

            # Decompress with explicit uncompressed size (required for Zen format)
            decompressed = lz4.block.decompress(compressed_data, uncompressed_size=uncompressed_size)
            logger.info(f"DEBUG decode: Decompressed size: {len(decompressed)} bytes")

            # Parse JSON
            return json.loads(decompressed.decode('utf-8'))

        except Exception as e:
            logger.error(f"Failed to decode sessionstore: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def encode_sessionstore(self, session_data: Dict, output_path: Path) -> bool:
        """Encode session data to Mozilla LZ4 format."""
        try:
            # Convert to JSON bytes
            json_data = json.dumps(session_data, separators=(',', ':')).encode('utf-8')
            logger.info(f"DEBUG: JSON data size: {len(json_data)} bytes")

            # Compress with LZ4 (store_size=False since we store size separately in header)
            compressed = lz4.block.compress(json_data, store_size=False)
            logger.info(f"DEBUG: Compressed size: {len(compressed)} bytes")

            # Create Mozilla header
            header = b'mozLz40\0'
            length = len(json_data).to_bytes(4, 'little')

            total_size = len(header) + len(length) + len(compressed)
            logger.info(f"DEBUG: Total file size will be: {total_size} bytes")

            # Write complete file
            with open(output_path, 'wb') as f:
                f.write(header + length + compressed)

            logger.info(f"DEBUG: Wrote file to {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to encode sessionstore: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def create_zen_tab_entry(self, tab: ZenTab) -> Dict:
        """Create a tab entry in Zen's native format for zen-sessions.jsonlz4."""
        timestamp = int(datetime.now().timestamp() * 1000)
        return {
            "entries": [
                {
                    "url": tab.url,
                    "title": tab.title,
                    "cacheKey": 0,
                    "ID": timestamp,
                    "docshellUUID": "{" + str(uuid.uuid4()) + "}",
                    "resultPrincipalURI": None,
                    "hasUserInteraction": False,
                    "triggeringPrincipal_base64": "{\"3\":{}}",
                    "docIdentifier": timestamp,
                    "transient": False,
                    "navigationKey": "{" + str(uuid.uuid4()) + "}",
                    "navigationId": "{" + str(uuid.uuid4()) + "}"
                }
            ],
            "lastAccessed": timestamp,
            "pinned": True,
            "hidden": False,
            "zenWorkspace": tab.workspace_uuid,
            "zenSyncId": "{" + str(uuid.uuid4()) + "}",
            "zenEssential": False,
            "zenDefaultUserContextId": "true" if tab.userContextId == 0 else "false",
            "zenPinnedIcon": None,
            "zenIsEmpty": False,
            "zenHasStaticIcon": False,
            "zenGlanceId": None,
            "zenIsGlance": False,
            "_zenPinnedInitialState": {
                "entry": {
                    "url": tab.url,
                    "title": tab.title,
                    "cacheKey": 0,
                    "ID": timestamp,
                    "docshellUUID": "{" + str(uuid.uuid4()) + "}",
                    "resultPrincipalURI": None,
                    "hasUserInteraction": False,
                    "triggeringPrincipal_base64": "{\"3\":{}}",
                    "docIdentifier": timestamp,
                    "transient": False,
                    "navigationKey": "{" + str(uuid.uuid4()) + "}",
                    "navigationId": "{" + str(uuid.uuid4()) + "}"
                },
                "image": None
            },
            "searchMode": None,
            "userContextId": tab.userContextId,
            "attributes": {},
            "index": 1,
            "storage": {},
            "userTypedValue": "",
            "userTypedClear": 0,
            "image": None
        }

    def add_tabs_to_zen_sessions(self, workspaces: List[ZenWorkspace]) -> bool:
        """Add tabs to Zen's zen-sessions.jsonlz4 file."""
        try:
            logger.info("🔧 Adding tabs to zen-sessions.jsonlz4...")

            # Read current zen-sessions
            zen_sessions = self.decode_sessionstore(self.zen_sessions_file)
            if not zen_sessions:
                logger.error("Failed to read zen-sessions.jsonlz4")
                return False

            logger.info(f"DEBUG: Current zen-sessions has {len(zen_sessions.get('tabs', []))} tabs")

            # Backup current zen-sessions
            backup_name = f"zen-sessions_backup_{int(datetime.now().timestamp())}.jsonlz4"
            backup_path = self.zen_profile / backup_name
            shutil.copy2(self.zen_sessions_file, backup_path)
            logger.info(f"✅ Backed up zen-sessions to {backup_name}")

            # Create tab entries for all workspaces
            new_tabs = []
            for workspace in workspaces:
                logger.info(f"DEBUG: Adding {len(workspace.tabs)} tabs for workspace '{workspace.name}'")
                for i, tab in enumerate(workspace.tabs):
                    zen_tab = self.create_zen_tab_entry(tab)
                    new_tabs.append(zen_tab)
                    if i < 2:
                        logger.info(f"DEBUG: Added tab: {tab.title[:50]}")

            # Add new tabs to existing tabs
            existing_tabs = zen_sessions.get('tabs', [])
            zen_sessions['tabs'] = existing_tabs + new_tabs

            logger.info(f"DEBUG: Total tabs now: {len(zen_sessions['tabs'])} (was {len(existing_tabs)}, added {len(new_tabs)})")

            # Write updated zen-sessions
            if not self.encode_sessionstore(zen_sessions, self.zen_sessions_file):
                logger.error("❌ Failed to write zen-sessions.jsonlz4")
                return False

            # Verify the written file is valid
            logger.info("DEBUG: Verifying written file...")
            verification = self.decode_sessionstore(self.zen_sessions_file)
            if not verification:
                logger.error("❌ Verification failed: Cannot read written zen-sessions.jsonlz4")
                logger.info("Restoring backup...")
                shutil.copy2(backup_path, self.zen_sessions_file)
                return False

            verified_tab_count = len(verification.get('tabs', []))
            expected_tab_count = len(existing_tabs) + len(new_tabs)

            if verified_tab_count != expected_tab_count:
                logger.error(f"❌ Verification failed: Expected {expected_tab_count} tabs, found {verified_tab_count}")
                logger.info("Restoring backup...")
                shutil.copy2(backup_path, self.zen_sessions_file)
                return False

            logger.info(f"✅ Verification passed: {verified_tab_count} tabs in zen-sessions.jsonlz4")
            logger.info(f"✅ Added {len(new_tabs)} tabs to zen-sessions.jsonlz4")
            return True

        except Exception as e:
            logger.error(f"Failed to add tabs to zen-sessions: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def create_tab_entry(self, tab: ZenTab) -> Dict:
        """Create a tab entry for the sessionstore."""
        return {
            "entries": [
                {
                    "url": tab.url,
                    "title": tab.title,
                    "charset": "UTF-8",
                    "ID": int(datetime.now().timestamp() * 1000),
                    "docshellUUID": str(uuid.uuid4()),
                    "originalURI": tab.url,
                    "resultPrincipalURI": tab.url,
                    "hasUserInteraction": False,
                    "persist": True
                }
            ],
            "lastAccessed": int(datetime.now().timestamp() * 1000),
            "hidden": False,
            "attributes": {},
            "userContextId": tab.userContextId,
            "index": 1,
            "image": None
        }

    def create_workspace_session(self, workspaces: List[ZenWorkspace]) -> Dict:
        """Create a complete session with workspaces and tabs."""
        logger.info(f"DEBUG: create_workspace_session called with {len(workspaces)} workspaces")

        windows = []

        for workspace in workspaces:
            logger.info(f"DEBUG: Processing workspace '{workspace.name}' with {len(workspace.tabs)} tabs")

            if not workspace.tabs:
                logger.warning(f"DEBUG: Skipping workspace '{workspace.name}' - no tabs!")
                continue

            # Create tabs for this workspace
            tabs = []
            for i, tab in enumerate(workspace.tabs):
                tab_entry = self.create_tab_entry(tab)
                tabs.append(tab_entry)
                if i < 2:  # Log first 2 tabs per workspace
                    logger.info(f"DEBUG: Created tab entry: {tab.title[:40]}")

            logger.info(f"DEBUG: Created {len(tabs)} tab entries for workspace '{workspace.name}'")

            # Create window for this workspace
            window = {
                "tabs": tabs,
                "selected": 1,  # First tab selected
                "width": 1200,
                "height": 900,
                "screenX": 100,
                "screenY": 100,
                "sizemode": "normal",
                "sidebar": {
                    "command": "",
                    "visible": False
                },
                "workspaceID": workspace.uuid,
                "userContextId": workspace.container_id
            }
            windows.append(window)
            logger.info(f"DEBUG: Added window for workspace '{workspace.name}', total windows now: {len(windows)}")

        logger.info(f"DEBUG: Final windows count: {len(windows)}")

        # Create complete session
        session = {
            "version": ["sessionrestore", 1],
            "windows": windows,
            "selectedWindow": 1,
            "session": {
                "state": "running",
                "lastUpdate": int(datetime.now().timestamp() * 1000),
                "startTime": int(datetime.now().timestamp() * 1000),
                "recentCrashes": 0
            }
        }

        return session

    def backup_current_session(self) -> bool:
        """Backup current sessionstore before modification."""
        try:
            if self.sessionstore_file.exists():
                backup_name = f"sessionstore_backup_{int(datetime.now().timestamp())}.jsonlz4"
                backup_path = self.zen_profile / backup_name
                shutil.copy2(self.sessionstore_file, backup_path)
                logger.info(f"✅ Backed up sessionstore to {backup_name}")
                return True
            return True

        except Exception as e:
            logger.error(f"Failed to backup sessionstore: {e}")
            return False

    def create_workspaces_with_tabs(self, arc_export_data: Dict, container_mappings: Dict[str, int], dry_run: bool = False) -> bool:
        """Create workspaces with tabs from Arc export data."""
        try:
            logger.info("🔧 Creating Zen sessionstore from Arc data...")

            # DEBUG: Show input data
            logger.info(f"DEBUG: Received {len(arc_export_data.get('spaces', []))} spaces from arc_export_data")
            for space in arc_export_data.get('spaces', []):
                logger.info(f"DEBUG: Space '{space['space_name']}' has {len(space.get('pinned_tabs', []))} pinned tabs")

            if dry_run:
                logger.info("🧪 DRY RUN - No sessionstore changes will be made")
                return True

            # Read workspace UUIDs from database (instead of creating new ones)
            db_workspaces = self.get_workspace_mappings_from_db()
            logger.info(f"DEBUG: Database has {len(db_workspaces)} workspaces")
            for name, info in db_workspaces.items():
                logger.info(f"DEBUG: DB workspace '{name}': UUID={info['uuid']}, container={info['container_id']}")

            if not db_workspaces:
                logger.error("No workspaces found in database! Run workspace import first.")
                return False

            # Create workspace objects
            workspaces = []
            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                logger.info(f"DEBUG: Processing space '{space_name}'...")

                # Use database UUID instead of creating new one
                workspace_info = db_workspaces.get(space_name)
                if not workspace_info:
                    logger.warning(f"Workspace '{space_name}' not found in database, skipping")
                    logger.info(f"DEBUG: Available DB workspaces: {list(db_workspaces.keys())}")
                    continue

                workspace_uuid = workspace_info['uuid']
                container_id = workspace_info['container_id']
                logger.info(f"DEBUG: Matched to UUID={workspace_uuid}, container={container_id}")

                # Create tabs for this workspace
                tabs = []
                pinned_tabs_data = space.get('pinned_tabs', [])
                logger.info(f"DEBUG: Creating {len(pinned_tabs_data)} tabs for workspace '{space_name}'")

                for i, pinned_tab in enumerate(pinned_tabs_data):
                    tab = ZenTab(
                        url=pinned_tab['url'],
                        title=pinned_tab['title'],
                        userContextId=container_id,
                        workspace_uuid=workspace_uuid
                    )
                    tabs.append(tab)
                    if i < 3:  # Log first 3 tabs
                        logger.info(f"DEBUG: Tab {i+1}: {pinned_tab['title'][:50]} - {pinned_tab['url'][:50]}")

                workspace = ZenWorkspace(
                    uuid=workspace_uuid,
                    name=space_name,
                    container_id=container_id,
                    tabs=tabs
                )
                workspaces.append(workspace)

                logger.info(f"  📁 Workspace: {space_name} ({len(tabs)} tabs, container {container_id})")

            logger.info(f"DEBUG: Created {len(workspaces)} workspace objects with tabs")

            # Add tabs to Zen's zen-sessions.jsonlz4 (Zen's native format)
            if self.add_tabs_to_zen_sessions(workspaces):
                logger.info("✅ Added tabs to zen-sessions.jsonlz4")
                logger.info("🔄 Restart Zen browser to see your migrated tabs")
                return True
            else:
                logger.error("❌ Failed to add tabs to zen-sessions.jsonlz4")
                return False

        except Exception as e:
            logger.error(f"Failed to create sessionstore: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False