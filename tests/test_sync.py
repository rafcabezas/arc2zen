"""Tests for sync_arc_to_zen.py (mirror mode).  Run:  python3 -m pytest tests/test_sync.py

Everything runs on synthetic Arc and Zen data in temporary folders; the real
Arc and Zen folders are never read (ARC2ZEN_ARC_DIR / ARC2ZEN_ZEN_DIR /
ARC2ZEN_ZEN_APP point at the fixtures, and process checks are stubbed).
"""

import contextlib
import copy
import glob
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

import sync_arc_to_zen as S
from tests import zen_restore_sim as sim

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


# --------------------------------------------------------------------------- #
# Synthetic Arc data
# --------------------------------------------------------------------------- #
class ArcFixture:
    def __init__(self):
        self.items, self.spaces, self.top_apps = [], [], []
        self.icons = {}

    def item(self, iid, title, data, children=()):
        self.items += [iid, {"id": iid, "title": title, "data": data,
                             "childrenIds": list(children), "parentID": None}]

    def tab(self, iid, url, saved_title=None, title=None, children=(), icon=False):
        self.item(iid, title, {"tab": {"savedURL": url,
                                       "savedTitle": saved_title or f"Page {iid}"}}, children)
        if icon:
            self.icons[iid] = PNG

    def folder(self, iid, name, children):
        self.item(iid, name, {"list": {}}, children)

    def container(self, iid, children):
        self.item(iid, None, {"itemContainer": {"containerType": {"spaceItems": {}}}}, children)

    def space(self, sid, title, profile, pinned, unpinned, emoji=None, icon=None, color=None,
              markers=("pinned", "unpinned")):
        self.container(f"{sid}-pinned", pinned)
        self.container(f"{sid}-unpinned", unpinned)
        ids = []
        for m in markers:
            ids += [m, f"{sid}-{m}"]
        info = {}
        if emoji:
            info["iconType"] = {"emoji_v2": emoji}
        elif icon:
            info["iconType"] = {"icon": icon}
        if color:
            info["windowTheme"] = {"primaryColorPalette": {"midTone": {
                "red": color[0], "green": color[1], "blue": color[2], "alpha": 1}}}
        prof = {"default": True} if profile == "Default" else \
            {"custom": {"_0": {"directoryBasename": profile, "machineID": "M"}}}
        self.spaces += [sid, {"id": sid, "title": title, "profile": prof,
                              "containerIDs": ids, "customInfo": info}]

    def favorites(self, profile, cid, children):
        self.container(cid, children)
        prof = {"default": True} if profile == "Default" else \
            {"custom": {"_0": {"directoryBasename": profile}}}
        self.top_apps += [prof, cid]

    def write(self, path, sidebar_index=1, profile_names=None, extensions=None):
        os.makedirs(path, exist_ok=True)
        main = {"items": self.items, "spaces": self.spaces,
                "topAppsContainerIDs": self.top_apps}
        containers = [{"global": {}}] + [{} for _ in range(sidebar_index - 1)] + [main]
        with open(os.path.join(path, "StorableSidebar.json"), "w") as f:
            json.dump({"version": 1, "sidebar": {"containers": containers}}, f)
        cache = os.path.join(path, "SidebarItemsFaviconCache")
        os.makedirs(cache, exist_ok=True)
        for iid, data in self.icons.items():
            with open(os.path.join(cache, hashlib.md5(iid.encode()).hexdigest()), "wb") as f:
                f.write(data)
        user_data = os.path.join(path, "User Data")
        os.makedirs(user_data, exist_ok=True)
        info_cache = {k: {"name": v} for k, v in (profile_names or {}).items()}
        with open(os.path.join(user_data, "Local State"), "w") as f:
            json.dump({"profile": {"info_cache": info_cache}}, f)
        for prof, exts in (extensions or {}).items():
            os.makedirs(os.path.join(user_data, prof), exist_ok=True)
            with open(os.path.join(user_data, prof, "Secure Preferences"), "w") as f:
                json.dump({"extensions": {"settings": exts}}, f)


def standard_arc():
    a = ArcFixture()
    # Personal (Default profile): nested folders, empty folder, tab under tab,
    # unknown item kind, arc:// url, labels
    a.tab("t-news", "https://news.example.com/", saved_title="Example News - Home",
          title="News", icon=True)
    a.tab("t-docs", "https://docs.example.com/")
    a.tab("t-tool", "https://tool.example.com/")
    a.tab("t-deep", "https://deep.example.com/")
    a.folder("f-deep", "Deep", ["t-deep"])
    a.folder("f-tools", "Tools", ["t-tool", "f-deep"])
    a.tab("t-repo", "https://repo.example.com/")
    a.folder("f-dev", "Dev", ["t-docs", "f-tools", "t-repo"])
    a.folder("f-empty", "Empty", [])
    a.tab("t-child", "https://child.example.com/")
    a.tab("t-parent", "https://parent.example.com/", children=["t-child"])
    a.tab("t-split1", "https://split1.example.com/")
    a.tab("t-split2", "https://split2.example.com/")
    a.item("u-split", None, {"splitView": {}}, ["t-split1", "t-split2"])
    a.tab("t-arc", "arc://settings")
    a.tab("t-same", "https://same.example.com/", saved_title="Same", title="Same")
    a.tab("o-1", "https://open1.example.com/")
    a.tab("o-2", "https://open2.example.com/")
    a.space("s-personal", "Personal", "Default",
            ["t-news", "f-dev", "f-empty", "t-parent", "u-split", "t-arc", "t-same"],
            ["o-1", "o-2"], emoji="\U0001F3E0", color=(0.2, 0.4, 0.6))
    # Reading (Default): named icon, reversed marker order, welcomeToArc item
    a.tab("t-read1", "https://read1.example.com/")
    a.tab("t-read2", "https://read2.example.com/")
    a.folder("f-later", "Later", ["t-read2"])
    a.item("w-1", None, {"welcomeToArc": {}}, [])
    a.space("s-reading", "Reading", "Default", ["t-read1", "f-later", "w-1"], [],
            icon="book", markers=("unpinned", "pinned"))
    # Work (Profile 1): subfolder, tab, subfolder inside a folder
    a.tab("t-a1", "https://alpha.example.com/")
    a.folder("f-alpha", "Alpha", ["t-a1"])
    a.tab("t-mid", "https://mid.example.com/")
    a.tab("t-b1", "https://beta.example.com/")
    a.folder("f-beta", "Beta", ["t-b1"])
    a.folder("f-proj", "Projects", ["f-alpha", "t-mid", "f-beta"])
    a.tab("t-w1", "https://work.example.com/")
    a.tab("o-w", "https://open-work.example.com/")
    a.space("s-work", "Work", "Profile 1", ["f-proj", "t-w1"], ["o-w"], emoji="\U0001F4BC")
    # A second "Personal" on another profile, and an empty space
    a.tab("t-p2", "https://side.example.com/")
    a.space("s-personal2", "Personal", "Profile 2", ["t-p2"], [])
    a.space("s-scratch", "Scratch", "Profile 2", [], [])
    # Favorites per profile
    a.tab("fav-1", "https://mail.example.com/", icon=True)
    a.tab("fav-2", "https://calendar.example.com/")
    a.favorites("Default", "favs-default", ["fav-1", "fav-2"])
    a.tab("fav-w", "https://chat.example.com/")
    a.favorites("Profile 1", "favs-p1", ["fav-w"])
    a.tab("fav-s", "https://tasks.example.com/")
    a.favorites("Profile 2", "favs-p2", ["fav-s"])
    return a


PROFILE_NAMES = {"Default": "Personal", "Profile 1": "Work", "Profile 2": "Side Project"}

# Expected Zen trees (as the restore simulation reports them)
EXPECTED_PERSONAL = [
    ("tab", "https://news.example.com/"),
    ("folder", "Dev", [
        ("tab", "https://docs.example.com/"),
        ("folder", "Tools", [
            ("tab", "https://tool.example.com/"),
            ("folder", "Deep", [("tab", "https://deep.example.com/")]),
        ]),
        ("tab", "https://repo.example.com/"),
    ]),
    ("folder", "Empty", []),
    ("tab", "https://parent.example.com/"),
    ("tab", "https://child.example.com/"),
    ("tab", "https://split1.example.com/"),
    ("tab", "https://split2.example.com/"),
    ("tab", "https://same.example.com/"),
]
EXPECTED_WORK = [
    ("folder", "Projects", [
        ("folder", "Alpha", [("tab", "https://alpha.example.com/")]),
        ("tab", "https://mid.example.com/"),
        ("folder", "Beta", [("tab", "https://beta.example.com/")]),
    ]),
    ("tab", "https://work.example.com/"),
]


