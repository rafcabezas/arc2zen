#!/usr/bin/env python3
"""
Mirror Arc's sidebar into Zen (built and checked against Zen 1.22).

A one-time migration for people leaving Arc. For every Arc space it creates
or reuses a Zen workspace and writes, in Arc's exact order:
  * pinned tabs and folders: nested folders, empty folders, Arc's custom tab
    names and the favicons from Arc's cache
  * open (unpinned) tabs, unless --no-open-tabs
  * Arc Favorites as Zen Essentials. Each Arc profile gets one Zen container
    (the default profile keeps its matched workspace's container, or none),
    so spaces that shared an Arc profile share logins and essentials in Zen
  * the space's emoji (or its named icon, when Zen ships one by that name),
    and its color for workspaces that don't have one yet
Optionally (--extensions) it installs the Firefox versions of Arc extensions.

Safety:
  * Dry run by default. --apply writes, and Zen must be quit for that.
  * Only tabs and folders this tool created (ids of the form arc2zen-<32 hex>
    and arc2zen-f-<32 hex>) are replaced or removed on later runs. Your own
    Zen tabs, folders, essentials and containers are kept. --replace-existing
    drops pre-existing ones, and only on the first sync into a profile. If
    they look like an earlier migration of the same Arc items, the first sync
    stops until you choose --replace-existing or --keep-existing.
  * Every file is backed up first and written atomically.
  * <profile>/arc2zen-sync.json records what was written. A later run refuses
    to touch synced items you changed in Zen (closed, moved, renamed,
    reordered, split, or your own pinned tabs or essentials added in a synced
    workspace or container), or to move a synced space to another workspace,
    unless you pass --overwrite-zen-changes.

Usage:
  python3 sync_arc_to_zen.py                  # dry run: print the plan
  python3 sync_arc_to_zen.py --apply          # write (quit Zen first)
  python3 sync_arc_to_zen.py --dump-arc       # print the Arc tree and exit
  python3 sync_arc_to_zen.py --map "Arc Space=Zen Workspace,A=B"
  python3 sync_arc_to_zen.py --extensions     # also install Firefox add-ons

Requires:  pip install lz4
"""

from __future__ import annotations

import argparse
import base64
import configparser
import copy
import errno
import glob
import hashlib
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter, OrderedDict
from datetime import datetime

HOME = os.path.expanduser("~")
DEFAULT_ARC_DIR = os.path.join(HOME, "Library", "Application Support", "Arc")
DEFAULT_ZEN_DIR = os.path.join(HOME, "Library", "Application Support", "zen")

SESSION_FILE = "zen-sessions.jsonlz4"
CONTAINERS_FILE = "containers.json"
STATE_FILE = "arc2zen-sync.json"
STATE_VERSION = 1

# Everything this tool writes gets a stable id derived from the Arc item id,
# so re-runs recognise (and only ever replace) their own items. Zen treats
# these ids as opaque strings: tabs use zenSyncId as their DOM id, and
# Firefox's only parser of group ids (ext-browser.js) falls back to a lookup
# table for ids that are not "<ms>-<n>".
TAB_PREFIX = "arc2zen-"
FOLDER_PREFIX = "arc2zen-f-"
# The full id shapes: inject_session_tabs.py wrote "arc2zen-ph-<n>" placeholder
# tabs, which belong to its own folders, not to this tool
OWNED_TAB_RE = re.compile(r"arc2zen-[0-9a-f]{32}\Z")
OWNED_FOLDER_RE = re.compile(r"arc2zen-f-[0-9a-f]{32}\Z")
ID_NAMESPACE = uuid.UUID("c6b1f0d2-3f4e-4b7a-9a61-7d2e5f8a0c93")

MOZLZ4_MAGIC = b"mozLz40\0"
SYSTEM_PRINCIPAL = '{"3":{}}'
ZEN_ICON_URL = "chrome://browser/skin/zen-icons/selectable/{}.svg"
WEB_SCHEMES = ("http", "https", "file")
DEFAULT_PROFILE = "Default"
MAX_USER_CONTEXT_ID = 4294967295

# Firefox container colors and icons, cycled for new containers
CONTAINER_COLORS = ["blue", "turquoise", "green", "yellow", "orange",
                    "red", "pink", "purple"]
CONTAINER_ICONS = ["fingerprint", "briefcase", "dollar", "cart", "circle",
                   "gift", "vacation", "food", "fruit", "pet", "tree",
                   "chill", "fence"]
# Firefox's built-in containers have an l10nId instead of a name
BUILTIN_CONTAINER_NAMES = {
    "user-context-personal": "Personal",
    "user-context-work": "Work",
    "user-context-banking": "Banking",
    "user-context-shopping": "Shopping",
}

# Chrome extension id -> addons.mozilla.org slug, for extensions whose developer
# publishes the same add-on for Firefox. Each id was taken from the Chrome Web
# Store or from a real Chrome/Arc install (some are no longer listed there),
# and each slug and its guid were checked on AMO. Add your own; anything not
# listed is reported by --extensions so you can look it up on AMO.
EXTENSION_MAP = {
    "aeblfdkhhhdcdjpifhhbdiojplfjncoa": "1password-x-password-manager",
    "jdocbkpgdakpekjlhemmfcncgdjeiika": "absolute-enable-right-click",
    "nngceckbapebfimnlniiiahkandclblb": "bitwarden-password-manager",
    "eimadpbcbfnmbkopoojfekhnkhdbieeh": "darkreader",
    "npkpplmpfeaeemeecniaikpjjfbfefhh": "docs_hotkey",
    "kbfnbcaeplbcioakkpcpgfkobkghlhen": "grammarly-1",
    "igfbafcgnaemgipnngnppdhilhpmmnoh": "keysmith",
    "hdokiejnpimakedhajhdlcegeplioahd": "lastpass-password-manager",
    "fakegmdomhmegokfomgmkbopjibonfcp": "metastream-remote",
    "oocalimimngaihdkbihfgmpkcpnmlaoa": "netflix-party-is-now-teleparty",
    "pkehgijcmpdhfbdbbnkijodmdjhbjlgp": "privacy-badger17",
    "fmkadmapgofadopljbjfkapdkoienihi": "react-devtools",
    "gebbhagfogifgggkldgodflihgfeippi": "return-youtube-dislikes",
    "ggmbioikhaicmnggmekhdhljdamngpam": "save-as-gif",
    "mnjggcdmjocbbbhaepdhchncahnbgone": "sponsorblock",
    "ghnomdcacenbmilgjigehppbamfndblo": "the-camelizer-price-history-ch",
    "cjpalhdlnbpafiamejdnhcphjbkeiagm": "ublock-origin",
    "ddkjiahejlhfcafbddmgiahcphecmpfh": "ublock-origin-lite",
    "nhdogjmejiglipccpnnnanhbledajbpd": "vue-js-devtools",
    "gppongmhjkpfnbhagpmjfkannfbllamg": "wappalyzer",
}
# Chrome extensions that need no add-on in Zen, or have no Firefox version:
# --extensions lists them with this reason instead of as unknown
EXTENSION_NOTES = {
    # JSON Formatter, Unicode Domain Warning, Microsoft Single Sign On
    "bcjindcccaagfpapjjmafapmmgkkhgoa": "Firefox has a JSON viewer built in",
    "khijgpndoffkmanglickjbnkbjimcdhn": "Firefox shows look-alike Unicode domains as punycode",
    "ppnbnpeolgkicgegkbkbjmhlideopiji": "Firefox has Microsoft Entra SSO built in "
                                        "(network.http.microsoft-entra-sso.enabled)",
    # Google Docs Offline, Chrome Remote Desktop
    "ghbmnnjooekpmoecnnnilnnbdlolhkhi": "Chrome only",
    "inomeogfingihgjfjlpeplalcfajhgai": "Chrome only",
}
AMO_API = "https://addons.mozilla.org/api/v5/addons/addon/{}/"
USER_AGENT = "arc2zen-sync"


