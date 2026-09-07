#!/usr/bin/env python3
# Arc → Zen Session Tab Injector
# Injects Arc tabs into zen-sessions.jsonlz4 as real browser tabs.
# Zen renders sidebar tabs from this file, NOT from the zen_pins DB.
# Re-running replaces previously-injected tabs (idempotent).
# Requires: pip install lz4

import argparse
import json
import struct
import lz4.block
import sqlite3
import glob
import os
import sys
import uuid
import shutil
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from arc_pinned_tab_extractor import ArcPinnedTabExtractor


# ---------------------------------------------------------------------------
# Mozilla LZ4 file I/O (zen-sessions.jsonlz4 format)
# ---------------------------------------------------------------------------

def read_mozlz4(path):
    # mozLz4 format: 8-byte magic + 4-byte LE size + lz4 block
    with open(path, 'rb') as f:
        magic = f.read(8)
        if magic != b'mozLz40\0':
            raise ValueError(f"Not a Mozilla LZ4 file (magic: {magic!r})")
        size = struct.unpack('<I', f.read(4))[0]
        return json.loads(lz4.block.decompress(f.read(), uncompressed_size=size))


def write_mozlz4(path, data):
    json_bytes = json.dumps(data, separators=(',', ':')).encode('utf-8')
    compressed = lz4.block.compress(json_bytes, store_size=False)
    with open(path, 'wb') as f:
        f.write(b'mozLz40\0')
        f.write(struct.pack('<I', len(json_bytes)))
        f.write(compressed)


# ---------------------------------------------------------------------------
# Zen profile discovery
# ---------------------------------------------------------------------------

def find_zen_profile():
    # Auto-detect Zen profile dir (macOS + Windows)
    if os.name == 'nt':
        base = os.path.expandvars(r'%APPDATA%\zen\Profiles')
    else:
        base = os.path.expanduser('~/Library/Application Support/zen/Profiles')

    pattern = os.path.join(base, '*', 'places.sqlite')
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(
            f"No Zen profile found. Looked in: {base}\n"
            "Make sure Zen browser has been run at least once."
        )
    profiles = [os.path.dirname(m) for m in matches]
    with_sessions = [
        p for p in profiles
        if os.path.isfile(os.path.join(p, 'zen-sessions.jsonlz4'))
    ]
    candidates = with_sessions if with_sessions else profiles

    def _profile_recency(p):
        sess = os.path.join(p, 'zen-sessions.jsonlz4')
        if os.path.isfile(sess):
            return os.path.getmtime(sess)
        return os.path.getmtime(os.path.join(p, 'places.sqlite'))

    return max(candidates, key=_profile_recency)


def _container_ids_by_space_name(profile_path):
    """Map workspace/container name -> userContextId from containers.json."""
    path = os.path.join(profile_path, 'containers.json')
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for ident in data.get('identities', []):
        raw = ident.get('name') or ident.get('l10nId', '').replace('user-context-', '')
        cid = ident.get('userContextId')
        if not raw or cid is None:
            continue
        out[raw] = int(cid)
        out[raw.lower()] = int(cid)
    return out


def _workspace_map_from_zen_sessions(profile_path):
    """Zen 1.18+: workspaces live in zen-sessions.jsonlz4, not places.sqlite."""
    sess_file = os.path.join(profile_path, 'zen-sessions.jsonlz4')
    session = read_mozlz4(sess_file)
    containers = _container_ids_by_space_name(profile_path)
    result = {}
    for space in session.get('spaces', []):
        name = space.get('name')
        ws_uuid = space.get('uuid')
        if not name or not ws_uuid:
            continue
        cid = space.get('container_id', space.get('userContextId'))
        if cid is None:
            cid = containers.get(name)
            if cid is None:
                cid = containers.get(name.lower(), 0)
        else:
            cid = int(cid)
        result[name] = {'uuid': ws_uuid, 'container_id': int(cid)}
    return result