# --------------------------------------------------------------------------- #
# Synthetic Zen profiles
# --------------------------------------------------------------------------- #
DEFAULT_IDENTITIES = [
    {"icon": "fingerprint", "color": "blue", "l10nId": "user-context-personal", "public": True,
     "userContextId": 1},
    {"icon": "briefcase", "color": "orange", "l10nId": "user-context-work", "public": True,
     "userContextId": 2},
    {"icon": "dollar", "color": "green", "l10nId": "user-context-banking", "public": True,
     "userContextId": 3},
    {"icon": "cart", "color": "pink", "l10nId": "user-context-shopping", "public": True,
     "userContextId": 4},
    {"public": False, "icon": "", "color": "", "name": "userContextIdInternal.thumbnail",
     "accessKey": "", "userContextId": 5},
    {"userContextId": 4294967295, "public": False, "icon": "", "color": "",
     "name": "userContextIdInternal.webextStorageLocal", "accessKey": ""},
]


def zen_tab(sync_id, url, ws, ctx=0, pinned=True, essential=False, group=None, empty=False):
    tab = S.make_tab(sync_id, url, None if empty else f"Title of {url}", ws, ctx, pinned=pinned,
                     essential=essential, group_id=group, empty=empty, now=1700000000000)
    tab["lastAccessed"] = 1700000000000
    return tab


def zen_folder(fid, name, ws, placeholder):
    return {"pinned": True, "splitViewGroup": False, "id": fid, "name": name,
            "collapsed": False, "saveOnWindowClose": True, "parentId": None,
            "prevSiblingInfo": None, "emptyTabIds": [placeholder], "isLiveFolder": False,
            "workspaceId": ws}


def zen_group(gid, name):
    return {"pinned": True, "splitView": False, "id": gid, "name": name,
            "color": "zen-workspace-color", "collapsed": False, "saveOnWindowClose": True}


WS_DEFAULT = "{00000000-0000-4000-8000-000000000001}"
WS_PERSONAL = "{00000000-0000-4000-8000-00000000000a}"
WS_WORK = "{00000000-0000-4000-8000-00000000000b}"
WS_SIDE = "{00000000-0000-4000-8000-00000000000c}"
USER_ID = "1700000000000-0e6f3b39-8b8a-4b53-a1b6-6f9c2d3f4e5a"


def write_zen_profile(base, containers, session, prefs=None):
    profile = os.path.join(base, "Profiles", "abcd1234.Default (release)")
    os.makedirs(profile, exist_ok=True)
    with open(os.path.join(base, "profiles.ini"), "w") as f:
        f.write(textwrap.dedent("""\
            [Install0123456789ABCDEF]
            Default=Profiles/abcd1234.Default (release)
            Locked=1

            [Profile0]
            Name=Default (release)
            IsRelative=1
            Path=Profiles/abcd1234.Default (release)

            [General]
            StartWithLastProfile=1
            Version=2
            """))
    if containers is not None:
        with open(os.path.join(profile, "containers.json"), "w") as f:
            json.dump(containers, f)
    if session is not None:
        with open(os.path.join(profile, "zen-sessions.jsonlz4"), "wb") as f:
            f.write(S.mozlz4_encode(session))
    with open(os.path.join(profile, "prefs.js"), "w") as f:
        f.write('// Mozilla User Preferences\nuser_pref("browser.migration.version", 158);\n')
        for k, v in (prefs or {}).items():
            f.write(f'user_pref("{k}", {json.dumps(v)});\n')
    return profile


def fresh_zen(base, prefs=None):
    """A profile after Zen's first launch: default 'Space' with welcome content."""
    containers = {"version": 6, "lastUserContextId": 5,
                  "identities": copy.deepcopy(DEFAULT_IDENTITIES), "siteAssociations": {}}
    welcome_ph = zen_tab("1700000000000-11111111-1111-4111-8111-111111111111", "about:blank",
                         WS_DEFAULT, group="1700000000000-42", empty=True)
    session = {
        "lastCollected": 1700000000000,
        "tabs": [
            zen_tab("1700000000000-22222222-2222-4222-8222-222222222222",
                    "https://welcome-essential.example.org/", None, essential=True),
            welcome_ph,
            zen_tab("1700000000000-33333333-3333-4333-8333-333333333333",
                    "https://basics.example.org/", WS_DEFAULT, group="1700000000000-42"),
            zen_tab("1700000000000-44444444-4444-4444-8444-444444444444",
                    "https://welcome.example.org/", WS_DEFAULT, pinned=False),
        ],
        "folders": [zen_folder("1700000000000-42", "basics", WS_DEFAULT, welcome_ph["zenSyncId"])],
        "splitViewData": [],
        "groups": [zen_group("1700000000000-42", "basics")],
        "spaces": [{"uuid": WS_DEFAULT, "name": "Space",
                    "theme": {"type": "gradient", "gradientColors": [], "opacity": 0.5,
                              "texture": 0},
                    "containerTabId": 0, "hasCollapsedPinnedTabs": False}],
    }
    return write_zen_profile(base, containers, session, prefs)


def existing_zen(base, prefs=None):
    """A Zen the user already set up: custom spaces/containers, null icon/theme."""
    identities = copy.deepcopy(DEFAULT_IDENTITIES) + [
        {"userContextId": 6, "public": True, "icon": "tree", "color": "green", "name": "Old"},
        {"userContextId": 7, "public": True, "icon": "circle", "color": "red", "name": "Side"},
    ]
    containers = {"version": 6, "lastUserContextId": 7, "identities": identities,
                  "siteAssociations": {"example.org": {"userContextId": 7}},
                  "someFutureKey": {"keep": True}}
    mine_ph = zen_tab("1700000000000-55555555-5555-4555-8555-555555555555", "about:blank",
                      WS_WORK, group="1700000000000-7", empty=True)
    session = {
        "lastCollected": 1700000000000,
        "tabs": [
            zen_tab("1700000000000-66666666-6666-4666-8666-666666666666",
                    "https://my-mail.example.org/", None, ctx=2, essential=True),
            zen_tab(USER_ID, "https://mine.example.org/", WS_WORK, ctx=2),
            mine_ph,
            zen_tab("1700000000000-77777777-7777-4777-8777-777777777777",
                    "https://mine-in-folder.example.org/", WS_WORK, ctx=2,
                    group="1700000000000-7"),
            zen_tab("1700000000000-88888888-8888-4888-8888-888888888888",
                    "https://open.example.org/", WS_PERSONAL, pinned=False),
            zen_tab("1700000000000-99999999-9999-4999-8999-999999999999",
                    "https://side-pin.example.org/", WS_SIDE, ctx=7),
        ],
        "folders": [zen_folder("1700000000000-7", "Mine", WS_WORK, mine_ph["zenSyncId"])],
        "splitViewData": [],
        "groups": [zen_group("1700000000000-7", "Mine")],
        "spaces": [
            {"uuid": WS_PERSONAL, "name": "personal", "icon": None, "theme": None,
             "containerTabId": 0, "hasCollapsedPinnedTabs": False},
            {"uuid": WS_WORK, "name": "Work", "icon": "⚒",
             "theme": {"type": "gradient", "gradientColors": [], "opacity": 0.5, "texture": 0},
             "containerTabId": 2, "hasCollapsedPinnedTabs": False},
            {"uuid": WS_SIDE, "name": "Side stuff",
             "theme": {"type": "gradient", "gradientColors": [], "opacity": 0.5, "texture": 0},
             "containerTabId": 7, "hasCollapsedPinnedTabs": False},
        ],
    }
    return write_zen_profile(base, containers, session, prefs)