class SyncError(Exception):
    """A problem to report to the user as a friendly message (exit code 1)."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def log(message: str = "") -> None:
    print(message, flush=True)


def norm(s) -> str:
    return (s or "").strip().lower() if isinstance(s, str) else ""


def text_or_none(value):
    """value if it is a non-empty string, else None (Arc data is loosely typed)."""
    return value if isinstance(value, str) and value else None


def arc_dir() -> str:
    return os.environ.get("ARC2ZEN_ARC_DIR") or DEFAULT_ARC_DIR


def zen_dir() -> str:
    return os.environ.get("ARC2ZEN_ZEN_DIR") or DEFAULT_ZEN_DIR


def tab_sync_id(arc_item_id: str, kind: str = "tab") -> str:
    return TAB_PREFIX + uuid.uuid5(ID_NAMESPACE, f"{kind}:{arc_item_id}").hex


def folder_sync_id(arc_item_id: str) -> str:
    return FOLDER_PREFIX + uuid.uuid5(ID_NAMESPACE, f"folder:{arc_item_id}").hex


def space_uuid(arc_space_id: str) -> str:
    return "{" + str(uuid.uuid5(ID_NAMESPACE, f"space:{arc_space_id}")) + "}"


def is_owned_tab(tab_id) -> bool:
    return isinstance(tab_id, str) and OWNED_TAB_RE.match(tab_id) is not None


def is_owned_folder(folder_id) -> bool:
    return isinstance(folder_id, str) and OWNED_FOLDER_RE.match(folder_id) is not None


def pgrep(pattern: str, exact: bool = False) -> list[int]:
    """Pids whose command line matches (or whose name equals) pattern."""
    cmd = ["pgrep", "-x" if exact else "-f", pattern]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return [int(p) for p in out.stdout.split() if p.isdigit() and int(p) != os.getpid()]


# --------------------------------------------------------------------------- #
# Files: mozLz4 codec, atomic writes, backups
# --------------------------------------------------------------------------- #
def _lz4_block():
    try:
        import lz4.block  # type: ignore
    except ImportError:
        raise SyncError("Missing dependency 'lz4'. Install it with:  pip install lz4")
    return lz4.block


def mozlz4_decode(raw: bytes, name: str = "file") -> dict:
    if raw[:8] != MOZLZ4_MAGIC or len(raw) < 12:
        raise SyncError(f"{name} is not a mozLz4 file (bad magic).")
    size = struct.unpack("<I", raw[8:12])[0]
    try:
        data = _lz4_block().decompress(raw[12:], uncompressed_size=size)
        return json.loads(data)
    except SyncError:
        raise
    except Exception as e:
        raise SyncError(f"{name} could not be decoded ({e}).")


def mozlz4_encode(obj) -> bytes:
    data = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return (MOZLZ4_MAGIC + struct.pack("<I", len(data))
            + _lz4_block().compress(data, store_size=False))


def atomic_write(path: str, data: bytes) -> None:
    """Write via a temp file in the same directory, fsync, then rename."""
    tmp = f"{path}.arc2zen-tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    try:
        dir_fd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def backup_file(path: str, stamp: str) -> str | None:
    """Copy path to <path>.arc2zen-bak-<stamp>, never overwriting a backup."""
    if not os.path.exists(path):
        return None
    dest = f"{path}.arc2zen-bak-{stamp}"
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = f"{path}.arc2zen-bak-{stamp}-{n}"
    shutil.copy2(path, dest)
    return dest


def read_json(path: str, what: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise SyncError(f"Could not read {what} ({path}): {e}")


# --------------------------------------------------------------------------- #
# Arc
# --------------------------------------------------------------------------- #
def arc_profile_key(profile) -> str:
    """Arc stores the profile as {"default": true} or
    {"custom": {"_0": {"directoryBasename": "Profile 1", ...}}}."""
    if not isinstance(profile, dict) or "default" in profile or not profile:
        return DEFAULT_PROFILE
    custom = profile.get("custom")
    inner = custom.get("_0") if isinstance(custom, dict) else None
    if isinstance(inner, dict):
        base = inner.get("directoryBasename")
        if isinstance(base, str) and base:
            return base
    return "custom:" + json.dumps(profile, sort_keys=True)[:60]


def _objects_with_id(flat) -> list[dict]:
    # Arc stores maps as [key, value, key, value, ...]; keep the values
    return [v for v in flat or [] if isinstance(v, dict) and isinstance(v.get("id"), str)]


def _pairs(flat) -> list[tuple]:
    out = []
    flat = flat or []
    for i in range(len(flat) - 1):
        if isinstance(flat[i], dict) and isinstance(flat[i + 1], str):
            out.append((flat[i], flat[i + 1]))
    return out


def flatten(nodes):
    for node in nodes:
        if node["kind"] == "tab":
            yield node
        else:
            yield from flatten(node["children"])


def count_nodes(nodes) -> tuple[int, int]:
    tabs = folders = 0
    for node in nodes:
        if node["kind"] == "tab":
            tabs += 1
        else:
            folders += 1
            t, f = count_nodes(node["children"])
            tabs, folders = tabs + t, folders + f
    return tabs, folders


class ArcSpace:
    def __init__(self, raw: dict, index: int):
        self.id = raw["id"]
        self.name = (text_or_none(raw.get("title")) or "").strip() or f"Arc Space {index}"
        self.profile = arc_profile_key(raw.get("profile"))
        self.raw = raw
        self.pinned: list = []
        self.open: list = []
        info = raw.get("customInfo") if isinstance(raw.get("customInfo"), dict) else {}
        icon = info.get("iconType") if isinstance(info.get("iconType"), dict) else {}
        self.emoji = icon.get("emoji_v2") if isinstance(icon.get("emoji_v2"), str) else None
        self.icon_name = icon.get("icon") if isinstance(icon.get("icon"), str) else None
        self.color = None
        try:
            mid = info["windowTheme"]["primaryColorPalette"]["midTone"]
            self.color = [max(0, min(255, round(float(mid[c]) * 255)))
                          for c in ("red", "green", "blue")]
        except (KeyError, TypeError, ValueError):
            pass


class ArcData:
    """Arc's sidebar (StorableSidebar.json) as trees of tab/folder nodes.

    A tab node is {"kind": "tab", "id", "url", "title", "label", "icon"}, a
    folder node {"kind": "folder", "id", "name", "children"}; lists are in
    Arc's display order (each container's childrenIds).
    """

    def __init__(self, path: str):
        self.path = path
        sidebar_file = os.path.join(path, "StorableSidebar.json")
        if not os.path.isfile(sidebar_file):
            raise SyncError(f"Arc data not found: {sidebar_file}. "
                            "Is Arc installed, or pass ARC2ZEN_ARC_DIR?")
        root = self._load(sidebar_file)
        main = self._sidebar_container(root)
        self.items = {v["id"]: v for v in _objects_with_id(main.get("items"))}
        self.skipped_kinds: Counter = Counter()
        self.skipped_schemes: Counter = Counter()
        self.skipped_no_url = 0
        self.duplicates = 0
        self._seen: set = set()
        self.profile_names = self._profile_names()

        self.spaces: list[ArcSpace] = []
        for i, raw in enumerate(_objects_with_id(main.get("spaces")), 1):
            space = ArcSpace(raw, i)
            space.pinned = self._tree(self._container_children(raw, "pinned"))
            space.open = self._tree(self._container_children(raw, "unpinned"))
            self.spaces.append(space)

        fav_pairs = _pairs(main.get("topAppsContainerIDs"))
        if not fav_pairs:
            try:
                fav_pairs = _pairs(root["sidebarSyncState"]["container"]["value"]
                                   ["topAppsContainerIDs"])
            except (KeyError, TypeError):
                fav_pairs = []
        self.favorites: dict[str, list] = OrderedDict()
        for profile, cid in fav_pairs:
            key = arc_profile_key(profile)
            children = (self.items.get(cid) or {}).get("childrenIds") or []
            self.favorites.setdefault(key, []).extend(flatten(self._tree(children)))

    @staticmethod
    def _load(path: str) -> dict:
        # Arc rewrites this file while running; retry once on a partial read
        for attempt in (1, 2):
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError as e:
                if attempt == 2:
                    raise SyncError(f"Could not parse {path} ({e}). Quit Arc and retry.")
                time.sleep(1.0)
            except OSError as e:
                raise SyncError(f"Could not read {path}: {e}")
        return {}

    @staticmethod
    def _sidebar_container(root: dict) -> dict:
        sidebar = root.get("sidebar") if isinstance(root, dict) else None
        containers = sidebar.get("containers") if isinstance(sidebar, dict) else None
        candidates = [c for c in containers or [] if isinstance(c, dict)
                      and isinstance(c.get("spaces"), list) and isinstance(c.get("items"), list)]
        if not candidates:
            raise SyncError("StorableSidebar.json has no sidebar container with spaces. "
                            "Open Arc once so it saves its sidebar, then retry.")
        return max(candidates, key=lambda c: (len(_objects_with_id(c["spaces"])),
                                              len(c["items"])))

    def _profile_names(self) -> dict:
        local_state = os.path.join(self.path, "User Data", "Local State")
        try:
            with open(local_state, encoding="utf-8") as f:
                cache = json.load(f)["profile"]["info_cache"]
            return {k: v["name"] for k, v in cache.items()
                    if isinstance(v, dict) and isinstance(v.get("name"), str)}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def profile_label(self, key: str) -> str:
        name = self.profile_names.get(key)
        return f"'{name}' ({key})" if name else key

    def _container_children(self, space: dict, marker: str) -> list:
        # The id right after the 'pinned'/'unpinned' marker holds display order
        ids = space.get("containerIDs")
        ids = ids if isinstance(ids, list) else []
        for i, cid in enumerate(ids[:-1]):
            if cid == marker:
                return (self.items.get(ids[i + 1]) or {}).get("childrenIds") or []
        return []

    def favicon(self, item_id: str) -> str | None:
        path = os.path.join(self.path, "SidebarItemsFaviconCache",
                            hashlib.md5(item_id.encode()).hexdigest())
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode() if data else None

    def _tree(self, child_ids) -> list:
        nodes = []
        for cid in child_ids or []:
            if not isinstance(cid, str):
                continue
            item = self.items.get(cid)
            if item is None:
                continue
            if cid in self._seen:
                self.duplicates += 1
                continue
            self._seen.add(cid)
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            children = item.get("childrenIds") or []
            if "list" in data:
                lst = data["list"] if isinstance(data["list"], dict) else {}
                name = (text_or_none(item.get("title")) or text_or_none(lst.get("title"))
                        or "").strip() or "Folder"
                nodes.append({"kind": "folder", "id": cid, "name": name,
                              "children": self._tree(children)})
            elif "tab" in data:
                tab = data["tab"] if isinstance(data["tab"], dict) else {}
                url = tab.get("savedURL")
                if isinstance(url, str) and url:
                    try:
                        scheme = urllib.parse.urlsplit(url).scheme.lower()
                    except ValueError:            # e.g. an unbalanced "[" in the host
                        scheme = "invalid URL"
                    if scheme in WEB_SCHEMES:
                        saved_title = text_or_none(tab.get("savedTitle"))
                        custom = text_or_none(item.get("title"))
                        nodes.append({
                            "kind": "tab", "id": cid, "url": url,
                            "title": saved_title or custom or url,
                            # zenStaticLabel only for a real Arc rename
                            "label": custom if custom and custom != saved_title else None,
                            "icon": self.favicon(cid),
                        })
                    else:
                        self.skipped_schemes[scheme or "?"] += 1
                else:
                    self.skipped_no_url += 1
                # Arc can nest tabs under a tab; Zen can't, so flatten in place
                nodes.extend(self._tree(children))
            else:
                kind = next(iter(data), "unknown")
                self.skipped_kinds[kind] += 1
                nodes.extend(self._tree(children))
        return nodes

    def skipped_summary(self) -> list[str]:
        out = []
        if self.skipped_schemes:
            detail = ", ".join(f"{k}: {v}" for k, v in sorted(self.skipped_schemes.items()))
            out.append(f"{sum(self.skipped_schemes.values())} non-web tab(s) skipped ({detail})")
        if self.skipped_kinds:
            detail = ", ".join(f"{k}: {v}" for k, v in sorted(self.skipped_kinds.items()))
            out.append(f"{sum(self.skipped_kinds.values())} Arc item(s) of other kinds "
                       f"flattened ({detail})")
        if self.skipped_no_url:
            out.append(f"{self.skipped_no_url} tab(s) without a URL skipped")
        if self.duplicates:
            out.append(f"{self.duplicates} item(s) listed twice in Arc used once")
        return out

    def extensions(self) -> dict:
        """Chrome extension id -> name, from every Arc profile (built-ins skipped)."""
        found: dict = {}
        pattern = os.path.join(self.path, "User Data", "*", "*Preferences")
        for prefs in sorted(glob.glob(pattern)):
            try:
                with open(prefs, encoding="utf-8") as f:
                    settings = json.load(f).get("extensions", {}).get("settings", {})
            except (OSError, ValueError, AttributeError):
                continue
            for ext_id, info in (settings or {}).items():
                info = info if isinstance(info, dict) else {}
                if info.get("location") in (5, 10):  # component extensions
                    continue
                name = (info.get("manifest") or {}).get("name")
                if not isinstance(name, str) or name.startswith("__MSG_"):
                    name = ext_id
                found.setdefault(ext_id, name)
        return found


def arc_running() -> bool:
    return bool(pgrep("Arc", exact=True))


# --------------------------------------------------------------------------- #
# Zen profile: discovery, running check, prefs, omni.ja
# --------------------------------------------------------------------------- #
def find_zen_profile(base: str) -> str:
    """The profile Zen launches: the [Install*] Default in profiles.ini."""
    ini_path = os.path.join(base, "profiles.ini")
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(ini_path, encoding="utf-8")
    except configparser.Error as e:
        raise SyncError(f"Could not parse {ini_path}: {e}")

    def resolve(rel: str) -> str:
        return os.path.normpath(rel if os.path.isabs(rel) else os.path.join(base, rel))

    candidates = []
    for section in ini.sections():
        if section.startswith("Install") and ini.get(section, "Default", fallback=None):
            candidates.append(resolve(ini.get(section, "Default")))
    if not candidates:
        for section in ini.sections():
            if section.startswith("Profile") and ini.get(section, "Default", fallback="0") == "1":
                candidates.append(resolve(ini.get(section, "Path", fallback="")))
    if not candidates:
        candidates = [os.path.dirname(p) for p in
                      glob.glob(os.path.join(base, "Profiles", "*", SESSION_FILE))]
    candidates = [c for c in dict.fromkeys(candidates) if os.path.isdir(c)]
    if not candidates:
        raise SyncError(f"No Zen profile found under {base}. Launch Zen once, quit it, "
                        "and re-run (or pass --profile).")
    if len(candidates) == 1:
        return candidates[0]

    def mtime(p):
        try:
            return os.path.getmtime(os.path.join(p, SESSION_FILE))
        except OSError:
            return 0.0
    best = max(candidates, key=mtime)
    log("Several Zen installs/profiles found; using the one saved most recently:")
    for c in candidates:
        log(f"  {'*' if c == best else ' '} {c}")
    log("  (pass --profile to pick another)")
    return best


def zen_profile_locked(profile: str) -> bool:
    """True if a running Zen holds <profile>/.parentlock (never writes to it)."""
    try:
        import fcntl
    except ImportError:
        return False
    path = os.path.join(profile, ".parentlock")
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        # Firefox takes a POSIX (fcntl) lock; on Linux flock does not see it
        layouts = {"darwin": "@qqihh", "linux": "@hhqqi"}
        fmt = layouts.get(sys.platform)
        if fmt:
            if sys.platform == "darwin":
                query = struct.pack(fmt, 0, 0, 0, fcntl.F_WRLCK, 0)
                l_type = struct.unpack(fmt, fcntl.fcntl(fd, fcntl.F_GETLK, query))[3]
            else:
                query = struct.pack(fmt, fcntl.F_WRLCK, 0, 0, 0, 0)
                l_type = struct.unpack(fmt, fcntl.fcntl(fd, fcntl.F_GETLK, query))[0]
            return l_type != fcntl.F_UNLCK
        return False
    except OSError:
        return False
    finally:
        os.close(fd)


def zen_process_ids() -> list[int]:
    return pgrep("MacOS/zen")


def check_zen_running(profile: str, apply: bool, force: bool) -> None:
    """Refuse --apply while Zen could overwrite what we write.

    A held .parentlock proves a running Zen has this very profile open; it
    rewrites zen-sessions.jsonlz4 from memory when it saves or quits, so that
    is always refused. A Zen process alone may be using another profile or
    install (say, when writing to a copy), so --force-running can skip that.
    """
    if zen_profile_locked(profile):
        text = "This Zen profile is open in a running Zen (its .parentlock is held)."
        if apply:
            raise SyncError(f"{text} Quit Zen (Cmd+Q) and re-run. --force-running doesn't "
                            "override this: Zen would overwrite the sync when it quits.")
        log(f"Warning: {text} Quit it before running with --apply.")
        return
    pids = zen_process_ids()
    if not pids:
        return
    text = "Zen appears to be running (pid " + ", ".join(map(str, pids)) + ")."
    if apply and not force:
        raise SyncError(f"{text} Quit Zen (Cmd+Q) and re-run, or pass --force-running if "
                        "you're sure it isn't using this profile (for example, when writing "
                        "to a copy of it).")
    log(f"Warning: {text} " + ("Continuing (--force-running); this profile isn't locked."
                               if apply else "Quit it before running with --apply."))


_PREF_RE = re.compile(r'^\s*user_pref\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*(.*?)\s*\)\s*;')


def read_prefs(profile: str) -> dict:
    """prefs.js then user.js (user.js wins, as at Zen startup)."""
    prefs = {}
    for name in ("prefs.js", "user.js"):
        try:
            with open(os.path.join(profile, name), encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            m = _PREF_RE.match(line)
            if not m:
                continue
            raw = m.group(2)
            try:
                value = json.loads(raw)
            except ValueError:
                value = raw
            prefs[m.group(1)] = value
    return prefs


def check_prefs(prefs: dict, profile: str, include_open: bool) -> tuple[list, list]:
    errors, warnings = [], []
    if prefs.get("zen.window-sync.enabled", True) is False:
        errors.append("zen.window-sync.enabled is false. Zen then ignores zen-sessions.jsonlz4 "
                      "and overwrites it on quit, so the sync would be lost. Set it back "
                      "to true in about:config (or remove it from user.js) and re-run.")
    if prefs.get("zen.workspaces.separate-essentials", True) is False:
        warnings.append(
            "zen.workspaces.separate-essentials is false, so the Essentials of every Arc "
            "profile will show in every workspace. To keep them per container, quit Zen "
            f"and add this line to {os.path.join(profile, 'user.js')}:\n"
            '      user_pref("zen.workspaces.separate-essentials", true);')
    if include_open:
        if prefs.get("zen.window-sync.sync-only-pinned-tabs", False) is True:
            warnings.append("zen.window-sync.sync-only-pinned-tabs is true, so Zen will not "
                            "restore the synced open tabs (use --no-open-tabs to skip them).")
        page = prefs.get("browser.startup.page", 3)
        if page != 3:
            warnings.append(f"browser.startup.page is {page}, so Zen will not restore the "
                            "synced open tabs. Turn on Settings > General > 'Open previous "
                            "windows and tabs', or use --no-open-tabs.")
    return errors, warnings


def zen_version(profile: str) -> str | None:
    """The Zen version that last opened the profile (from compatibility.ini), if known."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read(os.path.join(profile, "compatibility.ini"), encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return None
    return ini.get("Compatibility", "LastVersion", fallback="").split("_")[0] or None


