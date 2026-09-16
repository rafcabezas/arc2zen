#!/usr/bin/env python3
# Arc -> Zen Open (unpinned) Tab Injector
#
# Injects Arc's OPEN (non-pinned) tabs into Zen so they are restored as real
# tabs in the correct workspace after a restart.
#
# KEY INSIGHT (verified against a live Zen 1.20 profile):
#   Zen keeps TWO session files in lockstep, matched by `zenSyncId`:
#     1. zen-sessions.jsonlz4               - Zen's workspace tab store (flat list)
#     2. sessionstore.jsonlz4 / recovery    - Firefox per-window session
#   On restore Zen takes the INTERSECTION: a tab survives only if its zenSyncId
#   exists in BOTH files. Earlier attempts failed because they wrote to only one
#   file (inject_session_tabs.py -> zen-sessions only; the first inject_open_tabs
#   -> sessionstore only), so Zen pruned the orphans. This version writes the
#   SAME tab object (same zenSyncId) into BOTH files.
#
# Zen MUST be fully quit before running without --dry-run.
# Requires: pip install lz4

import argparse
import json
import struct
import os
import sys
import uuid
import shutil
import subprocess
from collections import Counter
from datetime import datetime

import lz4.block

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from arc_pinned_tab_extractor import ArcPinnedTabExtractor

# Serialized nsIReferrerInfo with no referrer + default policy (copied from a
# real Zen-restored tab). Safe generic value for injected tabs.
NO_REFERRER = "BBoSnxDOS9qmDeAnom1e0AAAAAAAAAAAwAAAAAAAAEYAAAAAAAAAAAABAQAAAAABAA=="


# ---------------------------------------------------------------------------
# Mozilla LZ4 file I/O
# ---------------------------------------------------------------------------

def read_mozlz4(path):
    with open(path, 'rb') as f:
        if f.read(8) != b'mozLz40\0':
            raise ValueError(f"Not a Mozilla LZ4 file: {path}")
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
# Environment
# ---------------------------------------------------------------------------

def is_zen_running():
    try:
        r = subprocess.run(['pgrep', '-f', '/Applications/Zen.app/Contents/MacOS'],
                           capture_output=True, text=True)
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def find_zen_profile():
    import glob
    base = os.path.expanduser('~/Library/Application Support/zen/Profiles')
    matches = glob.glob(os.path.join(base, '*', 'zen-sessions.jsonlz4'))
    if not matches:
        matches = glob.glob(os.path.join(base, '*', 'places.sqlite'))
    if not matches:
        raise FileNotFoundError(f"No Zen profile found in {base}")
    profiles = [os.path.dirname(m) for m in matches]
    return max(profiles, key=lambda p: os.path.getmtime(
        os.path.join(p, 'zen-sessions.jsonlz4')
        if os.path.isfile(os.path.join(p, 'zen-sessions.jsonlz4'))
        else os.path.join(p, 'places.sqlite')))


def load_sessionstore(profile):
    """Return (session_dict, path). Prefer clean-shutdown sessionstore.jsonlz4."""
    ss = os.path.join(profile, 'sessionstore.jsonlz4')
    rec = os.path.join(profile, 'sessionstore-backups', 'recovery.jsonlz4')
    if os.path.isfile(ss):
        return read_mozlz4(ss), ss
    if os.path.isfile(rec):
        return read_mozlz4(rec), rec
    raise FileNotFoundError("No sessionstore.jsonlz4 or recovery.jsonlz4 found")


def pick_window(session):
    wins = session.get('windows', [])
    if not wins:
        raise ValueError("Session has no windows")
    sel = session.get('selectedWindow')
    if isinstance(sel, int) and 1 <= sel <= len(wins):
        return sel - 1
    return max(range(len(wins)), key=lambda i: len(wins[i].get('tabs', [])))


def workspace_map(window):
    """name -> {uuid, container_id}. container_id = modal userContextId of the
    workspace's existing tabs (so injected tabs match their pinned siblings)."""
    name_to_uuid = {s.get('name'): s.get('uuid')
                    for s in window.get('spaces', []) if s.get('name') and s.get('uuid')}
    per_uuid = {}
    for t in window.get('tabs', []):
        ws = t.get('zenWorkspace')
        if ws:
            per_uuid.setdefault(ws, Counter())[t.get('userContextId', 0)] += 1
    out = {}
    for name, ws_uuid in name_to_uuid.items():
        cid = per_uuid.get(ws_uuid, Counter({0: 1})).most_common(1)[0][0]
        out[name] = {'uuid': ws_uuid, 'container_id': int(cid)}
    return out


# ---------------------------------------------------------------------------
# Tab construction (one object, written to BOTH files)
# ---------------------------------------------------------------------------

