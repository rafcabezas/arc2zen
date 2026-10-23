"""A stand-alone model of how Zen 1.22 restores (and re-saves) its sidebar.

Kept separate from sync_arc_to_zen.render_layout on purpose, so the tests
check the sync's output against an independent implementation of:
  * Tabbrowser.createTabsForSessionRestore: a tab group node is created where
    its first tab appears; later tabs with that groupId are appended to it;
    an empty (zenIsEmpty) tab stays first in its group.
  * ZenFolders.restoreDataFromSessionStore: each folder replaces its group
    node; then, in folders-array order, a folder with parentId moves after
    the element named by prevSiblingInfo, or after its parent's first child.
  * ZenFolders.storeDataForSessionStore: folders saved in DOM order with the
    previous element sibling as prevSiblingInfo.
"""


class Node:
    def __init__(self, kind, node_id, data=None):
        self.kind, self.id, self.data = kind, node_id, data
        self.children = []
        self.parent = None


def detach(node):
    if node.parent is not None:
        node.parent.children.remove(node)
        node.parent = None


def append(parent, node):
    detach(node)
    parent.children.append(node)
    node.parent = parent


def insert_after(item, node):
    detach(node)
    parent = item.parent
    parent.children.insert(parent.children.index(item) + 1, node)
    node.parent = parent


def restore(session):
    """Returns (root node, names of folders that could not be restored)."""
    root = Node("root", None)
    groups = {g["id"] for g in session.get("groups", [])}
    group_nodes, by_id = {}, {}
    for tab in session["tabs"]:
        node = Node("tab", tab.get("zenSyncId"), tab)
        by_id[node.id] = node
        gid = tab.get("groupId")
        if gid and gid in groups:
            if gid not in group_nodes:
                group_nodes[gid] = Node("group", gid)
                append(root, group_nodes[gid])
            append(group_nodes[gid], node)
            if tab.get("zenIsEmpty"):
                group_nodes[gid].children.remove(node)
                group_nodes[gid].children.insert(0, node)
        else:
            append(root, node)
    lost, work = [], {}
    for folder in session.get("folders", []):
        old = group_nodes.get(folder["id"])
        if old is None:
            lost.append(folder["name"])
            continue
        node = Node("folder", folder["id"], folder)
        parent = old.parent
        parent.children.insert(parent.children.index(old), node)
        node.parent = parent
        for child in list(old.children):
            append(node, child)
        detach(old)
        work[folder["id"]] = by_id[folder["id"]] = node
    for folder in session.get("folders", []):
        node = work.get(folder["id"])
        if node is None or not folder.get("parentId"):
            continue
        parent = work.get(folder["parentId"])
        if parent is None:
            continue
        prev = folder.get("prevSiblingInfo") or {}
        if prev.get("type") in ("tab", "group") and prev.get("id") in by_id:
            insert_after(by_id[prev["id"]], node)
        elif parent.children and parent.children[0] is not node:
            insert_after(parent.children[0], node)
        else:
            append(parent, node)
    return root, lost


def _tree(node):
    out = []
    for child in node.children:
        if child.kind == "tab":
            if child.data.get("zenIsEmpty"):
                continue
            out.append(("tab", child.data["entries"][-1]["url"]))
        else:
            out.append(("folder", child.data["name"], _tree(child)))
    return out


def _first_tab(node):
    if node.kind == "tab":
        return node.data
    for child in node.children:
        found = _first_tab(child)
        if found:
            return found
    return None


def sidebar(session):
    """{(workspace, 'pinned'|'open') | ('essentials', ctx): tree} plus lost folders."""
    root, lost = restore(session)
    out = {}
    for top in root.children:
        tab = _first_tab(top)
        if tab is None:
            continue
        if tab.get("zenEssential"):
            key = ("essentials", tab.get("userContextId") or 0)
        elif top.kind == "folder":
            key = (top.data.get("workspaceId"), "pinned")
        else:
            key = (tab.get("zenWorkspace"), "pinned" if tab.get("pinned") else "open")
        if top.kind == "tab":
            if tab.get("zenIsEmpty"):
                continue
            out.setdefault(key, []).append(("tab", tab["entries"][-1]["url"]))
        else:
            out.setdefault(key, []).append(("folder", top.data["name"], _tree(top)))
    return out, lost


def resave(session):
    """What Zen writes back after restoring `session`: tabs and folders in DOM
    order (essentials, then each workspace's pinned and open tabs), folders
    with prevSiblingInfo taken from their previous element sibling."""
    root, _ = restore(session)
    order = []
    for top in root.children:
        tab = _first_tab(top) or {}
        if tab.get("zenEssential"):
            rank = (0, tab.get("userContextId") or 0, "")
        else:
            rank = (1 if tab.get("pinned") or top.kind == "folder" else 2, 0,
                    str(tab.get("zenWorkspace")))
        order.append((rank, len(order), top))
    order.sort(key=lambda r: (r[0][0], r[0][1], r[0][2], r[1]))

    tabs, folders = [], []

    def walk(node, parent_folder):
        if node.kind == "tab":
            tabs.append(dict(node.data))
            return
        folder = dict(node.data)
        siblings = node.parent.children
        i = siblings.index(node)
        if parent_folder is None:
            prev_info = folder.get("prevSiblingInfo")
        elif i == 0:
            prev_info = None
        else:
            prev = siblings[i - 1]
            prev_info = ({"type": "group", "id": prev.id} if prev.kind == "folder"
                         else {"type": "tab", "id": prev.id})
        folder["prevSiblingInfo"] = prev_info
        folder["parentId"] = parent_folder
        folders.append(folder)
        for child in node.children:
            walk(child, node.id)

    for _, _, top in order:
        walk(top, None)
    out = dict(session)
    out["tabs"], out["folders"] = tabs, folders
    return out