def find_zen_omni() -> str | None:
    apps = []
    if os.environ.get("ARC2ZEN_ZEN_APP"):
        apps.append(os.environ["ARC2ZEN_ZEN_APP"])
    else:
        for root in ("/Applications", os.path.join(HOME, "Applications")):
            apps.append(os.path.join(root, "Zen.app"))
            apps.extend(sorted(glob.glob(os.path.join(root, "Zen*.app"))))
        for pid in zen_process_ids():
            try:
                exe = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            if ".app/" in exe:
                apps.append(exe.split(".app/")[0] + ".app")
    for app in dict.fromkeys(apps):
        omni = os.path.join(app, "Contents", "Resources", "browser", "omni.ja")
        if os.path.isfile(omni):
            return omni
    return None


class ZenIcons:
    """Which named SVG icons Zen ships (looked up in omni.ja)."""

    def __init__(self, omni: str | None):
        self.omni = omni
        self._data = None

    def has(self, name: str) -> bool:
        if not self.omni:
            return False
        if self._data is None:
            # omni.ja is an "optimized" jar that zipfile can't open, but entry
            # names are stored uncompressed, so a byte search is enough
            try:
                with open(self.omni, "rb") as f:
                    self._data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            except (OSError, ValueError):
                self._data = b""
        return self._data.find(f"zen-icons/selectable/{name}.svg".encode()) >= 0


def load_zen_profile(profile: str) -> tuple[dict, dict]:
    missing = [n for n in (SESSION_FILE, CONTAINERS_FILE)
               if not os.path.isfile(os.path.join(profile, n))]
    if missing:
        raise SyncError(f"{' and '.join(missing)} not found in {profile}. Launch Zen once, "
                        "quit it (Cmd+Q), and re-run.")
    with open(os.path.join(profile, SESSION_FILE), "rb") as f:
        session = mozlz4_decode(f.read(), SESSION_FILE)
    containers = read_json(os.path.join(profile, CONTAINERS_FILE), CONTAINERS_FILE)
    if not isinstance(session, dict) or not isinstance(containers, dict):
        raise SyncError("Unexpected Zen data format (not a JSON object).")
    if not isinstance(containers.get("identities"), list):
        raise SyncError(f"{CONTAINERS_FILE} has no identities list.")
    return session, containers


SECTION_KINDS = ("pinned", "open", "essentials")


def _state_problem(state) -> str | None:
    """Why a parsed arc2zen-sync.json can't be used, or None if it can."""
    if not isinstance(state, dict):
        return "not a JSON object"
    version = state.get("version", 0)
    if not isinstance(version, int) or isinstance(version, bool):
        return "'version' is not a number"
    if version > STATE_VERSION:
        return None                       # reported separately
    if not isinstance(state.get("layout"), dict):
        return "no 'layout'"
    for sec, entries in state["layout"].items():
        kind, _, key = sec.partition(" ")
        if kind not in SECTION_KINDS or not key:
            return f"unknown layout section {sec!r}"
        if not isinstance(entries, list) or not all(
                isinstance(e, dict) and isinstance(e.get("id"), str)
                and e.get("kind") in ("tab", "folder") for e in entries):
            return f"bad entries in layout section {sec!r}"
    for name, value_type in (("spaces", str), ("workspaces", dict), ("arc_profiles", int)):
        value = state.get(name, {})
        if not isinstance(value, dict) or not all(
                isinstance(v, value_type) and not isinstance(v, bool) for v in value.values()):
            return f"bad '{name}'"
    if not isinstance(state.get("created_containers", []), list):
        return "bad 'created_containers'"
    return None


def load_state(profile: str) -> dict | None:
    path = os.path.join(profile, STATE_FILE)
    if not os.path.exists(path):
        return None
    state = read_json(path, STATE_FILE)
    problem = _state_problem(state)
    if problem:
        raise SyncError(f"{path} is not a valid arc2zen state file ({problem}). Move it away "
                        "to treat the next run as a first sync.")
    if state.get("version", 0) > STATE_VERSION:
        raise SyncError(f"{path} was written by a newer version of this tool.")
    return state


# --------------------------------------------------------------------------- #
# Zen session objects (shapes match what Zen 1.22 itself saves)
# --------------------------------------------------------------------------- #
def make_tab(sync_id, url, title, ws_uuid, container, *, pinned=True, essential=False,
             group_id=None, label=None, icon=None, empty=False, now=0):
    tab = {
        "entries": [{
            "url": url,
            "title": title,
            "cacheKey": 0,
            "triggeringPrincipal_base64": SYSTEM_PRINCIPAL,
        }],
        "lastAccessed": now,
        "pinned": pinned or essential,
        "hidden": False,
        "zenWorkspace": ws_uuid,
        "zenSyncId": sync_id,
        "zenEssential": essential,
        "zenDefaultUserContextId": None,
        "zenPinnedIcon": None,
        "zenIsEmpty": empty,
        "zenHasStaticIcon": False,
        "zenGlanceId": None,
        "zenIsGlance": False,
        "zenLiveFolderItemId": None,
        "searchMode": None,
        "userContextId": container,
        "attributes": {},
        "index": 1,
        "userTypedValue": "",
        "userTypedClear": 0,
        "image": icon,
    }
    if pinned or essential:
        tab["_zenPinnedInitialState"] = {"entry": {"url": url, "title": title}, "image": icon}
    if label:
        tab["zenStaticLabel"] = label
    if group_id:
        tab["groupId"] = group_id
    return tab


def tab_url(tab: dict):
    """The pinned URL for pinned tabs, else the current entry's URL."""
    initial = tab.get("_zenPinnedInitialState")
    if tab.get("pinned") and isinstance(initial, dict):
        entry = initial.get("entry")
        if isinstance(entry, dict) and entry.get("url"):
            return entry["url"]
    entries = tab.get("entries") if isinstance(tab.get("entries"), list) else []
    if not entries:
        return None
    index = tab.get("index")
    i = index - 1 if isinstance(index, int) and 0 < index <= len(entries) else len(entries) - 1
    return entries[i].get("url") if isinstance(entries[i], dict) else None


