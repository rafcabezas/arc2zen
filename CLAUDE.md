# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python-based migration tool that converts Arc browser spaces and pinned tabs into Zen browser workspaces. The tool focuses on preserving organizational structure while adapting to Zen's different architecture.

## Common Commands

### Migration Commands

```bash
# Basic migration
python3 migrate_arc_to_zen.py

# Safe testing (recommended before real migration)
python3 migrate_arc_to_zen.py --dry-run

# Verbose logging for debugging
python3 migrate_arc_to_zen.py --verbose

# Inject Arc's open (unpinned) tabs — Zen must be fully quit
# (writes BOTH zen-sessions.jsonlz4 and sessionstore.jsonlz4, zenSyncId-matched)
python3 inject_open_tabs.py --dry-run
python3 inject_open_tabs.py
```

### Component Testing

```bash
# Test Arc data extraction
python3 src/arc_pinned_tab_extractor.py

# Test workspace mapping utilities
python3 src/zen_workspace_mapper.py
```

## Architecture Overview

### Migration Pipeline (4-Step Process)

1. **Arc Data Extraction** - Extract from `~/Library/Application Support/Arc/StorableSidebar.json`
2. **Zen Profile Discovery** - Locate Zen profiles in `~/Library/Application Support/zen/Profiles/`
3. **Container/Workspace Creation** - Create Zen containers and workspaces for each Arc space
4. **Database Import** - Import metadata to zen_pins and bookmarks
5. **Session Injection** - Inject real tabs into `zen-sessions.jsonlz4` (key step)

### Key Components

**`migrate_arc_to_zen.py`**

- Main orchestrator with `Arc2ZenMigrator` class
- Coordinates entire migration process
- Handles dry-run mode, logging, database backups

**`arc_pinned_tab_extractor.py`**

- Most complex component (22KB)
- Parses Arc's nested JSON structure with sync data
- Preserves original sidebar ordering via global index tracking
- Data classes: `ArcPinnedTab`, `ArcFolder`, `ArcSpace`

**`inject_open_tabs.py`**

- Injects Arc's open (unpinned) tabs as real tabs in the correct Zen workspace
- Writes the SAME tab object (shared `zenSyncId`) to BOTH `zen-sessions.jsonlz4`
  and `sessionstore.jsonlz4`/`recovery.jsonlz4` (see Critical insight below)
- Maps Arc spaces to Zen workspaces by name; container id inferred from the
  workspace's existing tabs; dedup, `--dry-run`, timestamped backups

**Zen Importer Components:**

- `zen_pinned_tab_importer.py` - Direct import to `zen_pins` table
- `zen_workspace_importer.py` - Creates workspaces in `zen_workspaces` table
- `zen_space_importer.py` - Manages containers in `containers.json`
- `zen_bookmark_importer.py` - Backup import as Firefox bookmarks
- `zen_sessionstore_manager.py` - Manages `zen-sessions.jsonlz4` for open tabs

### Data Flow

**Arc Source:**

```
StorableSidebar.json
├── firebaseSyncState.syncData.spaceModels (space metadata + icons)
├── sidebar.containers[1].items (tabs, folders, containers)
├── sidebar.containers[1].spaces (space → containerIDs mapping)
└── containerIDs: ['pinned', uuid, 'unpinned', uuid] per space
```

**Zen Target:**

```
Zen Profile/
├── places.sqlite (main database)
│   ├── zen_pins (pinned tab metadata — NOT used for rendering)
│   ├── zen_workspaces (workspace definitions with icons/colors)
│   └── moz_bookmarks (Firefox-style bookmarks backup)
├── zen-sessions.jsonlz4 (SOURCE OF TRUTH for rendered sidebar tabs)
├── sessionstore.jsonlz4 (Firefox session; open tabs must ALSO live here,
│   zenSyncId-matched with zen-sessions.jsonlz4 — Zen 1.20+)
├── sessionstore-backups/recovery.jsonlz4 (same content while Zen runs)
├── containers.json (container/space definitions)
└── prefs.js (active workspace preferences)
```