# --------------------------------------------------------------------------- #
class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arc2zen-test-")
        self.arc_dir = os.path.join(self.tmp, "Arc")
        self.zen_dir = os.path.join(self.tmp, "zen")
        standard_arc().write(self.arc_dir, profile_names=PROFILE_NAMES)
        env = {"ARC2ZEN_ARC_DIR": self.arc_dir, "ARC2ZEN_ZEN_DIR": self.zen_dir,
               "ARC2ZEN_ZEN_APP": os.path.join(self.tmp, "NoZen.app")}
        patches = [mock.patch.dict(os.environ, env),
                   mock.patch.object(S, "zen_process_ids", return_value=[]),
                   mock.patch.object(S, "arc_running", return_value=False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_sync(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = S.main(list(args))
        return code, out.getvalue()

    def session(self, profile):
        with open(os.path.join(profile, "zen-sessions.jsonlz4"), "rb") as f:
            return S.mozlz4_decode(f.read())

    def save_session(self, profile, session):
        with open(os.path.join(profile, "zen-sessions.jsonlz4"), "wb") as f:
            f.write(S.mozlz4_encode(session))

    def containers(self, profile):
        with open(os.path.join(profile, "containers.json")) as f:
            return json.load(f)

    def state(self, profile):
        with open(os.path.join(profile, S.STATE_FILE)) as f:
            return json.load(f)

    def space_by_name(self, session, name, n=0):
        return [s for s in session["spaces"] if s["name"] == name][n]

    def snapshot(self, path):
        out = {}
        for p in sorted(glob.glob(os.path.join(path, "**", "*"), recursive=True)):
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    out[p] = (f.read(), os.path.getmtime(p))
        return out


class TestDryRun(SyncTestCase):
    def test_dry_run_writes_nothing(self):
        profile = fresh_zen(self.zen_dir)
        before = self.snapshot(self.zen_dir)
        arc_before = self.snapshot(self.arc_dir)
        code, out = self.run_sync()
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertEqual(before, self.snapshot(self.zen_dir))
        self.assertEqual(arc_before, self.snapshot(self.arc_dir))
        self.assertFalse(os.path.exists(os.path.join(profile, S.STATE_FILE)))

    def test_dump_arc(self):
        code, out = self.run_sync("--dump-arc")
        self.assertEqual(code, 0, out)
        for text in ("Deep/", "Empty/  (empty)", "https://child.example.com/",
                     "welcomeToArc: 1", "splitView: 1", "arc: 1", "'Side Project' (Profile 2)"):
            self.assertIn(text, out)


class TestFreshProfile(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.profile = fresh_zen(self.zen_dir)
        code, self.out = self.run_sync("--apply")
        self.assertEqual(code, 0, self.out)
        self.s = self.session(self.profile)

    def by_url(self, url):
        return next(t for t in self.s["tabs"] if t["entries"][0]["url"] == url)

    def test_spaces_containers_essentials(self):
        s, c = self.s, self.containers(self.profile)
        names = [sp["name"] for sp in s["spaces"]]
        self.assertEqual(names, ["Personal", "Reading", "Work", "Personal", "Space"])
        personal, reading = s["spaces"][0], s["spaces"][1]
        work, personal2 = s["spaces"][2], s["spaces"][3]
        self.assertEqual(personal["containerTabId"], 0)    # Arc default profile: no container
        self.assertEqual(reading["containerTabId"], 0)
        self.assertEqual(work["containerTabId"], 6)
        self.assertEqual(personal2["containerTabId"], 7)
        self.assertEqual(personal["icon"], "\U0001F3E0")
        self.assertEqual(personal["theme"]["gradientColors"][0]["c"], [51, 102, 153])
        self.assertNotIn("icon", reading)                  # named icon, but no omni.ja
        self.assertEqual(c["lastUserContextId"], 7)
        new = {i["userContextId"]: i["name"] for i in c["identities"]
               if i["userContextId"] in (6, 7)}
        self.assertEqual(new, {6: "Work (Arc)", 7: "Side Project"})
        self.assertEqual(c["identities"][:6], DEFAULT_IDENTITIES)
        self.assertEqual(c["siteAssociations"], {})

        ess = [t for t in s["tabs"] if t.get("zenEssential")]
        owned = [t for t in ess if S.is_owned_tab(t["zenSyncId"])]
        self.assertEqual([(t["entries"][0]["url"], t["userContextId"]) for t in owned], [
            ("https://mail.example.com/", 0), ("https://calendar.example.com/", 0),
            ("https://chat.example.com/", 6), ("https://tasks.example.com/", 7)])
        self.assertTrue(all(t["zenWorkspace"] is None for t in owned))
        self.assertTrue(owned[0]["image"].startswith("data:image/png;base64,"))
        self.assertEqual(owned[0]["_zenPinnedInitialState"]["image"], owned[0]["image"])
        self.assertEqual(len(ess), 5)                        # welcome essential kept
        self.assertIn("Hint: Zen's default workspace 'Space'", self.out)

    def test_labels_and_skips(self):
        by_url = {t["entries"][0]["url"]: t for t in self.s["tabs"]}
        self.assertEqual(by_url["https://news.example.com/"]["zenStaticLabel"], "News")
        self.assertNotIn("zenStaticLabel", by_url["https://same.example.com/"])
        self.assertNotIn("arc://settings", by_url)
        self.assertIn("1 non-web tab(s) skipped (arc: 1)", self.out)
        self.assertIn("splitView: 1", self.out)
        self.assertIn("Skipped empty Arc spaces: 'Scratch'", self.out)

    def test_groups_contiguous_placeholders_unique_ids(self):
        s = self.s
        ids = [t["zenSyncId"] for t in s["tabs"]]
        self.assertEqual(len(ids), len(set(ids)))
        folder_ids = [f["id"] for f in s["folders"]]
        self.assertEqual(len(folder_ids), len(set(folder_ids)))
        self.assertEqual({g["id"] for g in s["groups"]}, set(folder_ids))
        owned = [t for t in s["tabs"] if S.is_owned_tab(t["zenSyncId"])]
        for f in s["folders"]:
            if not S.is_owned_folder(f["id"]):
                continue
            run = [i for i, t in enumerate(owned) if t.get("groupId") == f["id"]]
            self.assertEqual(run, list(range(run[0], run[0] + len(run))), f["name"])
            first = owned[run[0]]
            self.assertTrue(first["zenIsEmpty"])
            self.assertEqual(f["emptyTabIds"], [first["zenSyncId"]])
            self.assertEqual(first["entries"][0]["url"], "about:blank")
        empty = next(f for f in s["folders"] if f["name"] == "Empty")
        self.assertEqual(len([t for t in s["tabs"] if t.get("groupId") == empty["id"]]), 1)

    def test_nested_folders_preorder(self):
        folders = [f for f in self.s["folders"] if S.is_owned_folder(f["id"])]
        order = {f["id"]: i for i, f in enumerate(folders)}
        tab_ids = {t["zenSyncId"] for t in self.s["tabs"]}
        by_id = {f["id"]: f for f in folders}
        for f in folders:
            if not f["parentId"]:
                continue
            self.assertLess(order[f["parentId"]], order[f["id"]])
            prev = f["prevSiblingInfo"]
            if prev["type"] == "group":
                self.assertLess(order[prev["id"]], order[f["id"]])
                self.assertEqual(by_id[prev["id"]]["parentId"], f["parentId"])
            elif prev["type"] == "tab":
                self.assertIn(prev["id"], tab_ids)
        tools = next(f for f in folders if f["name"] == "Tools")
        tool_tab = self.by_url("https://tool.example.com/")
        deep = next(f for f in folders if f["name"] == "Deep")
        self.assertEqual(deep["parentId"], tools["id"])
        self.assertEqual(deep["prevSiblingInfo"], {"type": "tab", "id": tool_tab["zenSyncId"]})
        beta = next(f for f in folders if f["name"] == "Beta")
        mid = self.by_url("https://mid.example.com/")
        self.assertEqual(beta["prevSiblingInfo"], {"type": "tab", "id": mid["zenSyncId"]})
        alpha = next(f for f in folders if f["name"] == "Alpha")
        self.assertEqual(alpha["prevSiblingInfo"], {"type": "start", "id": None})

    def test_restore_simulation_matches_arc(self):
        trees, lost = sim.sidebar(self.s)
        self.assertEqual(lost, [])
        personal = self.space_by_name(self.s, "Personal")["uuid"]
        work = self.space_by_name(self.s, "Work")["uuid"]
        reading = self.space_by_name(self.s, "Reading")["uuid"]
        self.assertEqual(trees[(personal, "pinned")], EXPECTED_PERSONAL)
        self.assertEqual(trees[(work, "pinned")], EXPECTED_WORK)
        self.assertEqual(trees[(reading, "pinned")], [
            ("tab", "https://read1.example.com/"),
            ("folder", "Later", [("tab", "https://read2.example.com/")])])
        self.assertEqual(trees[(personal, "open")], [("tab", "https://open1.example.com/"),
                                                     ("tab", "https://open2.example.com/")])
        # The tool's own model of Zen's restore agrees with the independent one
        layout = S.render_layout(self.s)
        pinned = [(e["kind"], e["label"] if e["kind"] == "folder" else e["url"])
                  for e in layout[S.section_key("pinned", work)]]
        self.assertEqual(pinned, [("folder", "Projects"), ("folder", "Alpha"),
                                  ("tab", "https://alpha.example.com/"),
                                  ("tab", "https://mid.example.com/"), ("folder", "Beta"),
                                  ("tab", "https://beta.example.com/"),
                                  ("tab", "https://work.example.com/")])

    def test_idempotent_rerun(self):
        before_sess = self.s
        with open(os.path.join(self.profile, "containers.json"), "rb") as f:
            before_cont = f.read()
        before_state = self.state(self.profile)
        backups = sorted(p for p in os.listdir(self.profile) if ".arc2zen-bak-" in p)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("Already in sync; nothing written.", out)
        self.assertEqual(backups, sorted(p for p in os.listdir(self.profile)
                                         if ".arc2zen-bak-" in p))
        after = self.session(self.profile)
        def own(s):
            return {t["zenSyncId"]: t for t in s["tabs"] if S.is_owned_tab(t["zenSyncId"])}
        self.assertEqual(own(before_sess), own(after))
        self.assertEqual(before_sess, after)
        with open(os.path.join(self.profile, "containers.json"), "rb") as f:
            self.assertEqual(before_cont, f.read())
        after_state = self.state(self.profile)
        before_state.pop("synced_at")
        after_state.pop("synced_at")
        self.assertEqual(before_state, after_state)
        self.assertNotIn("(new)", out)

    def test_zen_resave_is_not_a_change(self):
        # Zen re-saves tabs/folders in DOM order with its own prevSiblingInfo
        resaved = sim.resave(self.s)
        self.assertNotEqual([t["zenSyncId"] for t in resaved["tabs"]],
                            [t["zenSyncId"] for t in self.s["tabs"]])
        self.save_session(self.profile, resaved)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertNotIn("changed in Zen", out)
        self.assertEqual(sim.sidebar(self.session(self.profile)), sim.sidebar(self.s))

    def test_atomic_write_leaves_no_temp_files(self):
        leftovers = [p for p in os.listdir(self.profile) if "tmp" in p]
        self.assertEqual(leftovers, [])
        backups = [p for p in os.listdir(self.profile) if ".arc2zen-bak-" in p]
        self.assertEqual(sorted(b.split(".arc2zen-bak-")[0] for b in backups),
                         ["containers.json", "zen-sessions.jsonlz4"])
        for b in backups:                      # the undo hint names this run's backups
            self.assertIn(b, self.out.split("To undo this run:")[1])
        self.assertIn(f"delete {S.STATE_FILE} (this was the first sync)", self.out)
        path = os.path.join(self.profile, "containers.json")
        with open(path, "rb") as f:
            original = f.read()
        with mock.patch.object(S.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                S.atomic_write(path, b"{}")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), original)
        self.assertEqual([p for p in os.listdir(self.profile) if "tmp" in p], [])

    def test_backup_never_overwrites(self):
        path = os.path.join(self.profile, "containers.json")
        first = S.backup_file(path, "20260101-000000-000001")
        second = S.backup_file(path, "20260101-000000-000001")
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first) and os.path.exists(second))


class TestGuard(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.profile = fresh_zen(self.zen_dir)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.synced = self.session(self.profile)
        self.personal = self.space_by_name(self.synced, "Personal")["uuid"]

    def tab(self, session, url):
        return next(t for t in session["tabs"] if t["entries"][0]["url"] == url)

    def edit_move(self, s):
        empty = next(f for f in s["folders"] if f["name"] == "Empty")
        self.tab(s, "https://docs.example.com/")["groupId"] = empty["id"]

    def edit_rename(self, s):
        self.tab(s, "https://repo.example.com/")["zenStaticLabel"] = "My repo"

    def edit_close(self, s):
        s["tabs"].remove(self.tab(s, "https://read1.example.com/"))

    def edit_add(self, s):
        s["tabs"].append(zen_tab(USER_ID, "https://my-own.example.org/", self.personal))

    def edit_reorder(self, s):
        a = self.tab(s, "https://news.example.com/")
        b = self.tab(s, "https://same.example.com/")
        i, j = s["tabs"].index(a), s["tabs"].index(b)
        s["tabs"][i], s["tabs"][j] = b, a

    def test_refuses_each_edit(self):
        cases = {"moved": self.edit_move, "renamed": self.edit_rename,
                 "closed": self.edit_close, "added by you": self.edit_add,
                 "reordered": self.edit_reorder}
        for word, edit in cases.items():
            with self.subTest(word):
                s = copy.deepcopy(self.synced)
                edit(s)
                self.save_session(self.profile, s)
                with open(os.path.join(self.profile, "zen-sessions.jsonlz4"), "rb") as f:
                    before = f.read()
                for args in ((), ("--apply",)):
                    code, out = self.run_sync(*args)
                    self.assertEqual(code, 1, out)
                    self.assertIn(word, out)
                    self.assertIn("--overwrite-zen-changes", out)
                with open(os.path.join(self.profile, "zen-sessions.jsonlz4"), "rb") as f:
                    self.assertEqual(before, f.read())

    def test_overwrite_keeps_user_items(self):
        s = copy.deepcopy(self.synced)
        for edit in (self.edit_move, self.edit_rename, self.edit_close, self.edit_add):
            edit(s)
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply", "--overwrite-zen-changes")
        self.assertEqual(code, 0, out)
        after = self.session(self.profile)
        trees, lost = sim.sidebar(after)
        self.assertEqual(lost, [])
        self.assertEqual(trees[(self.personal, "pinned")],
                         EXPECTED_PERSONAL + [("tab", "https://my-own.example.org/")])
        self.assertNotIn("zenStaticLabel", self.tab(after, "https://repo.example.com/"))
        self.assertTrue(any(t["entries"][0]["url"] == "https://read1.example.com/"
                            for t in after["tabs"]))
        # and the new state accepts the user's tab as known
        code, out = self.run_sync()
        self.assertEqual(code, 0, out)

    def test_open_tabs(self):
        s = copy.deepcopy(self.synced)
        s["tabs"].remove(self.tab(s, "https://open1.example.com/"))
        s["tabs"].append(zen_tab(USER_ID, "https://my-open.example.org/", self.personal,
                                 pinned=False))
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply")               # closing a synced open tab counts
        self.assertEqual(code, 1, out)
        self.assertIn("closed: https://open1.example.com/", out)
        code, out = self.run_sync("--apply", "--no-open-tabs")   # ...unless open tabs are skipped
        self.assertEqual(code, 0, out)
        urls = [t["entries"][0]["url"] for t in self.session(self.profile)["tabs"]]
        self.assertIn("https://open2.example.com/", urls)         # left alone
        self.assertIn("https://my-open.example.org/", urls)

    def test_reordering_own_items_is_fine(self):
        s = copy.deepcopy(self.synced)
        for n in (1, 2):
            s["tabs"].append(zen_tab(f"{USER_ID[:-1]}{n}", f"https://own{n}.example.org/",
                                     self.personal))
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply", "--overwrite-zen-changes")
        self.assertEqual(code, 0, out)
        s = self.session(self.profile)
        own = [t for t in s["tabs"] if t["entries"][0]["url"].startswith("https://own")]
        i, j = s["tabs"].index(own[0]), s["tabs"].index(own[1])
        s["tabs"][i], s["tabs"][j] = own[1], own[0]        # swap the user's own two tabs
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        trees, _ = sim.sidebar(self.session(self.profile))
        self.assertEqual(trees[(self.personal, "pinned")][-2:],
                         [("tab", "https://own2.example.org/"),
                          ("tab", "https://own1.example.org/")])

    def test_own_tab_inside_synced_folder(self):
        s = copy.deepcopy(self.synced)
        dev = next(f for f in s["folders"] if f["name"] == "Dev")
        mine = zen_tab(USER_ID, "https://in-dev.example.org/", self.personal, group=dev["id"])
        s["tabs"].append(mine)
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 1, out)
        self.assertIn("added by you: https://in-dev.example.org/", out)
        code, out = self.run_sync("--apply", "--overwrite-zen-changes")
        self.assertEqual(code, 0, out)
        trees, _ = sim.sidebar(self.session(self.profile))
        dev_tree = trees[(self.personal, "pinned")][1]
        self.assertEqual(dev_tree[2][-1], ("tab", "https://in-dev.example.org/"))
        code, out = self.run_sync("--apply")                 # accepted from now on
        self.assertEqual(code, 0, out)
        # Arc drops the Dev folder: the user's tab stays, at the top level
        a = standard_arc()
        a.items[a.items.index("s-personal-pinned") + 1]["childrenIds"].remove("f-dev")
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        after = self.session(self.profile)
        trees, lost = sim.sidebar(after)
        self.assertEqual(lost, [])
        self.assertEqual(trees[(self.personal, "pinned")][-1],
                         ("tab", "https://in-dev.example.org/"))
        self.assertFalse(any(f["name"] == "Dev" for f in after["folders"]))
        self.assertNotIn("groupId", self.tab(after, "https://in-dev.example.org/"))

    def test_split_view_is_a_change(self):
        s = copy.deepcopy(self.synced)
        tabs = [self.tab(s, u) for u in ("https://parent.example.com/",
                                          "https://child.example.com/")]
        gid = "1700000000000-99"
        for t in tabs:
            t["groupId"] = gid
        s["groups"].append({"pinned": True, "splitView": True, "id": gid, "name": "",
                            "color": "", "collapsed": False, "saveOnWindowClose": True})
        s["splitViewData"] = [{"groupId": gid, "gridType": "vsep",
                               "tabs": [t["zenSyncId"] for t in tabs]}]
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 1, out)
        self.assertIn("split view: https://parent.example.com/ (put in a split view)", out)
        self.assertEqual([t.get("groupId") for t in tabs],
                         [self.tab(self.session(self.profile), t["entries"][0]["url"])
                          .get("groupId") for t in tabs])

    def test_icon_and_color_changed_in_zen_are_kept(self):
        s = copy.deepcopy(self.synced)
        personal = self.space_by_name(s, "Personal")
        personal["icon"] = None                     # 'None' in Zen's icon picker
        personal["theme"]["gradientColors"] = []
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        after = self.space_by_name(self.session(self.profile), "Personal")
        self.assertIsNone(after["icon"])
        self.assertEqual(after["theme"]["gradientColors"], [])
        # an icon left as synced follows Arc
        a = standard_arc()
        a.spaces[a.spaces.index("s-work") + 1]["customInfo"]["iconType"]["emoji_v2"] = "\u2692"
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        s = self.session(self.profile)
        self.assertEqual(self.space_by_name(s, "Work")["icon"], "\u2692")
        self.assertIsNone(self.space_by_name(s, "Personal")["icon"])

    def test_arc_spaces_missing_keep_their_records(self):
        shutil.rmtree(self.arc_dir)
        ArcFixture().write(self.arc_dir)                 # e.g. signed out of Arc
        with open(os.path.join(self.profile, "zen-sessions.jsonlz4"), "rb") as f:
            before = f.read()
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 1, out)
        self.assertIn("Arc has no spaces right now", out)
        with open(os.path.join(self.profile, "zen-sessions.jsonlz4"), "rb") as f:
            self.assertEqual(before, f.read())
        # Work deleted in Arc: left alone in Zen, and still recorded
        a = standard_arc()
        i = a.spaces.index("s-work")
        del a.spaces[i:i + 2]
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("The Arc space synced into 'Work' is no longer in Arc; its 5 synced tab(s) "
                      "there are left as they are.", out)
        work = self.space_by_name(self.synced, "Work")["uuid"]
        self.assertIn(work, self.state(self.profile)["workspaces"])
        self.assertEqual(sim.sidebar(self.session(self.profile))[0][(work, "pinned")],
                         EXPECTED_WORK)
        # ...so a change made in Zen meanwhile is caught when Work comes back
        s = self.session(self.profile)
        self.tab(s, "https://work.example.com/")["zenStaticLabel"] = "My work"
        self.save_session(self.profile, s)
        shutil.rmtree(self.arc_dir)
        standard_arc().write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 1, out)
        self.assertIn("renamed: https://work.example.com/ -> My work", out)

    def test_saved_workspace_is_not_taken_over(self):
        # Two Arc spaces named "Personal"; the Profile 2 one comes first in Arc
        # and was empty at the first sync
        def arc(p2_tabs):
            a = standard_arc()
            a.top_apps = a.top_apps[:-2]                    # no Profile 2 favorites
            a.items[a.items.index("s-personal2-pinned") + 1]["childrenIds"] = p2_tabs
            i = a.spaces.index("s-personal2")
            a.spaces = a.spaces[i:i + 2] + a.spaces[:i] + a.spaces[i + 2:]
            return a
        shutil.rmtree(self.zen_dir)
        shutil.rmtree(self.arc_dir)
        arc([]).write(self.arc_dir, profile_names=PROFILE_NAMES)
        profile = fresh_zen(self.zen_dir)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        before = self.session(profile)
        personal = self.space_by_name(before, "Personal")
        shutil.rmtree(self.arc_dir)
        arc(["t-p2"]).write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        after = self.session(profile)
        trees, _ = sim.sidebar(after)
        self.assertEqual(trees[(personal["uuid"], "pinned")], EXPECTED_PERSONAL)
        self.assertEqual(self.space_by_name(after, "Personal")["containerTabId"],
                         personal["containerTabId"])
        other = [sp for sp in after["spaces"] if sp["name"] == "Personal"
                 and sp["uuid"] != personal["uuid"]]
        self.assertEqual(trees[(other[0]["uuid"], "pinned")],
                         [("tab", "https://side.example.com/")])
        self.assertEqual(self.state(profile)["spaces"]["s-personal"], personal["uuid"])

    def test_unknown_map_name(self):
        code, out = self.run_sync("--map", "Wrok=Somewhere")
        self.assertEqual(code, 0, out)
        self.assertIn("--map names Arc space(s) that don't exist: 'Wrok'", out)
        with open(os.path.join(self.profile, "zen-sessions.jsonlz4"), "rb") as f:
            before = f.read()
        code, out = self.run_sync("--apply", "--map", "Wrok=Somewhere")
        self.assertEqual(code, 1, out)
        self.assertIn("Nothing was written", out)
        with open(os.path.join(self.profile, "zen-sessions.jsonlz4"), "rb") as f:
            self.assertEqual(before, f.read())

    def test_arc_changes_flow_through(self):
        a = standard_arc()
        # drop a tab from Arc and add a new one
        idx = a.items.index("s-work-pinned")
        a.items[idx + 1]["childrenIds"] = ["t-w1", "f-proj"]
        a.tab("t-new", "https://new.example.com/")
        a.items[a.items.index("s-reading-pinned") + 1]["childrenIds"] = ["t-new", "f-later"]
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("synced tab(s) no longer in Arc, removed", out)
        after = self.session(self.profile)
        trees, _ = sim.sidebar(after)
        work = self.space_by_name(after, "Work")["uuid"]
        reading = self.space_by_name(after, "Reading")["uuid"]
        self.assertEqual(trees[(work, "pinned")], [EXPECTED_WORK[1], EXPECTED_WORK[0]])
        self.assertEqual(trees[(reading, "pinned")], [
            ("tab", "https://new.example.com/"),
            ("folder", "Later", [("tab", "https://read2.example.com/")])])