class SessionBuilder:
    """Turns Arc trees into Zen tabs, folders and groups.

    Zen restores folders from runs of tabs sharing a groupId, each starting
    with an empty placeholder tab. Nested folders are then moved inside
    their parent, after the element named in prevSiblingInfo, so they are
    emitted in pre-order. Items written by an earlier run keep their Zen
    state (history, collapsed folders) when Arc did not change them.
    """

    def __init__(self, old_session: dict, now_ms: int):
        self.now = now_ms
        self.old_tabs = {t.get("zenSyncId"): t for t in old_session.get("tabs") or []
                         if isinstance(t, dict) and is_owned_tab(t.get("zenSyncId"))}
        self.old_folders = {f.get("id"): f for f in old_session.get("folders") or []
                            if isinstance(f, dict) and is_owned_folder(f.get("id"))}
        self.tabs: list = []
        self.folders: list = []
        self.groups: list = []

    def _emit(self, fresh: dict) -> str:
        old = self.old_tabs.get(fresh["zenSyncId"])
        self.tabs.append(self._merge(old, fresh) if old else fresh)
        return fresh["zenSyncId"]

    @staticmethod
    def _merge(old: dict, fresh: dict) -> dict:
        same_kind = (bool(old.get("pinned")) == fresh["pinned"]
                     and bool(old.get("zenEssential")) == fresh["zenEssential"]
                     and bool(old.get("zenIsEmpty")) == fresh["zenIsEmpty"])
        if not same_kind or (fresh["pinned"] and tab_url(old) != tab_url(fresh)):
            return fresh
        tab = copy.deepcopy(old)
        for key in ("pinned", "zenWorkspace", "zenEssential", "userContextId", "zenIsEmpty"):
            tab[key] = fresh[key]
        for key in ("groupId", "zenStaticLabel"):
            if key in fresh:
                tab[key] = fresh[key]
            else:
                tab.pop(key, None)
        if fresh["pinned"]:
            state = tab.get("_zenPinnedInitialState")
            state = dict(state) if isinstance(state, dict) else {}
            state["entry"] = dict(fresh["_zenPinnedInitialState"]["entry"])
            state["image"] = state.get("image") or fresh["_zenPinnedInitialState"]["image"]
            tab["_zenPinnedInitialState"] = state
        if not tab.get("image") and fresh.get("image"):
            tab["image"] = fresh["image"]
        return tab

    def add_essentials(self, nodes, container):
        # Native essentials carry no workspace; Zen places them by container
        for info in flatten(nodes):
            self._emit(make_tab(tab_sync_id(info["id"]), info["url"], info["title"], None,
                                container, essential=True, label=info["label"],
                                icon=info["icon"], now=self.now))

    def add_pinned(self, nodes, ws_uuid, container):
        prev = {"type": "start", "id": None}
        for node in nodes:
            if node["kind"] == "tab":
                prev = {"type": "tab", "id": self._tab(node, ws_uuid, container, None)}
            else:
                fid = folder_sync_id(node["id"])
                self._folder(node, fid, ws_uuid, container, None, prev)
                prev = {"type": "group", "id": fid}

    def add_open(self, nodes, ws_uuid, container):
        for info in flatten(nodes):
            self._emit(make_tab(tab_sync_id(info["id"]), info["url"], info["title"], ws_uuid,
                                container, pinned=False, icon=info["icon"], now=self.now))

    def _tab(self, info, ws_uuid, container, group_id) -> str:
        return self._emit(make_tab(tab_sync_id(info["id"]), info["url"], info["title"], ws_uuid,
                                   container, group_id=group_id, label=info["label"],
                                   icon=info["icon"], now=self.now))

    def _folder(self, node, folder_id, ws_uuid, container, parent_id, prev):
        placeholder = make_tab(tab_sync_id(node["id"], "placeholder"), "about:blank", None,
                               ws_uuid, 0, group_id=folder_id, empty=True, now=self.now)
        placeholder["entries"][0].pop("title")
        placeholder["_zenPinnedInitialState"]["entry"].pop("title")
        self._emit(placeholder)
        old = self.old_folders.get(folder_id) or {}
        collapsed = old.get("collapsed", True) if isinstance(old.get("collapsed"), bool) else True
        self.folders.append({
            "pinned": True,
            "essential": False,
            "splitViewGroup": False,
            "id": folder_id,
            "name": node["name"],
            "collapsed": collapsed,
            "saveOnWindowClose": True,
            "parentId": parent_id,
            "prevSiblingInfo": prev,
            "emptyTabIds": [placeholder["zenSyncId"]],
            "userIcon": old.get("userIcon") or "",
            "isLiveFolder": False,
            "workspaceId": ws_uuid,
        })
        self.groups.append({
            "pinned": True,
            "splitView": False,
            "id": folder_id,
            "name": node["name"],
            "color": "zen-workspace-color",
            "collapsed": collapsed,
            "saveOnWindowClose": True,
        })
        # Direct tabs first (keeps the group contiguous), then subfolders,
        # each told which sibling it follows inside this folder
        inner_prev = {"type": "start", "id": None}
        subfolders = []
        for child in node["children"]:
            if child["kind"] == "tab":
                inner_prev = {"type": "tab", "id": self._tab(child, ws_uuid, container, folder_id)}
            else:
                sub_id = folder_sync_id(child["id"])
                subfolders.append((child, sub_id, inner_prev))
                inner_prev = {"type": "group", "id": sub_id}
        for child, sub_id, child_prev in subfolders:
            self._folder(child, sub_id, ws_uuid, container, folder_id, child_prev)


# --------------------------------------------------------------------------- #
# What Zen will show: a model of Zen 1.22's restore
# --------------------------------------------------------------------------- #
class _Node:
    __slots__ = ("kind", "id", "data", "children", "parent")

    def __init__(self, kind, node_id, data):
        self.kind, self.id, self.data = kind, node_id, data
        self.children: list = []
        self.parent = None

    def insert(self, node, index=None):
        if node.parent is not None:
            node.parent.children.remove(node)
        node.parent = self
        if index is None:
            self.children.append(node)
        else:
            self.children.insert(index, node)

    def contains(self, node) -> bool:
        while node is not None:
            if node is self:
                return True
            node = node.parent
        return False


def section_key(kind: str, key) -> str:
    return f"{kind} {key}"


def render_layout(session: dict) -> dict:
    """Sidebar layout Zen will build from a session, as
    {"pinned <ws>" | "open <ws>" | "essentials <ctx>": [entry, ...]} with
    entries in pre-order. An entry is {id, kind: tab|folder, owned, workspace,
    parent (the enclosing folder, i.e. a tab's groupId), position (index
    under that parent), url, label (zenStaticLabel or folder name)}, plus
    split (the group id) for a tab in a split view.

    Follows Tabbrowser.createTabsForSessionRestore (a group is created where
    its first tab is; later tabs join it) and ZenFolders.
    restoreDataFromSessionStore (folders replace their group, then nest via
    parentId + prevSiblingInfo). Placeholder and glance tabs are left out.
    """
    groups = {g.get("id") for g in session.get("groups") or [] if isinstance(g, dict)}
    root = _Node("root", None, None)
    by_id, group_nodes = {}, {}
    for tab in session.get("tabs") or []:
        if not isinstance(tab, dict):
            continue
        node = _Node("tab", tab.get("zenSyncId"), tab)
        if node.id:
            by_id.setdefault(node.id, node)
        gid = tab.get("groupId")
        if gid and gid in groups:
            group = group_nodes.get(gid)
            if group is None:
                group = group_nodes[gid] = _Node("group", gid, None)
                root.insert(group)
                by_id.setdefault(gid, group)
            group.insert(node, 0 if tab.get("zenIsEmpty") else None)
        else:
            root.insert(node)

    folders = [f for f in session.get("folders") or [] if isinstance(f, dict) and f.get("id")]
    empty_ids = set()
    folder_nodes = {}
    for f in folders:
        if isinstance(f.get("emptyTabIds"), list):
            empty_ids.update(f["emptyTabIds"])
        group = group_nodes.get(f["id"])
        if group is None or f.get("splitViewGroup") or f["id"] in folder_nodes:
            continue
        node = _Node("folder", f["id"], f)
        parent = group.parent
        parent.insert(node, parent.children.index(group))
        for child in list(group.children):
            node.insert(child)
        parent.children.remove(group)
        folder_nodes[f["id"]] = node
        by_id[f["id"]] = node
    for f in folders:
        node, parent = folder_nodes.get(f["id"]), folder_nodes.get(f.get("parentId"))
        if node is None or parent is None or node.contains(parent):
            continue
        prev = f.get("prevSiblingInfo") if isinstance(f.get("prevSiblingInfo"), dict) else {}
        anchor = by_id.get(prev.get("id")) if prev.get("type") in ("tab", "group") else None
        if anchor is not None and not node.contains(anchor) and anchor.parent is not None:
            anchor.parent.insert(node, anchor.parent.children.index(anchor) + 1)
        elif parent.children and parent.children[0] is not node:
            first = parent.children[0]
            parent.insert(node, parent.children.index(first) + 1)
        else:
            parent.insert(node)

    def first_tab(node):
        if node.kind == "tab":
            return node.data
        for child in node.children:
            found = first_tab(child)
            if found:
                return found
        return None

    def section_of(node):
        if node.kind == "tab":
            tab = node.data
            if tab.get("zenEssential"):
                return section_key("essentials", tab.get("userContextId") or 0)
            return section_key("pinned" if tab.get("pinned") else "open", tab.get("zenWorkspace"))
        tab = first_tab(node) or {}
        if (node.data or {}).get("essential"):
            return section_key("essentials", tab.get("userContextId") or 0)
        ws = (node.data or {}).get("workspaceId") or tab.get("zenWorkspace")
        return section_key("pinned", ws)

    def expand(nodes):  # plain (non-folder) groups, e.g. split views, are transparent
        for node in nodes:
            if node.kind == "group":
                yield from expand(node.children)
            else:
                yield node

    sections: dict = OrderedDict()
    positions: Counter = Counter()

    def walk(node, parent_id, section, out):
        if node.kind == "tab" and (node.data.get("zenIsEmpty") or node.id in empty_ids
                                   or node.data.get("zenIsGlance")):
            return                        # placeholders and glance overlays aren't items
        pos = positions[(section, parent_id)]
        positions[(section, parent_id)] += 1
        ws = section.split(" ", 1)[1] if not section.startswith("essentials") else None
        if node.kind == "tab":
            label = node.data.get("zenStaticLabel")
            entry = {"id": node.id, "kind": "tab", "owned": is_owned_tab(node.id),
                     "workspace": ws, "parent": parent_id, "position": pos,
                     "url": tab_url(node.data),
                     "label": label if isinstance(label, str) else None}
            gid = node.data.get("groupId")
            if gid in group_nodes and gid not in folder_nodes:
                entry["split"] = gid          # a split view (or other non-folder group)
            out.append(entry)
        else:
            out.append({"id": node.id, "kind": "folder", "owned": is_owned_folder(node.id),
                        "workspace": ws, "parent": parent_id, "position": pos,
                        "url": None, "label": node.data.get("name")})
            for child in expand(node.children):
                walk(child, node.id, section, out)

    for top in expand(root.children):
        section = section_of(top)
        walk(top, None, section, sections.setdefault(section, []))
    return sections


# --------------------------------------------------------------------------- #
# Planning: Arc spaces -> Zen workspaces, Arc profiles -> Zen containers
# --------------------------------------------------------------------------- #
class SpacePlan:
    def __init__(self, arc_space: ArcSpace, zen: dict, how: str):
        self.arc = arc_space
        self.zen = zen            # the (copied) Zen space object that will be written
        self.how = how            # 'new', 'synced before', 'same name', '--map'
        self.old_container = None if how == "new" else (zen.get("containerTabId") or 0)
        self.old_icon = None if how == "new" else zen.get("icon")
        self.synced_icon = None   # the icon this sync last set (recorded in the state)
        self.container = 0

    @property
    def uuid(self) -> str:
        return self.zen["uuid"]


class Plan:
    def __init__(self):
        self.spaces: list[SpacePlan] = []
        self.profile_container: dict = OrderedDict()
        self.new_identities: list = []
        self.essentials: dict = OrderedDict()   # profile -> tab nodes
        self.skipped_spaces: list = []
        self.notes: list = []
        self.rebinds: list = []       # binding changes a re-run needs --overwrite-zen-changes for
        self.unknown_map: list = []   # --map names that match no Arc space
        self.gone: dict = OrderedDict()   # Arc space id -> workspace, space no longer in Arc


def parse_map(text) -> dict:
    """{'Arc Space' (as typed): 'Zen Workspace'}"""
    mapping = OrderedDict()
    for part in (text or "").split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise SyncError(f"--map entry '{part.strip()}' needs the form "
                            "'Arc Space=Zen Workspace'.")
        arc_name, zen_name = (s.strip() for s in part.split("=", 1))
        if not arc_name or not zen_name:
            raise SyncError(f"--map entry '{part.strip()}' has an empty name.")
        mapping[arc_name] = zen_name
    return mapping


def identity_name(identity: dict) -> str:
    return identity.get("name") or BUILTIN_CONTAINER_NAMES.get(identity.get("l10nId"), "")


def container_label(containers: dict, ctx) -> str:
    if not ctx:
        return "0 (no container)"
    for i in containers.get("identities") or []:
        if i.get("userContextId") == ctx:
            return f"{ctx} '{identity_name(i)}'"
    return f"{ctx} (missing)"