> **Critical insight**: Zen renders sidebar tabs from `zen-sessions.jsonlz4`,
> NOT from the `zen_pins` database table. The DB stores metadata only.
> `inject_session_tabs.py` handles this key step.
>
> **Open (unpinned) tabs need BOTH session files** (Zen 1.20+): Zen keeps
> `zen-sessions.jsonlz4` and `sessionstore.jsonlz4`/`recovery.jsonlz4` in
> lockstep, matched per-tab by `zenSyncId`. On startup only tabs present in
> BOTH files survive; orphans are pruned. `inject_open_tabs.py` handles this
> by writing the same tab object (same `zenSyncId`) into both files.

## Important Implementation Details

### Order Preservation (SOLVED - The Container childrenIds Solution)

**✅ SOLUTION IMPLEMENTED**: Arc's exact visual ordering is now preserved using container-based extraction.

**The Discovery:**
Arc's storage order ≠ display order. Each space has a **container UUID** (not "pinned" string) that contains a `childrenIds` array with items in **exact Arc display order**.

**Correct Data Structure:**

```json
// Each space has containerIDs like:
space_data.containerIDs = ['unpinned', 'uuid-1', 'pinned', 'uuid-2']

// The actual display order is in one of the UUID containers:
data.sidebar.containers[1].items['BDF69180-4E9B-4B4A-B1B4-D6950292683E'].childrenIds = [
  "ACEB0219-BA17-4ADC-BCCB-FF83840AE8DF",  // Finances folder (1st in Arc)
  "4A4CEAC3-53C4-4D04-9EED-B3967CD11904",  // Large Language Models (2nd)
  "BA5EC227-0247-4639-8125-0EA21C4554CC",  // Health folder (3rd)
  // ... etc in exact Arc visual order
]
```

**Key Insight:** The strings "pinned" and "unpinned" are markers in the `containerIDs` array. The UUID immediately *following* each marker is the actual container storing that category's `childrenIds` (display order). The marker order can vary — some spaces have `['pinned', uuid, 'unpinned', uuid]`, others have `['unpinned', uuid, 'pinned', uuid]`.

**Implementation (Working):**

```python
def _get_space_display_order(self, space_id, items_lookup, data):
    """Get pinned tab display order using the container after 'pinned' marker."""
    space_container_ids = self._get_space_container_ids(space_id, data)
    # Find the UUID immediately after the 'pinned' marker
    for idx, cid in enumerate(space_container_ids):
        if cid == 'pinned' and idx + 1 < len(space_container_ids):
            pinned_uuid = space_container_ids[idx + 1]
            # Look up its childrenIds — that's the display order
            ...
```

**Results Achieved:**

- **Before**: Site, Games, Large Language Models... (Arc index 6, 20, 24)
- **After**: Finances, Large Language Models, Health, Games... (Arc visual order) ✅
- **Perfect match**: Extraction now matches Arc sidebar exactly

**Process Flow:**

1. **Find space container UUIDs** from `space_data.containerIDs`
2. **Locate display container** that has `childrenIds` array
3. **Extract in order** using `childrenIds` sequence (not Arc index sorting)
4. **Process recursively** for folder contents using their own `childrenIds`
5. **Repeat for 'unpinned'** marker to get open tabs

### Folder Hierarchy

- Path-based folder UUID mapping: `folder_path → folder_uuid`
- Recursive parent-child relationships via `folder_parent_uuid`
- Folders created before tabs in proper dependency order

### Database Safety

- Read-only access to Arc data (never modifies original)
- Automatic database backups before Zen modifications
- Transaction-based operations with rollback capabilities
- Zen browser must be closed during migration to prevent database locks

### Generated Files

- `arc_pinned_tabs_export.json` - Extracted Arc data
- `zen_database_backup_*.sqlite` - Automatic backups
- `workspace_setup_guide.json` - Manual setup instructions

## Key Algorithms

### UUID Management

Temporary workspace UUIDs are created during import, then consolidated via `zen_workspace_mapper.py`. This prevents conflicts and allows proper cross-table relationship updates.

### Container Assignment

Round-robin icon/color assignment for new containers with existing container detection and reuse based on space names.

## Development Notes

- Python 3.7+ with `lz4` as the only external dependency
- All components include extensive logging for debugging
- Dry-run mode available for safe testing
- Error handling includes graceful degradation and detailed error reporting