class TestExistingProfile(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.profile = existing_zen(self.zen_dir)

    def test_first_sync_keeps_existing_items(self):
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        s, c = self.session(self.profile), self.containers(self.profile)
        spaces = {sp["uuid"]: sp for sp in s["spaces"]}
        # case-insensitive match; Arc default profile keeps container 0
        self.assertEqual(spaces[WS_PERSONAL]["name"], "personal")
        self.assertEqual(spaces[WS_PERSONAL]["containerTabId"], 0)
        self.assertEqual(spaces[WS_PERSONAL]["icon"], "\U0001F3E0")
        self.assertIsInstance(spaces[WS_PERSONAL]["theme"], dict)
        self.assertEqual(spaces[WS_WORK]["containerTabId"], 2)   # reused, no new container
        self.assertEqual(spaces[WS_SIDE]["containerTabId"], 7)   # untouched
        self.assertEqual(len(s["spaces"]), 5)
        new = [i for i in c["identities"] if i["userContextId"] > 7 and i["userContextId"] < 99]
        self.assertEqual([(i["userContextId"], i["name"]) for i in new], [(8, "Side Project")])
        self.assertEqual(c["someFutureKey"], {"keep": True})
        self.assertEqual(c["siteAssociations"], {"example.org": {"userContextId": 7}})
        urls = [t["entries"][0]["url"] for t in s["tabs"]]
        for mine in ("https://mine.example.org/", "https://mine-in-folder.example.org/",
                     "https://my-mail.example.org/", "https://open.example.org/",
                     "https://side-pin.example.org/"):
            self.assertIn(mine, urls)
        trees, lost = sim.sidebar(s)
        self.assertEqual(lost, [])
        self.assertEqual(trees[(WS_WORK, "pinned")], EXPECTED_WORK + [
            ("tab", "https://mine.example.org/"),
            ("folder", "Mine", [("tab", "https://mine-in-folder.example.org/")])])
        self.assertEqual(trees[("essentials", 2)][-1], ("tab", "https://my-mail.example.org/"))
        self.assertIn("--replace-existing", out)
        self.assertNotIn("'Old' is no longer used", out)      # never used by a workspace
        self.assertIn("! icon \u2692 -> \U0001F4BC", out)       # 'Work' had its own icon

    def test_map_survives_rename_and_remap_needs_confirmation(self):
        code, out = self.run_sync("--apply", "--map", "Work=Side stuff")
        self.assertEqual(code, 0, out)
        s = self.session(self.profile)
        spaces = {sp["uuid"]: sp for sp in s["spaces"]}
        spaces[WS_SIDE]["name"] = "Clients"              # renamed in Zen
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply", "--map", "Work=Side stuff")
        self.assertEqual(code, 0, out)
        self.assertIn("'Clients' <- Arc 'Work'", out)
        trees, _ = sim.sidebar(self.session(self.profile))
        self.assertEqual(trees[(WS_SIDE, "pinned")][:2], EXPECTED_WORK)
        # a different --map would move the synced items: needs confirmation
        code, out = self.run_sync("--apply", "--map", "Work=Brand New")
        self.assertEqual(code, 1, out)
        self.assertIn("Arc 'Work' was synced into 'Clients'; --map now puts it in 'Brand New'",
                      out)
        code, out = self.run_sync("--apply", "--map", "Work=Brand New",
                                  "--overwrite-zen-changes")
        self.assertEqual(code, 0, out)
        s = self.session(self.profile)
        trees, _ = sim.sidebar(s)
        brand = self.space_by_name(s, "Brand New")["uuid"]
        self.assertEqual(trees[(brand, "pinned")], EXPECTED_WORK)
        self.assertEqual(trees[(WS_SIDE, "pinned")], [("tab", "https://side-pin.example.org/")])
        # and --map onto a workspace another Arc space holds is an error
        code, out = self.run_sync("--map", "Work=Reading")
        self.assertEqual(code, 1, out)
        self.assertIn("already holds Arc space 'Reading'", out)

    def test_new_arc_space_does_not_adopt_your_workspace(self):
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        a = standard_arc()
        a.tab("t-new", "https://new-space.example.com/")
        a.space("s-new", "Side stuff", "Default", ["t-new"], [])
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        # asking for it with --map is fine, but moving its container needs confirmation
        code, out = self.run_sync("--map", "Side stuff=Side stuff")
        self.assertEqual(code, 1, out)
        self.assertIn("'Side stuff' wasn't synced into before", out)
        self.assertIn("from container 7 'Side' to 0 (no container)", out)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertIn("gets a new workspace even though Zen has one named 'Side stuff'", out)
        s = self.session(self.profile)
        spaces = {sp["uuid"]: sp for sp in s["spaces"]}
        self.assertEqual(spaces[WS_SIDE]["containerTabId"], 7)
        trees, _ = sim.sidebar(s)
        self.assertEqual(trees[(WS_SIDE, "pinned")], [("tab", "https://side-pin.example.org/")])
        self.assertEqual(len([sp for sp in s["spaces"] if sp["name"] == "Side stuff"]), 2)

    def test_earlier_migration_is_not_duplicated_silently(self):
        s = self.session(self.profile)
        # an earlier tool pinned an Arc URL here, with its own id
        s["tabs"].append(zen_tab("1700000000000-abababab-abab-4bab-8bab-abababababab",
                                 "https://work.example.com/", WS_WORK, ctx=2))
        self.save_session(self.profile, s)
        for args in ((), ("--apply",)):
            code, out = self.run_sync(*args)
            self.assertEqual(code, 1, out)
            self.assertIn("look like an earlier migration", out)
            self.assertIn("--keep-existing", out)
        self.assertFalse(os.path.exists(os.path.join(self.profile, S.STATE_FILE)))
        code, out = self.run_sync("--apply", "--keep-existing")
        self.assertEqual(code, 0, out)
        urls = [t["entries"][0]["url"] for t in self.session(self.profile)["tabs"]]
        self.assertEqual(urls.count("https://work.example.com/"), 2)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            S.parse_args(["--replace-existing", "--keep-existing"])

    def test_other_arc2zen_ids_are_not_owned(self):
        # inject_session_tabs.py gave its folder placeholders "arc2zen-ph-<n>" ids
        s = self.session(self.profile)
        ph = next(t for t in s["tabs"] if t.get("zenIsEmpty"))
        ph["zenSyncId"] = "arc2zen-ph-12345"
        s["folders"][0]["emptyTabIds"] = [ph["zenSyncId"]]
        self.save_session(self.profile, s)
        self.assertFalse(S.is_owned_tab("arc2zen-ph-12345"))
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertNotIn("has arc2zen items", out)
        after = self.session(self.profile)
        self.assertIn("arc2zen-ph-12345", [t["zenSyncId"] for t in after["tabs"]])
        trees, lost = sim.sidebar(after)
        self.assertEqual(lost, [])
        self.assertEqual(trees[(WS_WORK, "pinned")][-1],
                         ("folder", "Mine", [("tab", "https://mine-in-folder.example.org/")]))

    def test_replace_existing_first_sync_only(self):
        code, out = self.run_sync("--apply", "--replace-existing")
        self.assertEqual(code, 0, out)
        s = self.session(self.profile)
        urls = [t["entries"][0]["url"] for t in s["tabs"]]
        for gone in ("https://mine.example.org/", "https://mine-in-folder.example.org/",
                     "https://my-mail.example.org/", "https://open.example.org/"):
            self.assertNotIn(gone, urls)
        self.assertIn("https://side-pin.example.org/", urls)     # unmatched workspace
        self.assertEqual([f["name"] for f in s["folders"] if not S.is_owned_folder(f["id"])], [])
        self.assertIn("pre-existing tab(s) removed (--replace-existing)", out)
        # later runs ignore the flag and keep what the user adds
        s["tabs"].append(zen_tab(USER_ID, "https://later.example.org/", WS_WORK, ctx=2,
                                 pinned=False))
        self.save_session(self.profile, s)
        code, out = self.run_sync("--apply", "--replace-existing")
        self.assertEqual(code, 0, out)
        self.assertIn("only applies to the first sync", out)
        self.assertIn("https://later.example.org/",
                      [t["entries"][0]["url"] for t in self.session(self.profile)["tabs"]])

    def test_identities_never_deleted(self):
        s = self.session(self.profile)
        s["tabs"].insert(0, zen_tab("1700000000000-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                                    "https://side-mail.example.org/", None, ctx=7,
                                    essential=True))
        self.save_session(self.profile, s)
        before = self.containers(self.profile)["identities"]
        code, out = self.run_sync("--apply", "--map", "Reading=Side stuff")
        self.assertEqual(code, 0, out)
        after = self.containers(self.profile)["identities"]
        self.assertEqual(after[:len(before)], before)
        # 'Side stuff' now shares the Arc default profile's container 0 ...
        s = self.session(self.profile)
        self.assertEqual({sp["uuid"]: sp["containerTabId"] for sp in s["spaces"]}[WS_SIDE], 0)
        # ... and its old container is reported, not removed
        self.assertIn("container 7 'Side' is no longer used", out)
        self.assertIn("with its cookies and 1 Essential(s), hidden now", out)
        self.assertIn("was container 7", out)

    def test_map_and_case_insensitive_match(self):
        code, out = self.run_sync("--map", "reading=SIDE STUFF")
        self.assertEqual(code, 0, out)
        self.assertIn("'Side stuff' <- Arc 'Reading'", out)
        self.assertIn("matched by --map", out)
        self.assertIn("'personal' <- Arc 'Personal'", out)
        self.assertIn("matched by name", out)
        code, out = self.run_sync("--map", "Reading")
        self.assertEqual(code, 1)
        self.assertIn("needs the form", out)

    def test_no_container_churn_on_rerun(self):
        code, _ = self.run_sync("--apply")
        self.assertEqual(code, 0)
        first = self.containers(self.profile)
        state = self.state(self.profile)
        self.assertEqual(state["arc_profiles"], {"Default": 0, "Profile 1": 2, "Profile 2": 8})
        # Arc gains a space above the others on Profile 2: containers stay put
        a = standard_arc()
        a.tab("t-x", "https://extra.example.com/")
        a.space("s-extra", "Extra", "Profile 2", ["t-x"], [])
        a.spaces = a.spaces[-2:] + a.spaces[:-2]
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.containers(self.profile), first)
        self.assertEqual(self.state(self.profile)["arc_profiles"]["Profile 2"], 8)