def new_identity(containers: dict, name_hint: str, n: int) -> dict:
    identities = containers["identities"]
    ids = [i.get("userContextId") for i in identities
           if isinstance(i.get("userContextId"), int) and i["userContextId"] < MAX_USER_CONTEXT_ID]
    last = containers.get("lastUserContextId")
    # Older arc2zen runs could leave lastUserContextId on the reserved internal id
    # (UINT32_MAX); ignore it there, as Firefox refuses to create a container past it
    if not isinstance(last, int) or last >= MAX_USER_CONTEXT_ID:
        last = 0
    next_id = max([last] + ids) + 1
    if next_id >= MAX_USER_CONTEXT_ID:
        raise SyncError("containers.json has no free container id left.")
    used ={norm(identity_name(i)) for i in identities}
    base = name_hint.strip() or "Arc"
    name, k = base, 1
    while norm(name) in used:
        name = f"{base} (Arc)" if k == 1 else f"{base} (Arc {k})"
        k += 1
    identity = {"userContextId": next_id, "public": True,
                "icon": CONTAINER_ICONS[n % len(CONTAINER_ICONS)],
                "color": CONTAINER_COLORS[n % len(CONTAINER_COLORS)], "name": name}
    identities.append(identity)
    containers["lastUserContextId"] = next_id
    return identity


def build_plan(arc: ArcData, session: dict, containers: dict, state: dict | None,
               mapping: dict, include_open: bool) -> Plan:
    """Mutates containers (new identities) and works on copies of Zen spaces.

    Each Arc space keeps the workspace an earlier sync put it in, so no other
    space can take that over. On the first sync, the others are matched by
    --map or by name. On later runs only --map picks an existing workspace:
    a space that is new since the last sync gets a new one, so a workspace
    you set up yourself is never taken over by accident.
    """
    plan = Plan()
    saved_spaces = (state or {}).get("spaces") or {}
    saved_ws = (state or {}).get("workspaces") or {}
    zen_spaces = [s for s in session.get("spaces") or [] if isinstance(s, dict) and s.get("uuid")]
    zen_by_uuid = {s["uuid"]: s for s in zen_spaces}
    targets = {norm(k): v for k, v in mapping.items()}
    arc_names = {norm(sp.name) for sp in arc.spaces}
    plan.unknown_map = [k for k in mapping if norm(k) not in arc_names]

    # Which Arc spaces: non-empty ones, ones synced before (so removals in Arc
    # reach Zen), and the first space of a profile that only has Favorites
    def active(sp):
        return bool(sp.pinned or (include_open and sp.open) or sp.id in saved_spaces)
    chosen = {sp.id for sp in arc.spaces if active(sp)}
    for profile, favs in arc.favorites.items():
        if not favs or any(sp.profile == profile and sp.id in chosen for sp in arc.spaces):
            continue
        first = next((sp for sp in arc.spaces if sp.profile == profile), None)
        if first:
            chosen.add(first.id)
        else:
            plan.notes.append(f"{len(favs)} Favorite(s) of Arc profile "
                              f"{arc.profile_label(profile)} skipped: that profile has no space.")
    plan.skipped_spaces = [sp.name for sp in arc.spaces if sp.id not in chosen]
    spaces = [sp for sp in arc.spaces if sp.id in chosen]

    # Synced before but gone from Arc: that workspace and its synced items
    # are left as they are (only an explicit --map can reuse the workspace)
    arc_ids = {sp.id for sp in arc.spaces}
    for arc_id, ws in saved_spaces.items():
        if arc_id not in arc_ids and ws in zen_by_uuid:
            plan.gone[arc_id] = ws

    bound, used, moved = {}, {}, {}   # sp.id -> (zen, how); uuid -> Arc name; sp.id -> old
    kept = set()                      # workspaces kept from earlier syncs
    # 1. Every space keeps the workspace it was synced into, unless --map
    #    names another one (then it moves, which a re-run has to confirm)
    for sp in spaces:
        saved = zen_by_uuid.get(saved_spaces.get(sp.id))
        if saved is None or saved["uuid"] in used:
            continue
        target = targets.get(norm(sp.name))
        names = {norm(saved.get("name")), norm((saved_ws.get(saved["uuid"]) or {}).get("name"))}
        if target and norm(target) not in names:
            moved[sp.id] = saved
            continue
        bound[sp.id] = (saved, "synced before")
        used[saved["uuid"]] = sp.name
        kept.add(saved["uuid"])
    #    ...or, without a record (state file lost), the workspace an earlier
    #    sync created for it
    for sp in spaces:
        if sp.id in bound or sp.id in moved:
            continue
        ws, target = space_uuid(sp.id), targets.get(norm(sp.name))
        found = zen_by_uuid.get(ws)
        if found and ws not in used and ws not in plan.gone.values() and (
                not target or norm(target) == norm(found.get("name"))):
            bound[sp.id] = (found, "synced before")
            used[ws] = sp.name
            kept.add(ws)

    # 2. The rest: --map, then (first sync only) the same name, else new
    for sp in spaces:
        if sp.id in bound:
            continue
        target = targets.get(norm(sp.name))
        want = target or sp.name
        same_name = [z for z in zen_spaces if norm(z.get("name")) == norm(want)]
        free = [z for z in same_name if z["uuid"] not in used]
        zen = None
        if target:
            taken = [used[z["uuid"]] for z in same_name
                     if z["uuid"] in kept and used[z["uuid"]] != sp.name]
            if not free and taken:
                other = taken[0]
                raise SyncError(f"--map puts Arc space '{sp.name}' into '{target}', but that "
                                f"workspace already holds Arc space '{other}'. Two Arc spaces "
                                "can't share a workspace; pick another name.")
            zen = free[0] if free else None
            how = "--map" if zen else "new"
        elif state is None:
            zen = free[0] if free else None
            how = "same name" if zen else "new"
        else:
            how = "new"
            if free and sp.id not in saved_spaces:
                plan.notes.append(
                    f"Arc space '{sp.name}' wasn't synced before, so it gets a new workspace even "
                    f"though Zen has one named '{free[0].get('name')}'. To sync into that one "
                    f"instead, add --map \"{sp.name}={free[0].get('name')}\".")
        if saved_spaces.get(sp.id) and saved_spaces[sp.id] not in zen_by_uuid:
            plan.notes.append(f"Workspace for Arc space '{sp.name}' was deleted in Zen; "
                              "it will be created again.")
        if zen is None:
            zen = {"uuid": space_uuid(sp.id), "name": want,
                   "theme": {"type": "gradient", "gradientColors": [], "opacity": 0.5,
                             "texture": 0},
                   "containerTabId": 0, "hasCollapsedPinnedTabs": False}
            if zen["uuid"] in zen_by_uuid or zen["uuid"] in used:
                zen["uuid"] = "{" + str(uuid.uuid4()) + "}"
            if target:
                plan.notes.append(f"--map: no Zen workspace named '{target}'; "
                                  "it will be created.")
        bound[sp.id] = (zen, how)
        used[zen["uuid"]] = sp.name
        if sp.id in moved:
            old = moved[sp.id].get("name")
            plan.rebinds.append(
                f"Arc '{sp.name}' was synced into '{old}'; --map now puts it in "
                f"'{zen.get('name')}'. Its synced items would move there ('{old}' keeps "
                "your own items).")
    for arc_id, ws in list(plan.gone.items()):
        if ws in used:                    # reused by --map: synced from the new space now
            del plan.gone[arc_id]
            continue
        n = sum(1 for t in session.get("tabs") or [] if isinstance(t, dict)
                and t.get("zenWorkspace") == ws and is_owned_tab(t.get("zenSyncId"))
                and not t.get("zenIsEmpty"))
        plan.notes.append(f"The Arc space synced into '{zen_by_uuid[ws].get('name')}' is no "
                          f"longer in Arc; its {n} synced tab(s) there are left as they are.")

    for sp in spaces:
        zen, how = bound[sp.id]
        plan.spaces.append(SpacePlan(sp, copy.deepcopy(zen), how))

    choose_containers(plan, arc, containers, state)
    for profile, ctx in plan.profile_container.items():
        favs = arc.favorites.get(profile) or []
        plan.essentials[profile] = favs
    for item in plan.spaces:
        item.container = plan.profile_container[item.arc.profile]
        if (state is not None and item.how == "--map" and item.uuid not in saved_ws
                and item.old_container != item.container):
            plan.rebinds.append(
                f"'{item.zen.get('name')}' wasn't synced into before; --map makes it Arc "
                f"'{item.arc.name}''s workspace and moves it from container "
                f"{container_label(containers, item.old_container)} to "
                f"{container_label(containers, item.container)}, so sites there lose that "
                "container's cookies and logins.")
    return plan


def choose_containers(plan: Plan, arc: ArcData, containers: dict, state: dict | None):
    public = {i["userContextId"] for i in containers["identities"]
              if i.get("public") and isinstance(i.get("userContextId"), int)}
    saved = (state or {}).get("arc_profiles") or {}
    profiles = list(OrderedDict.fromkeys(item.arc.profile for item in plan.spaces))
    taken = {}

    for profile in profiles:          # 1. what earlier runs chose (no churn)
        ctx = saved.get(profile)
        if not isinstance(ctx, int):
            continue
        valid = ctx in public or (ctx == 0 and profile == DEFAULT_PROFILE)
        if valid and ctx not in taken:
            plan.profile_container[profile] = ctx
            taken[ctx] = profile
        elif not valid:
            plan.notes.append(f"Container {ctx} used for Arc profile {arc.profile_label(profile)} "
                              "no longer exists; picking another.")

    for profile in profiles:          # 2. what the matched Zen workspaces use
        if profile in plan.profile_container:
            continue
        votes, order = Counter(), []
        for item in plan.spaces:
            if item.arc.profile != profile or item.how == "new":
                continue
            ctx = item.zen.get("containerTabId") or 0
            if (ctx in public or ctx == 0) and ctx not in taken:
                votes[ctx] += 1
                order.append(ctx)
        if profile != DEFAULT_PROFILE:
            votes.pop(0, None)        # other profiles need their own cookie jar
        if votes:
            best = max(votes, key=lambda c: (votes[c], -order.index(c)))
        elif profile == DEFAULT_PROFILE and 0 not in taken:
            best = 0
        else:
            hint = arc.profile_names.get(profile) or next(
                item.arc.name for item in plan.spaces if item.arc.profile == profile)
            identity = new_identity(containers, hint, len(plan.new_identities))
            plan.new_identities.append(identity)
            best = identity["userContextId"]
        plan.profile_container[profile] = best
        taken[best] = profile


# --------------------------------------------------------------------------- #
# Guard: did the user change synced items in Zen since the last sync?
# --------------------------------------------------------------------------- #
def describe(entry: dict) -> str:
    text = entry.get("label") or entry.get("url") or entry.get("id")
    return f"folder '{text}'" if entry["kind"] == "folder" else str(text)