def get_workspace_map(profile_path):
    """Workspace name → {uuid, container_id} for legacy DB or modern session file."""
    db = os.path.join(profile_path, 'places.sqlite')
    if os.path.isfile(db):
        try:
            conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            cur = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='zen_workspaces' LIMIT 1"
            )
            if cur.fetchone():
                result = {}
                for name, ws_uuid, cid in conn.execute(
                    'SELECT name, uuid, container_id FROM zen_workspaces ORDER BY position'
                ).fetchall():
                    result[name] = {'uuid': ws_uuid, 'container_id': cid or 0}
                conn.close()
                return result
            conn.close()
        except sqlite3.Error:
            pass

    sess_file = os.path.join(profile_path, 'zen-sessions.jsonlz4')
    if os.path.isfile(sess_file):
        return _workspace_map_from_zen_sessions(profile_path)
    return {}


# ---------------------------------------------------------------------------
# Session tab construction
# ---------------------------------------------------------------------------

def make_session_tab(url, title, workspace_uuid, container_id,
                     pinned=False, essential=False, group_id=None):
    # Build a single tab entry for zen-sessions.jsonlz4
    # zenWorkspace = which sidebar, zenEssential = top toolbar, groupId = folder
    now_ms = int(datetime.now().timestamp() * 1000)
    doc_id = int.from_bytes(os.urandom(4), 'big')
    entry_id = int.from_bytes(os.urandom(4), 'big')

    tab = {
        "entries": [{
            "url": url,
            "title": title or url,
            "cacheKey": 0,
            "ID": entry_id,
            "docshellUUID": "{" + str(uuid.uuid4()) + "}",
            "originalURI": url,
            "resultPrincipalURI": None,
            "hasUserInteraction": False,
            "triggeringPrincipal_base64": '{"3":{}}',
            "docIdentifier": doc_id,
            "transient": False,
        }],
        "lastAccessed": now_ms,
        "hidden": False,
        "zenWorkspace": workspace_uuid,
        "zenSyncId": f"{now_ms}-{doc_id}",
        "zenEssential": essential,
        "pinned": pinned,
        "zenDefaultUserContextId": "true",
        "zenPinnedIcon": None,
        "zenIsEmpty": False,
        "zenHasStaticIcon": False,
        "zenGlanceId": None,
        "zenIsGlance": False,
        "zenLiveFolderItemId": None,
        "searchMode": None,
        "userContextId": container_id,
        "attributes": {},
        "index": 1,
        "scroll": {"scroll": "0,0"},
        "storage": {},
        "userTypedValue": "",
        "userTypedClear": 0,
        "image": None,
    }
    if group_id:
        tab["groupId"] = group_id
    return tab


def make_session_folder(folder_id, name, workspace_uuid, pinned=True, parent_id=None):
    # Build a folder entry for the session 'folders' array
    # Tabs inside this folder reference it via groupId = folder_id
    return {
        "pinned": pinned,
        "splitViewGroup": False,
        "id": folder_id,
        "name": name,
        "collapsed": False,
        "saveOnWindowClose": True,
        "parentId": parent_id,
        "prevSiblingInfo": None,
        "emptyTabIds": [],
        "userIcon": "",
        "workspaceId": workspace_uuid,
    }


def make_session_group(folder_id, name, pinned=True):
    # Build matching 'groups' entry (Zen stores both folders + groups)
    return {
        "pinned": pinned,
        "splitView": False,
        "id": folder_id,
        "name": name,
        "color": "zen-workspace-color",
        "collapsed": False,
        "saveOnWindowClose": True,
    }


def make_session_placeholder_tab(workspace_uuid, group_id):
    # Empty about:blank tab marking an otherwise-empty folder so Zen retains it.
    # Zen prunes folders that contain no direct tabs on session load; without
    # a placeholder, structural parent folders are dropped and their children
    # are reparented to the workspace root (hierarchy flattens).
    now_ms = int(datetime.now().timestamp() * 1000)
    seq = int.from_bytes(os.urandom(4), 'big')
    return {
        "entries": [{
            "url": "about:blank",
            "triggeringPrincipal_base64": '{"3":{}}',
        }],
        "lastAccessed": now_ms,
        "pinned": True,
        "hidden": False,
        "groupId": group_id,
        "zenWorkspace": workspace_uuid,
        "zenSyncId": f"arc2zen-ph-{seq}",
        "zenEssential": False,
        "zenDefaultUserContextId": None,
        "zenPinnedIcon": None,
        "zenIsEmpty": True,
        "zenHasStaticIcon": False,
        "zenGlanceId": None,
        "zenIsGlance": False,
        "searchMode": None,
        "userContextId": 0,
        "attributes": {},
        "index": 1,
    }