class TestPrefsAndErrors(SyncTestCase):
    def test_missing_containers_json(self):
        profile = fresh_zen(self.zen_dir)
        os.remove(os.path.join(profile, "containers.json"))
        code, out = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn("containers.json not found", out)
        self.assertIn("Launch Zen once", out)

    def test_missing_session(self):
        profile = fresh_zen(self.zen_dir)
        os.remove(os.path.join(profile, "zen-sessions.jsonlz4"))
        code, out = self.run_sync("--profile", profile)
        self.assertEqual(code, 1)
        self.assertIn("zen-sessions.jsonlz4 not found", out)

    def test_separate_essentials_false_warns(self):
        fresh_zen(self.zen_dir, prefs={"zen.workspaces.separate-essentials": False})
        code, out = self.run_sync()
        self.assertEqual(code, 0, out)
        self.assertIn("separate-essentials is false", out)
        self.assertIn('user_pref("zen.workspaces.separate-essentials", true);', out)

    def test_window_sync_disabled_aborts(self):
        fresh_zen(self.zen_dir, prefs={"zen.window-sync.enabled": False})
        code, out = self.run_sync()
        self.assertEqual(code, 1)
        self.assertIn("zen.window-sync.enabled is false", out)

    def test_open_tabs_not_restored_warning(self):
        fresh_zen(self.zen_dir, prefs={"browser.startup.page": 1})
        code, out = self.run_sync()
        self.assertEqual(code, 0, out)
        self.assertIn("browser.startup.page is 1", out)
        code, out = self.run_sync("--no-open-tabs")
        self.assertNotIn("browser.startup.page", out)
        self.assertIn("open tabs skipped", out)

    def test_other_zen_version_warns(self):
        profile = fresh_zen(self.zen_dir)
        ini = os.path.join(profile, "compatibility.ini")
        for last, warned in (("1.22.3b_20260101000000/20260101000000", False),
                             ("1.20.1b_20250101000000/20250101000000", True)):
            with open(ini, "w") as f:
                f.write(f"[Compatibility]\nLastVersion={last}\nLastOSABI=Darwin_aarch64\n")
            code, out = self.run_sync()
            self.assertEqual(code, 0, out)
            self.assertEqual(f"last opened by Zen {last.split('_')[0]}." in out, warned, out)

    def test_refuses_when_running(self):
        profile = fresh_zen(self.zen_dir)
        with mock.patch.object(S, "zen_process_ids", return_value=[4242]):
            code, out = self.run_sync("--apply")
            self.assertEqual(code, 1)
            self.assertIn("Zen appears to be running", out)
            self.assertFalse(os.path.exists(os.path.join(profile, S.STATE_FILE)))
            code, out = self.run_sync()                      # dry run still works
            self.assertEqual(code, 0, out)
            code, out = self.run_sync("--apply", "--force-running")
            self.assertEqual(code, 0, out)       # only a process was seen, the profile is free
            self.assertIn("this profile isn't locked", out)

    @unittest.skipUnless(sys.platform in ("darwin", "linux"), "needs POSIX record locks")
    def test_profile_lock_detection(self):
        profile = fresh_zen(self.zen_dir)
        lock = os.path.join(profile, ".parentlock")
        open(lock, "w").close()
        self.assertFalse(S.zen_profile_locked(profile))
        holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
            import fcntl, os, sys, time
            fd = os.open({lock!r}, os.O_WRONLY)
            fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)   # a POSIX lock, like Firefox
            print("locked", flush=True)
            time.sleep(30)
            """)], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            self.assertTrue(S.zen_profile_locked(profile))
            code, out = self.run_sync()                      # dry run only warns
            self.assertEqual(code, 0, out)
            self.assertIn("open in a running Zen", out)
            # A held lock means Zen has this profile open: --force-running can't override it
            for args in (("--apply",), ("--apply", "--force-running")):
                code, out = self.run_sync(*args)
                self.assertEqual(code, 1, out)
                self.assertIn("open in a running Zen", out)
                self.assertNotIn("pass --force-running", out)
            self.assertFalse(os.path.exists(os.path.join(profile, S.STATE_FILE)))
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()
        self.assertFalse(S.zen_profile_locked(profile))
        self.assertEqual(os.path.getsize(lock), 0)

    def test_profile_discovery_picks_newest(self):
        base = os.path.join(self.tmp, "multi")
        for name in ("a.Release", "b.Twilight"):
            os.makedirs(os.path.join(base, "Profiles", name))
        with open(os.path.join(base, "profiles.ini"), "w") as f:
            f.write("[InstallAAA]\nDefault=Profiles/a.Release\n\n"
                    "[InstallBBB]\nDefault=Profiles/b.Twilight\n\n"
                    "[Profile0]\nName=50%off\nPath=Profiles/a.Release\n")
        for name, age in (("a.Release", 100), ("b.Twilight", 10)):
            p = os.path.join(base, "Profiles", name, "zen-sessions.jsonlz4")
            open(p, "w").close()
            os.utime(p, (time.time() - age, time.time() - age))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            found = S.find_zen_profile(base)
        self.assertEqual(os.path.basename(found), "b.Twilight")
        self.assertIn("a.Release", out.getvalue())

    def test_arc_sidebar_not_at_index_one(self):
        standard_arc().write(self.arc_dir, sidebar_index=3, profile_names=PROFILE_NAMES)
        arc = S.ArcData(self.arc_dir)
        self.assertEqual([sp.name for sp in arc.spaces],
                         ["Personal", "Reading", "Work", "Personal", "Scratch"])
        self.assertEqual(S.count_nodes(arc.spaces[0].pinned), (10, 4))


class TestArcAndStateFiles(SyncTestCase):
    def test_odd_arc_data_is_skipped_not_fatal(self):
        a = ArcFixture()
        a.tab("t-ok", "https://ok.example.com/")
        a.tab("t-bad", "http://[::1")                    # urlsplit raises ValueError
        a.item("t-int", 5, {"tab": {"savedURL": "https://int.example.com/", "savedTitle": 7}})
        a.item("f-int", 3, {"list": {}}, ["t-int"])
        a.space("s-1", "Personal", "Default", ["t-ok", "t-bad", "f-int"], [])
        a.spaces.append("s-2")
        a.spaces.append({"id": "s-2", "title": 9, "containerIDs": {"x": 1}})
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir)
        fresh_zen(self.zen_dir)
        code, out = self.run_sync("--dump-arc")
        self.assertEqual(code, 0, out)
        self.assertIn("1 non-web tab(s) skipped (invalid URL: 1)", out)
        self.assertIn("+ Folder/", out)
        self.assertIn("'Arc Space 2'", out)
        self.assertEqual(out.count("\n\nNote:"), 1)
        code, out = self.run_sync("--apply")
        self.assertEqual(code, 0, out)
        urls = [t["entries"][0]["url"] for t in self.session(
            os.path.join(self.zen_dir, "Profiles", "abcd1234.Default (release)"))["tabs"]]
        self.assertIn("https://int.example.com/", urls)
        self.assertNotIn("http://[::1", urls)

    def test_no_empty_essentials_heading(self):
        profile = fresh_zen(self.zen_dir)
        s = self.session(profile)
        s["tabs"] = [t for t in s["tabs"] if not t.get("zenEssential")]
        self.save_session(profile, s)
        a = standard_arc()
        a.top_apps = []
        shutil.rmtree(self.arc_dir)
        a.write(self.arc_dir, profile_names=PROFILE_NAMES)
        code, out = self.run_sync()
        self.assertEqual(code, 0, out)
        self.assertNotIn("Essentials (", out)

    def test_malformed_state_file(self):
        profile = fresh_zen(self.zen_dir)
        for state in ({"version": 1, "layout": {"pinned": []}},
                      {"version": "1", "layout": {}},
                      {"version": 1, "layout": {"pinned {x}": [1]}},
                      {"version": 1, "layout": {}, "spaces": {"a": ["b"]}},
                      [], {"layout": {}, "workspaces": {"x": 1}}):
            with self.subTest(state=state):
                with open(os.path.join(profile, S.STATE_FILE), "w") as f:
                    json.dump(state, f)
                code, out = self.run_sync()
                self.assertEqual(code, 1, out)
                self.assertIn("is not a valid arc2zen state file", out)
                self.assertNotIn("Traceback", out)


class TestExtensions(SyncTestCase):
    XPI = b"PK\x03\x04 fake xpi"

    def setUp(self):
        super().setUp()
        standard_arc().write(self.arc_dir, profile_names=PROFILE_NAMES, extensions={
            "Default": {"eimadpbcbfnmbkopoojfekhnkhdbieeh": {"location": 1,
                                                            "manifest": {"name": "Dark Reader"}},
                        "abcdefghijklmnopabcdefghijklmnop": {"location": 1,
                                                            "manifest": {"name": "Obscure Tool"}},
                        "bcjindcccaagfpapjjmafapmmgkkhgoa": {"location": 1,
                                                            "manifest": {"name": "JSON Formatter"}},
                        "mhjfbmdgcfjbbpaeojofohoefgiehjai": {"location": 5,
                                                            "manifest": {"name": "Built-in"}}},
            "Profile 1": {"pkehgijcmpdhfbdbbnkijodmdjhbjlgp": {"location": 1,
                                                              "manifest": {"name": "Badger"}}},
        })
        self.profile = fresh_zen(self.zen_dir)

    def fake_amo(self, good_hash=True):
        digest = hashlib.sha256(self.XPI).hexdigest() if good_hash else "0" * 64

        def addon(slug):
            if slug == "privacy-badger17":
                raise OSError("network down")
            return {"guid": f"{slug}@example", "name": {"en-US": slug.title()},
                    "current_version": {"version": "1.0", "file": {
                        "url": f"https://files.example/{slug}.xpi", "size": len(self.XPI),
                        "hash": f"sha256:{digest}"}}}

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return addon, (lambda url, timeout=60: Resp(self.XPI))

    def test_dry_run_and_install(self):
        addon, opener = self.fake_amo()
        with mock.patch.object(S, "amo_addon", addon), mock.patch.object(S, "_http_open", opener):
            code, out = self.run_sync("--skip-tabs", "--extensions")
            self.assertEqual(code, 1, out)                   # one slug failed
            self.assertIn("would install      Darkreader", out)
            self.assertIn("failed             privacy-badger17", out)
            self.assertIn("No known Firefox version for 1 Arc extension(s): Obscure Tool", out)
            self.assertIn("skipped            JSON Formatter (Firefox has a JSON viewer", out)
            self.assertNotIn("Built-in", out)
            self.assertFalse(os.path.exists(os.path.join(self.profile, "extensions")))
            code, out = self.run_sync("--skip-tabs", "--apply", "--extensions", "darkreader")
            self.assertEqual(code, 0, out)
            xpi = os.path.join(self.profile, "extensions", "darkreader@example.xpi")
            with open(xpi, "rb") as f:
                self.assertEqual(f.read(), self.XPI)
            self.assertIn("click Enable", out)

    def test_ublock_origin_replaces_lite(self):
        # Arc disabled uBlock Origin (Manifest V2) and has uBO Lite; Zen needs only uBO
        shutil.rmtree(self.arc_dir)
        standard_arc().write(self.arc_dir, profile_names=PROFILE_NAMES, extensions={
            "Default": {"cjpalhdlnbpafiamejdnhcphjbkeiagm": {
                            "location": 1, "manifest": {"name": "uBlock Origin"},
                            "disable_reasons": [8388608]},
                        "ddkjiahejlhfcafbddmgiahcphecmpfh": {
                            "location": 1, "manifest": {"name": "uBlock Origin Lite"}}}})
        addon, opener = self.fake_amo()
        with mock.patch.object(S, "amo_addon", addon), mock.patch.object(S, "_http_open", opener):
            code, out = self.run_sync("--skip-tabs", "--extensions")
            self.assertEqual(code, 0, out)
            self.assertIn("would install      Ublock-Origin 1.0", out)
            self.assertNotIn("Ublock-Origin-Lite", out)
            self.assertIn("skipped            uBlock Origin Lite (uBlock Origin covers it", out)
            code, out = self.run_sync("--skip-tabs", "--extensions",
                                      "ublock-origin", "ublock-origin-lite")
            self.assertEqual(code, 0, out)
            self.assertIn("would install      Ublock-Origin-Lite", out)   # asked for by name

    def test_extension_tables(self):
        for ext_id in list(S.EXTENSION_MAP) + list(S.EXTENSION_NOTES):
            self.assertRegex(ext_id, r"^[a-p]{32}$")      # Chrome extension id alphabet
        self.assertFalse(set(S.EXTENSION_MAP) & set(S.EXTENSION_NOTES))
        for slug in S.EXTENSION_MAP.values():
            self.assertRegex(slug, r"^[\w.-]+$")

    def test_checksum_mismatch_discards(self):
        addon, opener = self.fake_amo(good_hash=False)
        with mock.patch.object(S, "amo_addon", addon), mock.patch.object(S, "_http_open", opener):
            code, out = self.run_sync("--skip-tabs", "--apply", "--extensions", "darkreader")
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch", out)
        self.assertEqual(os.listdir(os.path.join(self.profile, "extensions")), [])


class TestMozLz4(unittest.TestCase):
    def test_roundtrip_and_bad_magic(self):
        data = {"tabs": [{"zenSyncId": "arc2zen-x", "title": "é\U0001F3E0"}]}
        raw = S.mozlz4_encode(data)
        self.assertEqual(raw[:8], b"mozLz40\0")
        self.assertEqual(S.mozlz4_decode(raw), data)
        with self.assertRaises(S.SyncError):
            S.mozlz4_decode(b"notmozlz4data")

    def test_ids_are_stable_and_prefixed(self):
        self.assertEqual(S.tab_sync_id("abc"), S.tab_sync_id("abc"))
        self.assertTrue(S.is_owned_tab(S.tab_sync_id("abc")))
        self.assertTrue(S.is_owned_folder(S.folder_sync_id("abc")))
        self.assertFalse(S.is_owned_tab(S.folder_sync_id("abc")))
        self.assertFalse(S.is_owned_tab(USER_ID))
        self.assertFalse(S.is_owned_folder(S.tab_sync_id("abc")))
        self.assertFalse(S.is_owned_tab(S.tab_sync_id("abc") + "-copy"))
        self.assertNotEqual(S.tab_sync_id("abc"), S.tab_sync_id("abc", "placeholder"))



class TestNewIdentity(unittest.TestCase):
    def test_skips_reserved_last_user_context_id(self):
        # Older arc2zen runs could leave lastUserContextId on webextStorageLocal's id
        containers = {"lastUserContextId": 4294967295,
                      "identities": copy.deepcopy(DEFAULT_IDENTITIES)}
        identity = S.new_identity(containers, "Example", 0)
        self.assertEqual(identity["userContextId"], 6)
        self.assertEqual(containers["lastUserContextId"], 6)

    def test_keeps_counter_above_removed_ids(self):
        containers = {"lastUserContextId": 9, "identities": copy.deepcopy(DEFAULT_IDENTITIES)}
        self.assertEqual(S.new_identity(containers, "Example", 0)["userContextId"], 10)

    def test_no_free_id_left(self):
        identities = copy.deepcopy(DEFAULT_IDENTITIES) + [
            {"userContextId": 4294967294, "public": True, "name": "Example"}]
        with self.assertRaises(S.SyncError):
            S.new_identity({"lastUserContextId": 5, "identities": identities}, "Other", 0)