def detect_zen_changes(state: dict, session: dict, sections: set) -> dict:
    """{section: {category: [descriptions]}} for recorded sections we would touch."""
    current = render_layout(session)
    where = {}
    for sec, entries in current.items():
        for e in entries:
            where.setdefault(e["id"], (sec, e))
    changes: dict = OrderedDict()
    recorded_anywhere = {e["id"] for entries in (state.get("layout") or {}).values()
                         for e in entries}

    def add(sec, category, text):
        changes.setdefault(sec, OrderedDict()).setdefault(category, []).append(text)

    for sec, recorded in (state.get("layout") or {}).items():
        if sec not in sections:
            continue
        is_open = sec.startswith("open ")
        now = current.get(sec, [])
        recorded_ids = {e["id"] for e in recorded}
        for e in recorded:
            if not e.get("owned"):
                continue
            found = where.get(e["id"])
            if found is None:
                add(sec, "closed" if e["kind"] == "tab" else "deleted", describe(e))
                continue
            cur_sec, cur = found
            if cur_sec != sec:
                add(sec, "moved", f"{describe(e)} (to another workspace or section)")
            elif cur["parent"] != e.get("parent"):
                add(sec, "moved", f"{describe(e)} (to another folder)")
            else:
                if cur["label"] != e.get("label"):
                    add(sec, "renamed", f"{describe(e)} -> {cur['label'] or '(page title)'}")
                if e["kind"] == "tab" and not is_open and cur["url"] != e.get("url"):
                    add(sec, "URL changed", f"{describe(e)} -> {cur['url']}")
            if cur_sec == sec and cur.get("split") != e.get("split"):
                add(sec, "split view", f"{describe(e)} "
                    + ("(put in a split view)" if cur.get("split") else "(split view undone)"))
        if not is_open:
            for cur in now:
                if cur["id"] in recorded_ids:
                    continue
                if not cur.get("owned"):
                    add(sec, "added by you", describe(cur))
                elif cur["id"] not in recorded_anywhere:
                    add(sec, "moved", f"{describe(cur)} (moved here)")
            # Same items under the same parent, in a different order? A re-sync
            # writes the synced items first and keeps yours after them, so
            # only an order that is neither the recorded one nor "synced
            # items unchanged, yours after" would be lost.
            rec_parent = {e["id"]: e.get("parent") for e in recorded}
            cur_parent = {e["id"]: e["parent"] for e in now}
            owned = {e["id"] for e in recorded if e.get("owned")}
            common = {i for i in rec_parent if cur_parent.get(i, object()) == rec_parent[i]}
            reordered = []
            for parent in dict.fromkeys(rec_parent[i] for i in common):
                if parent is not None and parent not in owned:
                    continue                  # inside your own folder: kept as is
                was = [e["id"] for e in recorded if e["id"] in common and e["parent"] == parent]
                now_ids = [e["id"] for e in now if e["id"] in common and e["parent"] == parent]
                resync = [i for i in was if i in owned] + [i for i in now_ids if i not in owned]
                if now_ids not in (was, resync):
                    reordered.append(parent)
            for parent in reordered:
                label = "top level" if parent is None else describe(where[parent][1]) \
                    if parent in where else parent
                add(sec, "reordered", f"in {label}")

    for ws, info in (state.get("workspaces") or {}).items():
        if section_key("pinned", ws) not in sections:
            continue
        zen = next((s for s in session.get("spaces") or []
                    if isinstance(s, dict) and s.get("uuid") == ws), None)
        if zen is None:
            add(section_key("pinned", ws), "workspace deleted", info.get("name") or ws)
        elif (zen.get("containerTabId") or 0) != info.get("container", 0):
            add(section_key("pinned", ws), "container changed",
                f"{info.get('container', 0)} -> {zen.get('containerTabId') or 0}")
    return changes


def guard_sections(state: dict, plan: Plan, managed: set, include_open: bool) -> set:
    """Recorded sections this run would touch: those of Arc spaces and
    profiles still being synced (even if their Zen workspace was deleted or
    re-mapped), plus everything managed now."""
    saved_spaces = state.get("spaces") or {}
    saved_profiles = state.get("arc_profiles") or {}
    workspaces = {item.uuid for item in plan.spaces}
    workspaces |= {saved_spaces[item.arc.id] for item in plan.spaces
                   if item.arc.id in saved_spaces}
    contexts = {str(c) for c in plan.profile_container.values()}
    contexts |= {str(saved_profiles[p]) for p in plan.profile_container if p in saved_profiles}
    scope = set(managed)
    for sec in state.get("layout") or {}:
        kind, key = sec.split(" ", 1)
        if kind == "essentials" and key in contexts:
            scope.add(sec)
        elif kind == "pinned" and key in workspaces:
            scope.add(sec)
        elif kind == "open" and key in workspaces and include_open:
            scope.add(sec)
    for ws in workspaces:
        scope.add(section_key("pinned", ws))
    return scope


def print_changes(changes: dict, names: dict) -> None:
    for sec, cats in changes.items():
        kind, key = sec.split(" ", 1)
        where = (f"Essentials in container {key}" if kind == "essentials"
                 else f"'{names.get(key, key)}' {kind}")
        summary = ", ".join(f"{len(v)} {k}" if k != "reordered" else f"reordered ({len(v)}x)"
                            for k, v in cats.items())
        log(f"  {where}: {summary}")
        for cat, items in cats.items():
            for text in items[:3]:
                log(f"      {cat}: {text}")
            if len(items) > 3:
                log(f"      {cat}: ... and {len(items) - 3} more")


# --------------------------------------------------------------------------- #
# Building the new session
# --------------------------------------------------------------------------- #
def tab_section(tab: dict) -> str:
    if tab.get("zenEssential"):
        return section_key("essentials", tab.get("userContextId") or 0)
    return section_key("pinned" if tab.get("pinned") else "open", tab.get("zenWorkspace"))


def build_session(plan: Plan, arc: ArcData, session: dict, state: dict | None,
                  icons: ZenIcons, include_open: bool, replace_existing: bool,
                  now_ms: int) -> tuple[dict, set, dict]:
    """Returns (new session, managed section keys, per-section stats)."""
    session = copy.deepcopy(session)
    builder = SessionBuilder(session, now_ms)
    for profile, favs in plan.essentials.items():
        builder.add_essentials(favs, plan.profile_container[profile])
    for item in plan.spaces:
        builder.add_pinned(item.arc.pinned, item.uuid, item.container)
        if include_open:
            builder.add_open(item.arc.open, item.uuid, item.container)

    managed = set()
    for item in plan.spaces:
        managed.add(section_key("pinned", item.uuid))
        if include_open:
            managed.add(section_key("open", item.uuid))
    for ctx in plan.profile_container.values():
        managed.add(section_key("essentials", ctx))

    new_tab_ids = {t["zenSyncId"] for t in builder.tabs}
    new_folder_ids = {f["id"] for f in builder.folders}
    stats: dict = {}
    # What an earlier migration of the same Arc items would look like: the
    # same (pinned) URL in the same section, a folder with the same name
    arc_urls, arc_folders = {}, {}
    for t in builder.tabs:
        if not t["zenIsEmpty"]:
            arc_urls.setdefault(tab_section(t), set()).add(tab_url(t))
    for f in builder.folders:
        arc_folders.setdefault(section_key("pinned", f["workspaceId"]), set()).add(norm(f["name"]))

    def bump(sec, key):
        stats.setdefault(sec, Counter())[key] += 1

    kept_tabs = []
    for tab in session.get("tabs") or []:
        if not isinstance(tab, dict):
            kept_tabs.append(tab)
            continue
        tid, sec = tab.get("zenSyncId"), tab_section(tab)
        if tid in new_tab_ids:
            continue
        if sec in managed and is_owned_tab(tid):
            if not tab.get("zenIsEmpty"):
                bump(sec, "removed")      # gone from Arc
            continue
        if tab.get("zenIsEmpty") and tab.get("groupId") in new_folder_ids:
            continue                      # a second placeholder for one of our folders
        if sec in managed and replace_existing:
            if not tab.get("zenIsEmpty"):
                bump(sec, "replaced")
            continue
        if sec in managed and not tab.get("zenIsEmpty"):
            bump(sec, "kept")
            if not sec.startswith("open ") and tab_url(tab) in arc_urls.get(sec, ()):
                bump(sec, "kept dup")
        kept_tabs.append(tab)

    kept_folders = []
    for folder in session.get("folders") or []:
        if not isinstance(folder, dict):
            continue
        fid = folder.get("id")
        sec = section_key("pinned", folder.get("workspaceId"))
        if fid in new_folder_ids:
            continue
        if sec in managed and (is_owned_folder(fid) or replace_existing):
            bump(sec, "folders removed" if is_owned_folder(fid) else "folders replaced")
            continue
        if sec in managed:
            bump(sec, "folders kept")
            if norm(folder.get("name")) in arc_folders.get(sec, ()):
                bump(sec, "folders kept dup")
        kept_folders.append(folder)

    folder_ids = new_folder_ids | {f.get("id") for f in kept_folders}
    other_group_ids = {t.get("groupId") for t in kept_tabs if isinstance(t, dict)} - folder_ids
    group_ids = folder_ids | {g.get("id") for g in session.get("groups") or []
                              if isinstance(g, dict) and g.get("id") in other_group_ids
                              and not is_owned_folder(g.get("id"))}
    orphans = [t for t in kept_tabs if isinstance(t, dict) and t.get("groupId")
               and t["groupId"] not in group_ids]
    for tab in orphans:
        tab.pop("groupId")                # its folder was removed: move to top level
    orphan_ids = {id(t) for t in orphans if t.get("zenIsEmpty")}
    kept_tabs = [t for t in kept_tabs if id(t) not in orphan_ids]
    for folder in kept_folders:
        if folder.get("parentId") and folder["parentId"] not in folder_ids:
            folder["parentId"] = None
            folder["prevSiblingInfo"] = {"type": "start", "id": None}
    kept_groups = [g for g in session.get("groups") or [] if isinstance(g, dict)
                   and g.get("id") in group_ids and g.get("id") not in new_folder_ids]

    # Workspaces: first sync puts Arc's spaces first in Arc's order; later
    # runs keep the order the user has in Zen
    plan_uuids = [item.uuid for item in plan.spaces]
    others = [s for s in session.get("spaces") or []
              if not (isinstance(s, dict) and s.get("uuid") in plan_uuids)]
    seen = (state or {}).get("workspaces") or {}
    for item in plan.spaces:
        apply_space_look(item, icons, seen.get(item.uuid))
    if state is None:
        spaces = [item.zen for item in plan.spaces] + others
    else:
        replaced = {item.uuid: item.zen for item in plan.spaces}
        spaces = [replaced.pop(s["uuid"]) if isinstance(s, dict) and s.get("uuid") in replaced
                  else s for s in session.get("spaces") or []]
        spaces += [item.zen for item in plan.spaces if item.uuid in replaced]

    session["spaces"] = spaces
    session["tabs"] = builder.tabs + kept_tabs
    session["folders"] = builder.folders + kept_folders
    session["groups"] = builder.groups + kept_groups
    # splitViewData is left alone: Zen skips entries whose tabs are gone

    ids = [t.get("zenSyncId") for t in session["tabs"] if isinstance(t, dict)]
    dupes = [i for i, n in Counter(ids).items() if n > 1 and is_owned_tab(i)]
    fdupes = [i for i, n in Counter(f.get("id") for f in session["folders"]).items() if n > 1]
    if dupes or fdupes:
        raise SyncError(f"Internal error: duplicate ids {dupes[:3] + fdupes[:3]}; "
                        "nothing written.")
    return session, managed, stats


def apply_space_look(item: SpacePlan, icons: ZenIcons, synced: dict | None) -> None:
    """Container, icon and color. synced is what the last sync recorded for
    this workspace (None on its first sync). Later runs only update an icon
    you haven't changed in Zen since, and never touch the color."""
    zen, space = item.zen, item.arc
    zen["containerTabId"] = item.container
    icon = space.emoji
    if not icon and space.icon_name and icons.has(space.icon_name):
        icon = ZEN_ICON_URL.format(space.icon_name)
    item.synced_icon = zen.get("icon") if synced is None else synced.get("icon")
    if icon and (synced is None or zen.get("icon") == item.synced_icon):
        zen["icon"] = item.synced_icon = icon
    theme = zen.get("theme") if isinstance(zen.get("theme"), dict) else None
    if theme is None:
        theme = zen["theme"] = {"type": "gradient", "gradientColors": [], "opacity": 0.5,
                                "texture": 0}
    if space.color and synced is None and not theme.get("gradientColors"):
        theme["gradientColors"] = [{
            "c": space.color, "isCustom": False, "algorithm": "floating", "isPrimary": True,
            "lightness": "60", "position": {"x": 81, "y": 152},
            "type": "explicit-lightness",
        }]


