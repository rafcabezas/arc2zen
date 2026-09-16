import inject_session_tabs


def test_placeholder_is_linked_to_empty_folder():
    tabs = []
    folder = {
        "id": "folder-1",
        "workspaceId": "workspace-1",
        "emptyTabIds": [],
    }

    inject_session_tabs.add_session_placeholder_tab(tabs, folder)

    assert len(tabs) == 1
    assert tabs[0]["groupId"] == folder["id"]
    assert folder["emptyTabIds"] == [tabs[0]["zenSyncId"]]
