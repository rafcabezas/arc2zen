# Arc to Zen Browser Migration Tool

A complete Python-based migration tool that converts Arc browser spaces, pinned tabs, and open tabs into Zen browser workspaces with proper tab assignment.

## 🚀 Quick Start

### Prerequisites

- **Python 3.7+**
- **Arc Browser** (with spaces and pinned tabs you want to migrate)
- **Zen Browser** (installed and run at least once)
- **macOS** or **Windows** (current implementation)
- **lz4** Python package (for session tab injection): `pip install lz4`

### Installation

1. Clone this repository:
```bash
git clone https://github.com/rafcabezas/arc2zen.git
cd arc2zen
```

2. Install the optional dependency for session tab injection:
```bash
pip install lz4
```

### Basic Usage

Run the complete migration (recommended for first-time users):

```bash
# Dry run first to see what will be migrated
python3 migrate_arc_to_zen.py --dry-run

# If everything looks good, run the actual migration
python3 migrate_arc_to_zen.py

# Then inject all tabs into the session file (makes tabs actually appear)
python3 inject_session_tabs.py

# Finally, inject Arc's open (unpinned) tabs (Zen 1.20+ keeps TWO session files)
python3 inject_open_tabs.py --dry-run
python3 inject_open_tabs.py
```

### Important: Session Tab Injection

After running the main migration, tabs may only appear as bookmarks or metadata.
To make them show up as **real browser tabs** in Zen's sidebar, run:

```bash
# Migrate with custom settings
python3 migrate_arc_to_zen.py --zen-profile "Default" --verbose
```

This writes directly to `zen-sessions.jsonlz4`, which is the file Zen actually
reads to render tabs. The script is idempotent — re-running it replaces
previously-injected tabs with a fresh extraction.

### Open (Unpinned) Tabs on Zen 1.20+: Dual-File Injection

For open tabs, `zen-sessions.jsonlz4` alone is NOT enough. Zen keeps two
session files in lockstep, matched per-tab by `zenSyncId`:

1. `zen-sessions.jsonlz4` — Zen's workspace tab store (flat list)
2. `sessionstore.jsonlz4` / `sessionstore-backups/recovery.jsonlz4` — the regular Firefox session

On startup Zen effectively keeps the intersection: an open tab survives only
if a tab with the same `zenSyncId` exists in BOTH files. Tabs written to only
one file are silently pruned on the next restart.

`inject_open_tabs.py` handles this: it appends the same tab object (same
`zenSyncId`) to both files (plus `recovery.jsonlz4`), maps Arc spaces to Zen
workspaces by name, infers the container id from the workspace's existing
tabs, dedups against URLs already present in either file, and creates
timestamped backups. Zen must be fully quit while it runs.

```bash
python3 inject_open_tabs.py --dry-run   # preview what would be injected
python3 inject_open_tabs.py             # inject into both session files
```

### Advanced Usage

```bash
# Migrate only a specific Arc space
python3 migrate_arc_to_zen.py --arc-space "Personal"
python3 migrate_arc_to_zen.py --arc-space "Work" --dry-run

# Specify Zen profile
python3 migrate_arc_to_zen.py --zen-profile "Default"

# Also migrate open tabs via sessionstore (legacy approach)
python3 migrate_arc_to_zen.py --open-tabs

# Verbose logging
python3 migrate_arc_to_zen.py --verbose

# See all available options
python3 migrate_arc_to_zen.py --help
```

## 📋 What Gets Migrated