def build_state(plan: Plan, new_session: dict, managed: set, old_state: dict | None) -> dict:
    layout = render_layout(new_session)
    recorded = OrderedDict((sec, layout.get(sec, [])) for sec in sorted(managed))
    old = old_state or {}
    old_layout = old.get("layout") or {}
    plan_uuids = {item.uuid for item in plan.spaces}
    spaces = {item.arc.id: item.uuid for item in plan.spaces}
    workspaces = {item.uuid: {"name": item.zen.get("name"), "container": item.container,
                              "icon": item.synced_icon}
                  for item in plan.spaces}
    profiles = OrderedDict(plan.profile_container)
    carry = []
    for sec, entries in old_layout.items():
        kind, key = sec.split(" ", 1)
        if sec not in recorded and kind == "open" and key in plan_uuids:
            recorded[sec] = entries       # open tabs skipped this run (--no-open-tabs)
    # Keep the records of Arc spaces (and profiles) missing from Arc this run,
    # so if they come back, changes made in Zen meanwhile are still caught
    existing = {s.get("uuid") for s in new_session.get("spaces") or [] if isinstance(s, dict)}
    for arc_id, ws in plan.gone.items():
        if ws in plan_uuids or ws not in existing or arc_id in spaces:
            continue
        spaces[arc_id] = ws
        workspaces[ws] = (old.get("workspaces") or {}).get(ws) or {}
        carry += [section_key("pinned", ws), section_key("open", ws)]
    for profile, ctx in (old.get("arc_profiles") or {}).items():
        if profile not in profiles and ctx not in profiles.values():
            profiles[profile] = ctx
            carry.append(section_key("essentials", ctx))
    for sec in carry:
        if sec not in recorded and sec in old_layout:
            recorded[sec] = old_layout[sec]
    created = set(old.get("created_containers") or [])
    created |= {i["userContextId"] for i in plan.new_identities}
    return {
        "version": STATE_VERSION,
        "tool": "arc2zen sync_arc_to_zen.py",
        "synced_at": datetime.now().isoformat(timespec="seconds"),
        "arc_profiles": dict(profiles),
        "created_containers": sorted(created),
        "spaces": spaces,
        "workspaces": workspaces,
        "layout": recorded,
    }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def icon_text(icon) -> str:
    if not isinstance(icon, str) or not icon:
        return ""
    if icon.startswith("chrome://"):
        return "[" + icon.rsplit("/", 1)[-1].replace(".svg", "") + "] "
    return icon + " "


STAT_LABELS = [
    ("kept", "tab(s) already in Zen kept, placed after the Arc items"),
    ("folders kept", "folder(s) already in Zen kept, placed after the Arc items"),
    ("replaced", "pre-existing tab(s) removed (--replace-existing)"),
    ("folders replaced", "pre-existing folder(s) removed (--replace-existing)"),
    ("removed", "synced tab(s) no longer in Arc, removed"),
    ("folders removed", "synced folder(s) no longer in Arc, removed"),
]


def print_plan(plan: Plan, arc: ArcData, containers: dict, stats: dict,
               include_open: bool, replace_existing: bool, first_sync: bool) -> dict:
    totals = Counter()
    new_ids = {i["userContextId"] for i in plan.new_identities}
    log("\nPlan:")
    for n, item in enumerate(plan.spaces, 1):
        pinned_tabs, folders = count_nodes(item.arc.pinned)
        open_tabs = sum(1 for _ in flatten(item.arc.open)) if include_open else 0
        how = {"new": "new workspace", "same name": "matched by name",
               "--map": "matched by --map", "synced before": "synced before"}[item.how]
        log(f"  {n}. {icon_text(item.zen.get('icon'))}'{item.zen.get('name')}' <- Arc "
            f"'{item.arc.name}', profile {arc.profile_label(item.arc.profile)}  [{how}]")
        ctx_text = container_label(containers, item.container)
        if item.container in new_ids:
            ctx_text += " (new)"
        log(f"       container {ctx_text}")
        if item.old_container is not None and item.old_container != item.container:
            log(f"       ! was container {item.old_container}: sites here will not see cookies "
                "or logins from that container")
        if item.old_icon and item.old_icon != item.zen.get("icon"):
            log(f"       ! icon {icon_text(item.old_icon)}-> {icon_text(item.zen.get('icon'))}"
                .rstrip())
        log(f"       pinned {pinned_tabs}, folders {folders}"
            + (f", open {open_tabs}" if include_open else ", open tabs skipped"))
        pinned_stats = stats.get(section_key("pinned", item.uuid), Counter())
        open_stats = stats.get(section_key("open", item.uuid), Counter())
        for key, text in STAT_LABELS:
            n = pinned_stats.get(key, 0) + open_stats.get(key, 0)
            if n:
                split = f" ({open_stats[key]} of them open tabs)" if open_stats.get(key) else ""
                log(f"       {n} {text}{split}")
                totals[key] += n
        totals["pinned"] += pinned_tabs
        totals["folders"] += folders
        totals["open"] += open_tabs
        for key in ("kept dup", "folders kept dup"):
            totals[key] += pinned_stats.get(key, 0) + open_stats.get(key, 0)

    lines = []
    for profile, favs in plan.essentials.items():
        ctx = plan.profile_container[profile]
        s = stats.get(section_key("essentials", ctx), Counter())
        extra = []
        if s.get("kept"):
            extra.append(f"{s['kept']} already in Zen kept")
        if s.get("replaced"):
            extra.append(f"{s['replaced']} pre-existing removed")
        if s.get("removed"):
            extra.append(f"{s['removed']} no longer in Arc removed")
        if not favs and not extra:
            continue
        lines.append(f"       container {container_label(containers, ctx)}: {len(favs)} from "
                     f"{arc.profile_label(profile)}" + (f" ({', '.join(extra)})" if extra else ""))
        totals["essentials"] += len(favs)
        totals["kept"] += s.get("kept", 0)
        totals["kept dup"] += s.get("kept dup", 0)
        totals["replaced"] += s.get("replaced", 0)
    if lines:
        log("  Essentials (Arc Favorites; shown in workspaces of the same container):")
        for line in lines:
            log(line)
    if plan.skipped_spaces:
        log(f"  Skipped empty Arc spaces: {', '.join(repr(n) for n in plan.skipped_spaces)}")
    for line in arc.skipped_summary():
        log(f"  Note: {line}")
    for note in plan.notes:
        log(f"  Note: {note}")
    if first_sync and not replace_existing and (totals["kept"] or totals["folders kept"]):
        log(f"  Note: {totals['kept']} tab(s) and {totals['folders kept']} folder(s) that were "
            "already in these workspaces/containers are kept, after the Arc items. To drop "
            "them on this first sync, add --replace-existing.")
        if totals["kept dup"] or totals["folders kept dup"]:
            log(f"\n  Warning: {totals['kept dup']} of those tab(s) and "
                f"{totals['folders kept dup']} folder(s) have the same URL or folder name as an "
                "Arc item in the same workspace or container. They look like an earlier "
                "migration of the same Arc items (migrate_arc_to_zen.py, or an older "
                "version of this sync) and would show up twice.")
    return totals


def print_dump(arc: ArcData) -> None:
    log(f"Arc data: {arc.path}")
    log("\nProfiles:")
    profiles = list(OrderedDict.fromkeys([sp.profile for sp in arc.spaces]
                                         + list(arc.favorites)))
    for p in profiles:
        log(f"  {arc.profile_label(p)}: {len(arc.favorites.get(p) or [])} favorite(s), "
            f"{sum(1 for sp in arc.spaces if sp.profile == p)} space(s)")

    def tree(nodes, depth):
        for node in nodes:
            pad = "      " + "   " * depth
            if node["kind"] == "folder":
                log(f"{pad}+ {node['name']}/" + ("  (empty)" if not node["children"] else ""))
                tree(node["children"], depth + 1)
            else:
                name = node["label"] or node["title"]
                log(f"{pad}- {name[:60]}  {node['url']}")

    log("\nSpaces:")
    for n, sp in enumerate(arc.spaces, 1):
        color = "#{:02x}{:02x}{:02x}".format(*sp.color) if sp.color else "-"
        icon = sp.emoji or (f"[{sp.icon_name}]" if sp.icon_name else "-")
        pinned, folders = count_nodes(sp.pinned)
        opened = sum(1 for _ in flatten(sp.open))
        log(f"  {n}. '{sp.name}'  profile {arc.profile_label(sp.profile)}  icon {icon}  "
            f"color {color}")
        log(f"     pinned: {pinned} tab(s), {folders} folder(s)")
        tree(sp.pinned, 0)
        log(f"     open: {opened} tab(s)")
        tree(sp.open, 0)
    log("\nFavorites (-> Zen Essentials):")
    for p, favs in arc.favorites.items():
        log(f"  {arc.profile_label(p)}:")
        tree(favs, 0)
    notes = arc.skipped_summary()
    if notes:
        log()
    for line in notes:
        log(f"Note: {line}")


# --------------------------------------------------------------------------- #
# Extensions
# --------------------------------------------------------------------------- #
def _http_open(url: str, timeout: int = 60):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def amo_addon(slug: str) -> dict:
    with _http_open(AMO_API.format(urllib.parse.quote(slug, safe=""))) as r:
        return json.load(r)


def download_verified(url: str, dest: str, expected: str) -> None:
    algo, _, want = (expected or "").partition(":")
    if algo not in ("sha256", "sha512") or not want:
        raise SyncError(f"AMO gave no usable checksum ({expected!r})")
    digest = hashlib.new(algo)
    tmp = f"{dest}.arc2zen-tmp-{os.getpid()}"
    try:
        with _http_open(url, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                digest.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        if digest.hexdigest() != want.lower():
            raise SyncError("checksum mismatch, download discarded")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def install_extensions(arc: ArcData, profile: str, only: list, apply: bool) -> bool:
    log("\nExtensions:")
    found = arc.extensions()
    wanted = OrderedDict()
    for ext_id, name in sorted(found.items(), key=lambda kv: kv[1].lower()):
        if ext_id in EXTENSION_MAP:
            wanted.setdefault(EXTENSION_MAP[ext_id], name)
    unmapped = sorted({name for ext_id, name in found.items()
                       if ext_id not in EXTENSION_MAP and ext_id not in EXTENSION_NOTES},
                      key=str.lower)
    noted = sorted(((name, EXTENSION_NOTES[ext_id]) for ext_id, name in found.items()
                    if ext_id in EXTENSION_NOTES and ext_id not in EXTENSION_MAP),
                   key=lambda item: item[0].lower())
    if only:
        for slug in only:
            if slug not in wanted:
                wanted[slug] = f"{slug} (not detected in Arc, installing as asked)"
        wanted = OrderedDict((s, n) for s, n in wanted.items() if s in only)
    # Chrome dropped Manifest V2, so Arc users often have uBlock Origin (now disabled)
    # and uBO Lite. Firefox still runs the full uBlock Origin; one ad blocker is enough.
    if "ublock-origin" in wanted and "ublock-origin-lite" in wanted \
            and "ublock-origin-lite" not in (only or ()):
        noted.append((wanted.pop("ublock-origin-lite"), "uBlock Origin covers it in Firefox"))
    if not wanted:
        log("  No Arc extension with a known Firefox version.")

    installed = set()
    try:
        with open(os.path.join(profile, "extensions.json"), encoding="utf-8") as f:
            installed = {a.get("id") for a in json.load(f).get("addons", [])}
    except (OSError, ValueError, AttributeError):
        pass
    ext_dir = os.path.join(profile, "extensions")
    done, present, failed = [], [], []
    for slug, arc_name in wanted.items():
        try:
            info = amo_addon(slug)
            version = info.get("current_version") or {}
            file = version.get("file") or {}
            guid = info.get("guid")
            if not guid or not file.get("url") or info.get("is_disabled"):
                raise SyncError("not available on addons.mozilla.org")
            names = info.get("name") or {}
            name = names.get("en-US") or next(iter(names.values()), slug)
            dest = os.path.join(ext_dir, f"{guid}.xpi")
            if guid in installed or os.path.exists(dest):
                present.append(name)
                log(f"  already installed  {name}")
                continue
            size = (file.get("size") or 0) / 1e6
            if not apply:
                log(f"  would install      {name} {version.get('version', '')} ({size:.1f} MB)")
                done.append(name)
                continue
            os.makedirs(ext_dir, exist_ok=True)
            download_verified(file["url"], dest, file.get("hash"))
            log(f"  installed          {name} {version.get('version', '')} (checksum verified)")
            done.append(name)
        except Exception as e:  # keep going: one bad slug must not stop the rest
            failed.append(slug)
            log(f"  failed             {slug}: {e}")
    for name, note in noted:
        log(f"  skipped            {name} ({note})")
    if unmapped:
        log(f"  No known Firefox version for {len(unmapped)} Arc extension(s): "
            + ", ".join(unmapped[:12]) + (" ..." if len(unmapped) > 12 else ""))
        log("  (search addons.mozilla.org, then pass their slugs to --extensions)")
    verb = "to install" if not apply else "installed"
    log(f"  {len(done)} {verb}, {len(present)} already there, {len(failed)} failed.")
    if done:
        log("  Zen lists sideloaded add-ons as disabled: on next launch open the add-ons "
            "notification (or about:addons) and click Enable on each.")
    return not failed


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(1)


def parse_args(argv):
    p = _Parser(prog="sync_arc_to_zen.py",
                description="Mirror Arc's spaces, pinned tabs, folders, favorites and open "
                            "tabs into Zen. Dry run unless --apply.")
    p.add_argument("--apply", action="store_true", help="write the changes (Zen must be quit)")
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--profile", metavar="DIR",
                   help="target Zen profile dir (default: auto-detect)")
    p.add_argument("--map", metavar="'Arc Space=Zen Workspace,A=B'",
                   help="explicit Arc -> Zen workspace names (default: case-insensitive match)")
    p.add_argument("--dump-arc", action="store_true", help="print the Arc tree and exit")
    p.add_argument("--no-open-tabs", action="store_true", help="skip Arc's unpinned/open tabs")
    p.add_argument("--replace-existing", action="store_true",
                   help="first sync only: remove pre-existing tabs/folders in matched workspaces "
                        "and pre-existing essentials in the chosen containers")
    p.add_argument("--keep-existing", action="store_true",
                   help="first sync only: keep pre-existing tabs/folders even when they look "
                        "like an earlier migration of the same Arc items (they'd show twice)")
    p.add_argument("--overwrite-zen-changes", action="store_true",
                   help="re-sync even though synced items were changed in Zen since the last sync")
    p.add_argument("--skip-tabs", action="store_true", help="don't touch spaces/tabs")
    p.add_argument("--extensions", nargs="*", metavar="SLUG",
                   help="install Firefox versions of Arc extensions (each one found in Arc "
                        "that has a known Firefox version, or only these addons.mozilla.org "
                        "slugs)")
    p.add_argument("--force-running", action="store_true",
                   help="skip the 'a Zen process is running' check (a profile that a running "
                        "Zen has open is always refused)")
    args = p.parse_args(argv)
    if args.apply and args.dry_run:
        p.error("--apply and --dry-run can't be combined")
    if args.replace_existing and args.keep_existing:
        p.error("--replace-existing and --keep-existing can't be combined")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except SyncError as e:
        print(f"\nError: {e}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; nothing more was written.", file=sys.stderr)
        return 1