def make_tab(url, title, ws_uuid, container_id, sync_id):
    now_ms = int(datetime.now().timestamp() * 1000)
    return {
        "entries": [{
            "url": url,
            "title": title or url,
            "cacheKey": 0,
            "ID": int.from_bytes(os.urandom(4), 'big'),
            "docshellUUID": "{" + str(uuid.uuid4()) + "}",
            "referrerInfo": NO_REFERRER,
            "resultPrincipalURI": None,
            "hasUserInteraction": True,
            "triggeringPrincipal_base64": '{"3":{}}',
            "docIdentifier": int.from_bytes(os.urandom(4), 'big'),
            "transient": False,
            "navigationKey": "{" + str(uuid.uuid4()) + "}",
            "navigationId": "{" + str(uuid.uuid4()) + "}",
        }],
        "lastAccessed": now_ms,
        "hidden": False,
        "zenWorkspace": ws_uuid,
        "zenSyncId": sync_id,
        "zenEssential": False,
        "pinned": False,
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
        "userTypedValue": "",
        "userTypedClear": 0,
        "image": None,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Inject Arc open tabs into Zen (both session files)")
    ap.add_argument('--dry-run', action='store_true', help='Preview only; write nothing')
    args = ap.parse_args()

    print("=" * 60)
    print("Arc -> Zen Open Tab Injector (dual-file, zenSyncId-matched)")
    print("=" * 60)

    if is_zen_running() and not args.dry_run:
        print("\nERROR: Zen is running. Quit it completely (Cmd+Q) and re-run.")
        print("Zen rewrites both session files on quit and would discard the injection.")
        return False

    profile = find_zen_profile()
    print(f"Zen profile: {profile}")

    zs_path = os.path.join(profile, 'zen-sessions.jsonlz4')
    if not os.path.isfile(zs_path):
        print("ERROR: zen-sessions.jsonlz4 not found.")
        return False
    zen_sessions = read_mozlz4(zs_path)

    session, ss_source = load_sessionstore(profile)
    print(f"Sessionstore source: {os.path.basename(ss_source)}")

    wi = pick_window(session)
    window = session['windows'][wi]
    ws_map = workspace_map(window)
    if not ws_map:
        print("ERROR: no workspaces found in the session window.")
        return False
    print(f"Target window #{wi}: {len(window.get('tabs', []))} tabs, {len(ws_map)} workspaces")

    # Dedup against URLs already present in EITHER file
    seen = set()
    for t in window.get('tabs', []):
        for e in t.get('entries', []):
            if e.get('url'):
                seen.add(e['url'])
    for t in zen_sessions.get('tabs', []):
        for e in t.get('entries', []):
            if e.get('url'):
                seen.add(e['url'])

    spaces = ArcPinnedTabExtractor().extract_pinned_tabs()

    new_tabs = []
    counter = 0
    base_ms = int(datetime.now().timestamp() * 1000)
    stats = {}
    unmapped = []
    for space in spaces:
        if not space.open_tabs:
            continue
        info = ws_map.get(space.space_name)
        if not info:
            unmapped.append((space.space_name, len(space.open_tabs)))
            continue
        added = skipped = 0
        for tab in space.open_tabs:
            if not tab.url or tab.url in seen:
                skipped += 1
                continue
            seen.add(tab.url)
            counter += 1
            sync_id = f"{base_ms}-{counter}"
            new_tabs.append(make_tab(tab.url, tab.title, info['uuid'],
                                     info['container_id'], sync_id))
            added += 1
        stats[space.space_name] = (added, skipped)

    print("\nPer-workspace:")
    for name, (added, skipped) in stats.items():
        print(f"  {name:12s} +{added} new ({skipped} already present)")
    for name, n in unmapped:
        print(f"  {name:12s} SKIPPED - no matching Zen workspace ({n} tabs)")
    print(f"\nTotal to inject into BOTH files: {len(new_tabs)}")

    if not new_tabs:
        print("Nothing to inject.")
        return True

    if args.dry_run:
        print("\nDRY RUN - no files written. Sample:")
        for t in new_tabs[:8]:
            e = t['entries'][0]
            print(f"  [{t['zenWorkspace'][:10]}..] {(e['title'] or '')[:48]:48s} {e['url'][:55]}")
        return True

    # Append the SAME objects to both stores.
    zen_sessions['tabs'] = zen_sessions.get('tabs', []) + new_tabs
    window['tabs'] = window.get('tabs', []) + new_tabs

    stamp = int(datetime.now().timestamp())
    targets = [(zs_path, zen_sessions)]
    ss_file = os.path.join(profile, 'sessionstore.jsonlz4')
    rec_file = os.path.join(profile, 'sessionstore-backups', 'recovery.jsonlz4')
    # write the modified session to whichever sessionstore files exist
    targets.append((ss_file, session))
    if os.path.isfile(rec_file):
        targets.append((rec_file, session))

    for path, data in targets:
        if os.path.isfile(path):
            shutil.copy2(path, f"{path}.backup.{stamp}")
        write_mozlz4(path, data)
        print(f"Wrote {os.path.relpath(path, profile)}")

    print(f"\nSUCCESS. Injected {len(new_tabs)} open tabs into zen-sessions + sessionstore.")
    print("Launch Zen - open tabs will appear under their pinned tabs in each workspace.")
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