# ---------------------------------------------------------------------------
# Main injection logic
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Inject Arc tabs into Zen's session file as real browser tabs",
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview what would be injected without writing')
    args = parser.parse_args()

    print("=" * 60)
    print("Arc → Zen Session Tab Injector")
    print("=" * 60)

    # 1. Find Zen profile
    profile = find_zen_profile()
    print(f"Zen profile: {profile}")

    # 2. Get workspace mappings from DB (created by migrate_arc_to_zen.py)
    ws_map = get_workspace_map(profile)
    if not ws_map:
        print("ERROR: No workspaces found in Zen DB.")
        print("Run migrate_arc_to_zen.py first to create workspaces.")
        return False

    print(f"\nWorkspaces ({len(ws_map)}):")
    for name, info in ws_map.items():
        print(f"  {name}: uuid={info['uuid'][:16]}... cid={info['container_id']}")

    # 3. Extract all Arc tabs using the pinned tab extractor
    print("\nExtracting Arc tabs...")
    extractor = ArcPinnedTabExtractor()
    arc_spaces = extractor.extract_pinned_tabs()

    for s in arc_spaces:
        ess = sum(1 for t in s.pinned_tabs if t.is_essential)
        reg = sum(1 for t in s.pinned_tabs if not t.is_essential)
        print(f"  {s.space_name}: {ess} essential + {reg} pinned + "
              f"{len(s.open_tabs)} open + {len(s.folders)} folders")

    total_pinned = sum(len(s.pinned_tabs) for s in arc_spaces)
    total_open = sum(len(s.open_tabs) for s in arc_spaces)
    print(f"  TOTAL: {total_pinned} pinned (incl essential) + "
          f"{total_open} open = {total_pinned + total_open} tabs")

    # 4. Read current session file
    sess_file = os.path.join(profile, 'zen-sessions.jsonlz4')
    if not os.path.exists(sess_file):
        print("ERROR: zen-sessions.jsonlz4 not found!")
        print("Open Zen browser once, close it, then re-run this script.")
        return False

    session = read_mozlz4(sess_file)
    old_tabs = session.get('tabs', [])
    print(f"\nCurrent session tabs: {len(old_tabs)}")

    # 5. Separate native Zen tabs from previously-injected Arc tabs.
    #    Arc-migrated workspaces have known UUIDs; tabs in those workspaces
    #    are replaced. Tabs in non-Arc workspaces (e.g. default) are kept.
    arc_ws_uuids = set()
    for name, info in ws_map.items():
        if any(s.space_name == name for s in arc_spaces):
            arc_ws_uuids.add(info['uuid'])

    kept_tabs = []
    removed = 0
    for tab in old_tabs:
        if tab.get('zenWorkspace', '') in arc_ws_uuids:
            removed += 1
        else:
            kept_tabs.append(tab)

    # Also clean out old folders/groups that belong to Arc workspaces
    old_folders = session.get('folders', [])
    old_groups = session.get('groups', [])
    kept_folders = [f for f in old_folders if f.get('workspaceId', '') not in arc_ws_uuids]
    kept_folder_ids = {f['id'] for f in kept_folders}
    kept_groups = [g for g in old_groups if g.get('id', '') in kept_folder_ids]

    print(f"Keeping {len(kept_tabs)} native Zen tabs, "
          f"replacing {removed} previously-injected tabs")

    # 6. Build fresh tab entries + folder entries from Arc data
    new_tabs = []
    new_folders = []
    new_groups = []
    seen_urls = set()

    for t in kept_tabs:
        for e in t.get('entries', []):
            seen_urls.add(e.get('url', ''))

    stats = {}
    total_folders_created = 0

    # Folder IDs must be globally unique across all workspaces. Compute the
    # millisecond stamp once and use a single monotonically incrementing
    # counter, otherwise spaces processed in the same millisecond collide
    # (the per-space counter reset to 0) and Zen hangs on session load.
    folder_id_stamp = int(datetime.now().timestamp() * 1000)
    folder_seq = 0

    for space in arc_spaces:
        ws_info = ws_map.get(space.space_name)
        if not ws_info:
            print(f"  WARNING: No workspace for '{space.space_name}', skipping")
            continue

        ws_uuid = ws_info['uuid']
        cid = ws_info['container_id']
        s = {'essential': 0, 'pinned': 0, 'open': 0, 'skipped': 0, 'folders': 0}

        # Map Arc folder UUIDs to the session folder IDs we mint for them.
        # Keying by Arc's stable folder_id (not folder title) avoids collisions
        # when siblings share a name (e.g. multiple "Bookmarks" folders).
        folder_id_map = {}
        for folder in space.folders:
            folder_id = f"{folder_id_stamp}-{folder_seq}"
            folder_seq += 1
            folder_id_map[folder.folder_id] = folder_id

            parent_folder_id = (
                folder_id_map.get(folder.parent_id) if folder.parent_id else None
            )

            new_folders.append(make_session_folder(
                folder_id, folder.title, ws_uuid,
                pinned=True, parent_id=parent_folder_id,
            ))
            new_groups.append(make_session_group(
                folder_id, folder.title, pinned=True,
            ))
            s['folders'] += 1

        total_folders_created += s['folders']

        # Essential + pinned tabs (with folder assignment)
        for tab in space.pinned_tabs:
            if tab.url in seen_urls:
                s['skipped'] += 1
                continue
            seen_urls.add(tab.url)

            # Resolve the tab's folder via Arc's parent_id UUID. Using
            # folder_path titles previously broke when siblings shared a name.
            group_id = folder_id_map.get(tab.parent_id) if tab.parent_id else None

            new_tabs.append(make_session_tab(
                url=tab.url, title=tab.title,
                workspace_uuid=ws_uuid, container_id=cid,
                pinned=True, essential=tab.is_essential,
                group_id=group_id,
            ))
            s['essential' if tab.is_essential else 'pinned'] += 1

        # Open (unpinned) tabs
        for tab in space.open_tabs:
            if tab.url in seen_urls:
                s['skipped'] += 1
                continue
            seen_urls.add(tab.url)
            new_tabs.append(make_session_tab(
                url=tab.url, title=tab.title,
                workspace_uuid=ws_uuid, container_id=cid,
                pinned=False, essential=False,
            ))
            s['open'] += 1

        stats[space.space_name] = s

    # Add an empty placeholder tab to every folder that received no direct
    # tabs. Zen drops folders with no direct tabs on session load, which
    # orphans their children and flattens the hierarchy.
    direct_tab_count = {}
    for tab in new_tabs:
        gid = tab.get('groupId')
        if gid:
            direct_tab_count[gid] = direct_tab_count.get(gid, 0) + 1

    placeholders_added = 0
    for folder in new_folders:
        if direct_tab_count.get(folder['id'], 0) == 0:
            new_tabs.append(make_session_placeholder_tab(
                workspace_uuid=folder['workspaceId'],
                group_id=folder['id'],
            ))
            placeholders_added += 1

    # 7. Summary
    print(f"\nInjection summary:")
    print(f"  Native Zen tabs kept: {len(kept_tabs)}")
    print(f"  New Arc tabs to inject: {len(new_tabs)}")
    print(f"  Folders to create: {total_folders_created}")
    print(f"  Placeholder tabs for empty folders: {placeholders_added}")
    print(f"  Total session tabs: {len(kept_tabs) + len(new_tabs)}")

    print(f"\nPer-workspace breakdown:")
    for name, s in stats.items():
        print(f"  {name}: {s['essential']} essential + {s['pinned']} pinned + "
              f"{s['open']} open + {s['folders']} folders ({s['skipped']} deduped)")

    if args.dry_run:
        print(f"\nDRY RUN — no changes written.")
        return True

    # 8. Backup and write
    backup = sess_file + f'.backup.{int(datetime.now().timestamp())}'
    shutil.copy2(sess_file, backup)
    print(f"\nBacked up session file")

    session['tabs'] = kept_tabs + new_tabs
    session['folders'] = kept_folders + new_folders
    session['groups'] = kept_groups + new_groups
    write_mozlz4(sess_file, session)
    print(f"SUCCESS: Wrote {len(session['tabs'])} tabs + "
          f"{len(new_folders)} folders to zen-sessions.jsonlz4")
    print(f"Close & reopen Zen browser to see your tabs.")

    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