- **Arc Spaces** → **Zen Workspaces** (each Arc space becomes a Zen workspace)
- **Space Icons** → **Workspace Icons** (Unicode emojis preserved: 🏠, 🌳, 🎬, ⚖️, etc.)
- **Space Colors** → **Workspace Themes** (Arc's subtle color tints accurately reproduced)
- **Pinned Tabs** → **Zen Pinned Tabs** (with folder structure preserved)
- **Essential Tabs** → **Essential Pinned Tabs** (Arc's top toolbar tabs with large icons)
- **Open Tabs** → **Zen Open Tabs** (unpinned tabs restored as real browser tabs)
- **Folder Hierarchy** → **Zen Folder Structure** (nested folders maintained)
- **Display Order** → **Zen Sidebar Order** (Arc visual ordering preserved)
- **Backup Bookmarks** → **Firefox Bookmarks** (additional backup as standard bookmarks)

## 🔧 How It Works

### Step 1: Analyze Your Arc Data
The tool reads Arc's `StorableSidebar.json` to extract:
- Space names, structure, and icons (Unicode emojis when available)
- Pinned tabs with URLs and metadata
- Open (unpinned) tabs per space
- Folder hierarchy within each space
- Visual ordering using container childrenIds

### Step 2: Map Workspaces
The tool helps you map Arc spaces to Zen workspaces:
```bash
python3 src/zen_workspace_mapper.py
```
This creates a mapping guide showing which Zen workspace UUID corresponds to each Arc space.

### Step 3: Import Pinned Tabs & Workspaces
For **Zen 1.18+**, spaces, pinned tabs, and folders are written directly to `zen-sessions.jsonlz4` — the modern storage format for Zen's sidebar. For older Zen versions, the tool falls back to the legacy `zen_pins` and `zen_workspaces` SQLite tables.

## 🛡️ Safety Features

- **Read-only Arc access** - Your Arc data is never modified
- **Automatic backups** - Zen database is backed up before any changes
- **Dry-run mode** - Test migration without making changes
- **Validation** - Data integrity checks throughout the process

## 📁 File Structure

```
arc2zen/
├── migrate_arc_to_zen.py              # Main migration script
├── inject_session_tabs.py             # Inject pinned tabs/folders into zen-sessions.jsonlz4
├── inject_open_tabs.py                # Inject open tabs into BOTH session files (Zen 1.20+)
├── requirements.txt                   # Python dependencies (lz4)
├── src/
│   ├── arc_pinned_tab_extractor.py    # Extract Arc pinned tabs
│   ├── arc_bookmark_extractor.py      # Extract Arc bookmarks
│   ├── arc_profile_discovery.py       # Locate Arc profiles
│   ├── zen_sessions_importer.py       # Import to zen-sessions.jsonlz4 (Zen 1.18+)
│   ├── zen_sessionstore_manager.py    # Read/write mozlz4 sessionstore
│   ├── zen_pinned_tab_importer.py     # Legacy: import tabs to zen_pins table
│   ├── zen_workspace_importer.py      # Legacy: create zen_workspaces entries
│   ├── zen_space_importer.py          # Create Zen containers for spaces
│   ├── zen_bookmark_importer.py       # Backup import as Firefox bookmarks
│   ├── zen_workspace_mapper.py        # Map Arc spaces to Zen workspaces
│   └── zen_schema_analyzer.py         # Analyze Zen database schema
├── .gitignore                         # Excludes generated files
└── README.md                          # This file
```

## 🔍 Detailed Usage

### Workspace Mapping

Before running the full migration, you may want to map your Arc spaces to Zen workspaces:

```bash
python3 src/zen_workspace_mapper.py
```

This interactive script will:
1. Analyze your current Zen workspace structure
2. Ask for your Arc space names (or detect them automatically)
3. Create a mapping guide at `workspace_uuid_mapping.json`
4. Show you which UUID corresponds to each workspace

### Individual Components

You can also run individual components:

```bash
# Extract Arc pinned tabs only
python3 src/arc_pinned_tab_extractor.py

# Analyze Zen database schema
python3 src/zen_schema_analyzer.py

# Import pinned tabs to Zen (advanced usage)
python3 src/zen_pinned_tab_importer.py --dry-run
```

## ⚙️ Configuration

### Command Line Options

- `--dry-run` - Test migration without making changes
- `--zen-profile NAME` - Specify target Zen profile name
- `--arc-space NAME` - Migrate only a specific Arc space by name (case-insensitive partial matching). If not specified, all spaces are migrated.
- `--verbose` - Enable detailed debug logging
- `--help` - Show all available options

### Generated Files

The tool creates several files during migration (all excluded from git):

- `arc_bookmarks_export.json` - Extracted Arc pinned tabs
- `arc_pinned_tabs_export.json` - Arc pinned tabs with workspace info
- `workspace_uuid_mapping.json` - Mapping between Arc spaces and Zen workspaces
- `*.backup.*` - Database backups

## 🎯 Arc Display Order Solution

**✅ Improved**: The migration tool preserves Arc's visual ordering using Arc's internal container structure.

**Technical Solution**: Arc stores display order in each space's pinned container `childrenIds` array, which contains items in visual order. The migration tool uses this data structure to maintain ordering fidelity.

**Result**: Folders and tabs appear in Zen in a similar order to your Arc sidebar.

## 🎨 Visual Migration Features

### Space Icon Migration
**✅ Implemented**: Arc space icons migrate to Zen workspaces as Unicode emojis.

**Technical Solution**: Extracts Unicode emojis from Arc's `customInfo.iconType.emoji_v2` field and preserves them in Zen workspace definitions.

**Result**: Arc space icons (🏠, 🌳, 🎬, ⚖️, etc.) appear as visual icons in Zen workspaces.

### Space Color Migration
**✅ Implemented**: Arc space colors migrate as subtle workspace themes with pixel-perfect accuracy.

**Technical Solution**:
- Extracts RGB values from Arc's `customInfo.windowTheme.primaryColorPalette.midTone`
- Uses measured Arc color values (e.g., Personal green: #bbf6da, WillowTree gold: #fbe496)
- Applies Arc's exact color transformation algorithm to create matching subtle tints
- Stores as JSON theme data in Zen's workspace theme system (`theme_type`, `theme_colors`)

**Result**: Zen workspace backgrounds closely match Arc's subtle color aesthetics.

### Essential Tabs Migration
**✅ Implemented**: Arc's Essential tabs (top toolbar) migrate to appropriate workspaces.

**Technical Solution**:
- Extracts Essential tabs from Arc's `topApps` containers per profile
- Maps tabs to correct workspaces using profile associations (`directoryBasename`)
- Imports with `is_essential` flag to distinguish from regular pinned tabs

**Result**: Arc's Essential tabs appear as pinned tabs in their respective Zen workspaces.

## ⚠️ Minor Limitations

### Folder Visual Styles
- Arc's custom folder icons/colors are not preserved (Zen uses its own folder styling)
- All folder hierarchy and content relationships are maintained

### Browser-Specific Features
- Arc-specific features (like Boosts, Easels) don't have Zen equivalents and are not migrated
- Standard web content, bookmarks, and organizational structure migrate completely

### What's Now Supported ✅
- **Space icons**: Arc space emojis migrate as Unicode icons in Zen
- **Space colors**: Arc color themes migrate as Zen workspace themes
- **Essential tabs**: Arc's top toolbar tabs migrate to appropriate workspaces
- **Display ordering**: Arc sidebar ordering preserved via container childrenIds
- **Folder hierarchy**: Nested folder structure maintained
- **Workspace mapping**: Arc space → Zen workspace conversion

## 🐛 Troubleshooting

### Common Issues

**"Zen profile not found"**
- Make sure Zen browser has been run at least once
- Check that the profile directory exists at `~/Library/Application Support/zen/Profiles/`

**"No Arc data found"**
- Verify Arc browser is installed
- Check that you have spaces with pinned tabs

**Workspace mapping issues**
- Run `python3 src/zen_workspace_mapper.py` to manually map spaces
- Update the generated `workspace_uuid_mapping.json` file

### Data Recovery

If anything goes wrong:
1. The tool creates automatic backups of your Zen database
2. You can restore from the `.backup` files
3. Your Arc data remains unchanged (read-only access)

## 🔬 Technical Details

### Arc Browser Structure
- **Location**: `~/Library/Application Support/Arc/` (macOS)
- **Format**: Chromium-based with Arc-specific extensions
- **Key File**: `StorableSidebar.json` contains spaces, pinned tabs, and folder hierarchy
- **Container ordering**: Each space has `containerIDs: ['pinned', uuid, 'unpinned', uuid]`
  where the UUID immediately following each marker stores that category's `childrenIds`
  in exact visual order. The marker order can vary (pinned-first or unpinned-first).

### Zen Browser Structure
- **Location**: `~/Library/Application Support/zen/Profiles/[profile]/` (macOS)
- **Format**: Firefox-based
- **Key Files**:
  - `zen-sessions.jsonlz4` (Zen 1.18+: spaces, pinned tabs, folders)
  - `sessionstore.jsonlz4` + `sessionstore-backups/recovery.jsonlz4` (Firefox
    session; on Zen 1.20+ open tabs must also exist here with a matching
    `zenSyncId` — see "Dual-File Injection" above)
  - `places.sqlite` (bookmarks database)
  - `containers.json` (workspace container definitions)
  - `prefs.js` (preferences)

### Storage Formats
- **Zen 1.18+**: `zen-sessions.jsonlz4` — mozlz4-compressed JSON containing spaces, tabs, and folders
- **Legacy Zen**: `zen_pins` and `zen_workspaces` SQLite tables in `places.sqlite`
- **Bookmarks**: Standard Firefox `moz_bookmarks`/`moz_places` tables (used as backup)

## 🤝 Contributing

Contributions are welcome! This is an open source tool for the community.

### Development Setup

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

### Testing

Always test with dry-run first:
```bash
python3 migrate_arc_to_zen.py --dry-run --verbose
```

## 📄 License

MIT License - See LICENSE file for details.

**⚠️ Important**: Always backup your data before running migrations. Use at your own risk.

## 🙏 Acknowledgments

- Arc Browser team for creating an innovative browser
- Zen Browser team for building a privacy-focused alternative
- The open source community for inspiration and tools
- Claude Code for AI-assisted development and debugging
