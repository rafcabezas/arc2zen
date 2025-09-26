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
        self.sessionstore_backup_dir = zen_profile_path / "sessionstore-backups"

    def decode_sessionstore(self, file_path: Path) -> Dict:
        """Decode Mozilla LZ4 compressed sessionstore file."""
        try:
            with open(file_path, 'rb') as f:
                data = f.read()

            # Check Mozilla LZ4 header
            if not data.startswith(b'mozLz40\\0'):
                raise ValueError("Not a Mozilla LZ4 file")

            # Skip header (8 bytes) and length (4 bytes)
            compressed_data = data[12:]

            # Decompress
            decompressed = lz4.block.decompress(compressed_data)

            # Parse JSON
            return json.loads(decompressed.decode('utf-8'))

        except Exception as e:
            logger.error(f"Failed to decode sessionstore: {e}")
            return {}

    def encode_sessionstore(self, session_data: Dict, output_path: Path) -> bool:
        """Encode session data to Mozilla LZ4 format."""
        try:
            # Convert to JSON bytes
            json_data = json.dumps(session_data, separators=(',', ':')).encode('utf-8')

            # Compress with LZ4
            compressed = lz4.block.compress(json_data)

            # Create Mozilla header
            header = b'mozLz40\\0'
            length = len(json_data).to_bytes(4, 'little')

            # Write complete file
            with open(output_path, 'wb') as f:
                f.write(header + length + compressed)

            return True

        except Exception as e:
            logger.error(f"Failed to encode sessionstore: {e}")
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
        windows = []

        for workspace in workspaces:
            if not workspace.tabs:
                continue

            # Create tabs for this workspace
            tabs = []
            for tab in workspace.tabs:
                tab_entry = self.create_tab_entry(tab)
                tabs.append(tab_entry)

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
            logger.info("🔧 Creating Zen workspaces with tabs from Arc data...")

            if dry_run:
                logger.info("🧪 DRY RUN - No sessionstore changes will be made")

            # Create workspace objects
            workspaces = []
            for space in arc_export_data.get('spaces', []):
                space_name = space['space_name']
                container_id = container_mappings.get(space_name, 1)
                workspace_uuid = str(uuid.uuid4())

                # Create tabs for this workspace
                tabs = []
                for pinned_tab in space.get('pinned_tabs', [])[:10]:  # Limit for testing
                    tab = ZenTab(
                        url=pinned_tab['url'],
                        title=pinned_tab['title'],
                        userContextId=container_id,
                        workspace_uuid=workspace_uuid
                    )
                    tabs.append(tab)

                workspace = ZenWorkspace(
                    uuid=workspace_uuid,
                    name=space_name,
                    container_id=container_id,
                    tabs=tabs
                )
                workspaces.append(workspace)

                logger.info(f"  📁 Workspace: {space_name} ({len(tabs)} tabs, container {container_id})")

            if dry_run:
                logger.info(f"🧪 Would create {len(workspaces)} workspaces with tabs")
                return True

            # Backup current session
            if not self.backup_current_session():
                return False

            # Create new session with workspaces
            session_data = self.create_workspace_session(workspaces)

            # Write new sessionstore
            if self.encode_sessionstore(session_data, self.sessionstore_file):
                logger.info("✅ Created new sessionstore with workspaces and tabs")
                logger.info("🔄 Restart Zen browser to see your workspaces with tabs")
                return True
            else:
                logger.error("❌ Failed to write sessionstore")
                return False

        except Exception as e:
            logger.error(f"Failed to create workspaces with tabs: {e}")
            return False