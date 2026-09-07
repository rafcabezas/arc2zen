#!/usr/bin/env python3
# Arc -> Zen Auto-Archived Tab Restorer
#
# Restores ONLY the tabs Arc closed by itself (auto-archive timer):
# items in StorableArchiveItems.json with reason == "auto". Tabs the user
# closed manually (reason == "manual", e.g. Cmd+W) are deliberately ignored.
#
# Each tab is injected as a real open tab into its ORIGINAL workspace
# (resolved via the archive item's source.space UUID) using the same
# dual-file zenSyncId-matched mechanism as inject_open_tabs.py:
# the tab object must exist in BOTH zen-sessions.jsonlz4 and
# sessionstore.jsonlz4, matched by zenSyncId, or Zen prunes it.
#
# Zen MUST be fully quit before running without --dry-run.
# Requires: pip install lz4

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inject_open_tabs as iot

ARC_DIR = os.path.expanduser('~/Library/Application Support/Arc')
COCOA_EPOCH = 978307200  # 2001-01-01 00:00:00 UTC in unix seconds


def cocoa_to_unix_ms(cocoa_ts):
    return int((cocoa_ts + COCOA_EPOCH) * 1000)


def arc_space_names():
    """Arc space UUID -> space title, from StorableSidebar.json."""
    with open(os.path.join(ARC_DIR, 'StorableSidebar.json')) as f:
        data = json.load(f)
    out = {}
    for cont in data.get('sidebar', {}).get('containers', []):
        spaces = cont.get('spaces') or []
        it = iter(spaces)
        for el in it:
            if isinstance(el, str):
                obj = next(it, None)
                if isinstance(obj, dict) and obj.get('title'):
                    out[el] = obj['title']
    return out


def auto_archived_tabs():
    """All archive items with reason == 'auto' that carry a URL."""
    with open(os.path.join(ARC_DIR, 'StorableArchiveItems.json')) as f:
        data = json.load(f)
    tabs = []
    for item in data.get('items', []):
        if not isinstance(item, dict) or item.get('reason') != 'auto':
            continue
        tab = ((item.get('sidebarItem') or {}).get('data') or {}).get('tab') or {}
        url = tab.get('savedURL')
        if not url:
            continue
        source = item.get('source') or {}
        space = source.get('space')
        space_uuid = space.get('_0') if isinstance(space, dict) else None
        tabs.append({
            'url': url,
            'title': tab.get('savedTitle') or url,
            'space_uuid': space_uuid,
            'last_active': tab.get('timeLastActiveAt') or item.get('archivedAt') or 0,
        })
    tabs.sort(key=lambda t: t['last_active'])
    return tabs


def main():
    ap = argparse.ArgumentParser(
        description="Restore Arc auto-archived (reason=auto) tabs into Zen as open tabs")
    ap.add_argument('--dry-run', action='store_true', help='Preview only; write nothing')
    args = ap.parse_args()

    print("=" * 60)
    print("Arc -> Zen Auto-Archived Tab Restorer (reason=auto only)")
    print("=" * 60)

    if iot.is_zen_running() and not args.dry_run:
        print("\nERROR: Zen is running. Quit it completely (Cmd+Q) and re-run.")
        return False

    tabs = auto_archived_tabs()
    space_names = arc_space_names()
    print(f"Auto-archived tabs in Arc archive: {len(tabs)}")

    profile = iot.find_zen_profile()
    print(f"Zen profile: {profile}")

    zs_path = os.path.join(profile, 'zen-sessions.jsonlz4')
    zen_sessions = iot.read_mozlz4(zs_path)
    session, ss_source = iot.load_sessionstore(profile)
    print(f"Sessionstore source: {os.path.basename(ss_source)}")

    wi = iot.pick_window(session)
    window = session['windows'][wi]
    ws_map = iot.workspace_map(window)
    print(f"Target window #{wi}: {len(window.get('tabs', []))} tabs, {len(ws_map)} workspaces")

    # Dedup against URLs already present in EITHER session file
    seen = set()
    for t in window.get('tabs', []):
        for e in t.get('entries', []):
            if e.get('url'):
                seen.add(e['url'])
    for t in zen_sessions.get('tabs', []):
        for e in t.get('entries', []):
            if e.get('url'):
                seen.add(e['url'])

    new_tabs = []
    plan = []   # (space label, status, title, url)
    counter = 0
    base_ms = int(datetime.now().timestamp() * 1000)
    for tab in tabs:
        space_name = space_names.get(tab['space_uuid'])
        label = space_name or f"?{(tab['space_uuid'] or 'no-space')[:8]}"
        if tab['url'] in seen:
            plan.append((label, 'skip: already in Zen', tab['title'], tab['url']))
            continue
        info = ws_map.get(space_name) if space_name else None
        if not info:
            plan.append((label, 'skip: no Zen workspace', tab['title'], tab['url']))
            continue
        seen.add(tab['url'])
        counter += 1
        obj = iot.make_tab(tab['url'], tab['title'], info['uuid'],
                           info['container_id'], f"{base_ms}-{counter}")
        obj['lastAccessed'] = cocoa_to_unix_ms(tab['last_active'])
        new_tabs.append(obj)
        when = datetime.fromtimestamp(tab['last_active'] + COCOA_EPOCH)
        plan.append((label, f"restore ({when:%Y-%m-%d})", tab['title'], tab['url']))

    print("\nPlan:")
    for label, status, title, url in plan:
        print(f"  [{label:9.9s}] {status:24s} {title[:44]:44s} {url[:52]}")
    print(f"\nTotal to inject into BOTH files: {len(new_tabs)}")

    if not new_tabs:
        print("Nothing to inject.")
        return True

    if args.dry_run:
        print("\nDRY RUN - no files written.")
        return True

    zen_sessions['tabs'] = zen_sessions.get('tabs', []) + new_tabs
    window['tabs'] = window.get('tabs', []) + new_tabs

    stamp = int(datetime.now().timestamp())
    targets = [(zs_path, zen_sessions),
               (os.path.join(profile, 'sessionstore.jsonlz4'), session)]
    rec_file = os.path.join(profile, 'sessionstore-backups', 'recovery.jsonlz4')
    if os.path.isfile(rec_file):
        targets.append((rec_file, session))

    for path, data in targets:
        if os.path.isfile(path):
            shutil.copy2(path, f"{path}.backup.{stamp}")
        iot.write_mozlz4(path, data)
        print(f"Wrote {os.path.relpath(path, profile)}")

    print(f"\nSUCCESS. Restored {len(new_tabs)} auto-archived tabs as open tabs.")
    print("Launch Zen - they will appear in their original workspaces.")
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