def run(args) -> int:
    apply = args.apply
    if arc_running():
        log("Warning: Arc is running; its sidebar file may change while being read. "
            "Quitting Arc first is safest.")
    arc = ArcData(arc_dir())
    if args.dump_arc:
        print_dump(arc)
        return 0

    profile = os.path.abspath(os.path.expanduser(args.profile)) if args.profile \
        else find_zen_profile(zen_dir())
    if not os.path.isdir(profile):
        raise SyncError(f"Zen profile not found: {profile}")
    log(f"Zen profile: {profile}")
    log(f"Arc data:    {arc.path}")

    check_zen_running(profile, apply, args.force_running)

    ok = True
    if not args.skip_tabs:
        ok = sync_tabs(arc, profile, args)
        if ok is None:
            return 1
    if args.extensions is not None:
        ok = install_extensions(arc, profile, args.extensions, apply) and ok
    if not apply:
        log("\nDRY RUN: nothing was written. Re-run with --apply after quitting Zen (Cmd+Q).")
    return 0 if ok else 1


def sync_tabs(arc: ArcData, profile: str, args):
    """Plan and (with --apply) write. Returns True, or None if refused."""
    apply, include_open = args.apply, not args.no_open_tabs
    session, containers = load_zen_profile(profile)
    errors, warnings = check_prefs(read_prefs(profile), profile, include_open)
    version = zen_version(profile)
    if version and not re.match(r"1\.22(\D|$)", version):
        warnings.insert(0, f"this profile was last opened by Zen {version}. This script was "
                           "written for Zen 1.22's session format and checked only against it.")
    for w in warnings:
        log(f"Warning: {w}")
    if errors:
        raise SyncError(" ".join(errors))
    state = load_state(profile)
    first_sync = state is None
    replace_existing = args.replace_existing and first_sync
    if (args.replace_existing or args.keep_existing) and not first_sync:
        flag = "--replace-existing" if args.replace_existing else "--keep-existing"
        log(f"Note: {flag} only applies to the first sync into a profile; ignored. To redo the "
            f"first sync, quit Zen, delete {STATE_FILE} from the profile and run again with "
            "--replace-existing (that drops every tab and folder in the synced workspaces that "
            "this sync didn't write).")
    if first_sync and any(is_owned_tab(t.get("zenSyncId")) for t in session.get("tabs") or []
                          if isinstance(t, dict)):
        log(f"Note: this profile has arc2zen items but no {STATE_FILE}; treating this as a "
            "first sync (no check for changes made in Zen).")
    if not arc.spaces and state is not None and state.get("spaces"):
        raise SyncError("Arc has no spaces right now (signed out, or its data was reset?), but "
                        "this profile was synced from Arc before. Nothing was changed. Open Arc "
                        "so it loads your spaces, then re-run.")

    icons = ZenIcons(find_zen_omni())
    if not icons.omni and any(sp.icon_name and not sp.emoji for sp in arc.spaces):
        log("Warning: Zen's omni.ja was not found (looked in /Applications, ~/Applications and "
            "the running Zen), so Arc's named space icons are skipped. Emoji icons still work.")

    new_containers = copy.deepcopy(containers)
    plan = build_plan(arc, session, new_containers, state, parse_map(args.map), include_open)
    new_session, managed, stats = build_session(
        plan, arc, session, state, icons, include_open, replace_existing,
        int(time.time() * 1000))
    names = {item.uuid: item.zen.get("name") for item in plan.spaces}
    totals = print_plan(plan, arc, new_containers, stats, include_open, replace_existing,
                        first_sync)

    for sp in session.get("spaces") or []:
        if (isinstance(sp, dict) and sp.get("name") == "Space" and not sp.get("containerTabId")
                and sp.get("uuid") not in names):
            tabs = [t for t in session.get("tabs") or [] if isinstance(t, dict)
                    and t.get("zenWorkspace") == sp.get("uuid") and not t.get("zenIsEmpty")]
            if any(is_owned_tab(t.get("zenSyncId")) for t in tabs):
                continue
            n = len(tabs)
            log(f"  Hint: Zen's default workspace 'Space' is still there ({n} tab(s)). If you "
                "don't need it, delete it in Zen after the sync (right-click it > Delete).")

    used = {s.get("containerTabId") for s in new_session["spaces"] if isinstance(s, dict)}
    was_used = {item.old_container for item in plan.spaces if item.old_container}
    unused = [i for i in new_containers["identities"] if i.get("public")
              and i.get("userContextId") not in used
              and (i.get("userContextId") in was_used
                   or i.get("userContextId") in ((state or {}).get("created_containers") or []))]
    for i in unused:
        ctx = i["userContextId"]
        hidden = sum(1 for t in new_session["tabs"] if isinstance(t, dict)
                     and t.get("zenEssential") and t.get("userContextId") == ctx)
        kept = f"its cookies and {hidden} Essential(s), hidden now" if hidden else "its cookies"
        log(f"  Note: container {ctx} '{identity_name(i)}' is no longer used by any workspace. "
            f"It is kept (with {kept}); remove it in Zen's settings if you don't need it.")

    if plan.unknown_map:
        text = ("--map names Arc space(s) that don't exist: "
                + ", ".join(f"'{n}'" for n in plan.unknown_map)
                + ". Arc space names are matched ignoring case; see --dump-arc for the list.")
        if apply:
            raise SyncError(f"{text} Nothing was written.")
        log(f"\nWarning: {text} --apply refuses until that's fixed.")

    if state is not None:
        changes = detect_zen_changes(state, session,
                                     guard_sections(state, plan, managed, include_open))
        if changes:
            log(f"\nSynced items were changed in Zen since the last sync "
                f"({state.get('synced_at', '?')}):")
            print_changes(changes, dict(names, **{k: v.get("name", k) for k, v in
                                                  (state.get("workspaces") or {}).items()}))
        if plan.rebinds:
            log("\nThis run would change which workspace holds a synced Arc space:")
            for text in plan.rebinds:
                log(f"  {text}")
        if changes or plan.rebinds:
            if not args.overwrite_zen_changes:
                log("\nRefusing to overwrite them. Keep your Zen layout (the sync is meant as a "
                    "one-time migration), or re-run with --overwrite-zen-changes to go ahead: "
                    "synced items are reset to Arc's layout (your own tabs and folders are "
                    "kept, after them).")
                return None
            log("  Overwriting them (--overwrite-zen-changes).")
    elif (totals["kept dup"] or totals["folders kept dup"]) and not replace_existing \
            and not args.keep_existing:
        log("\nRefusing: on this first sync, add --replace-existing to start clean in these "
            "workspaces and containers (every tab and folder there that this sync doesn't write "
            "is dropped), or --keep-existing to keep them anyway, after the Arc items.")
        return None

    new_identities = len(plan.new_identities)
    log(f"\nSummary: {len(plan.spaces)} workspace(s) "
        f"({sum(1 for i in plan.spaces if i.how == 'new')} new), "
        f"{len(plan.profile_container)} container(s) ({new_identities} new), "
        f"{totals['essentials']} essential(s), {totals['pinned']} pinned tab(s), "
        f"{totals['folders']} folder(s), {totals['open']} open tab(s).")
    if not apply:
        return True

    new_state = build_state(plan, new_session, managed, state)
    to_write = []
    # containers.json first, so the session never names a missing container
    if new_containers != containers:
        to_write.append((CONTAINERS_FILE, json.dumps(new_containers, separators=(",", ":"))
                         .encode("utf-8")))
    if new_session != session:
        to_write.append((SESSION_FILE, mozlz4_encode(new_session)))
    if not same_state(new_state, state):
        to_write.append((STATE_FILE, (json.dumps(new_state, indent=1, ensure_ascii=False)
                                      + "\n").encode("utf-8")))
    if not to_write:
        log("\nAlready in sync; nothing written.")
        return True

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    log("\nBackups:")
    backups = []
    for name, _ in to_write:
        dest = backup_file(os.path.join(profile, name), stamp)
        if dest:
            backups.append(os.path.basename(dest))
            log(f"  {name} -> {os.path.basename(dest)}")
    log(f"  (Zen also keeps rolling session backups in "
        f"{os.path.join(profile, 'zen-sessions-backup')})")

    for name, data in to_write:
        atomic_write(os.path.join(profile, name), data)
    log(f"Wrote {', '.join(name for name, _ in to_write)}.")
    steps = [f"copy {', '.join(backups)} back over the originals"] if backups else []
    if first_sync:
        steps.append(f"delete {STATE_FILE} (this was the first sync)")
    log(f"Start Zen to see the result. To undo this run: quit Zen, {' and '.join(steps)}.")
    return True


def same_state(new: dict, old: dict | None) -> bool:
    """Equal apart from the timestamp (compared as JSON would store them)."""
    if old is None:
        return False
    a = json.loads(json.dumps(new))
    b = dict(old)
    a.pop("synced_at", None)
    b.pop("synced_at", None)
    return a == b


if __name__ == "__main__":
    sys.exit(main())
