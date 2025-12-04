# Arc to Zen Browser Migration Tool

A complete Python-based migration tool that converts Arc browser spaces and pinned tabs into Zen browser workspaces with proper pinned tab assignment.

## 🚀 Quick Start

### Prerequisites

- **Python 3.7+**
- **Arc Browser** (with spaces and pinned tabs you want to migrate)
- **Zen Browser** (installed and run at least once)
- **macOS** or **Windows** (current implementation)

### Installation

1. Clone this repository:
```bash
git clone https://github.com/rafcabezas/arc2zen.git
cd arc2zen
```

2. No additional dependencies required! Uses only Python standard library.

### Basic Usage

Run the complete migration (recommended for first-time users):

```bash
# Dry run first to see what will be migrated
python3 migrate_arc_to_zen.py --dry-run

# If everything looks good, run the actual migration
python3 migrate_arc_to_zen.py
```

### Advanced Usage

```bash
# Migrate with custom settings
python3 migrate_arc_to_zen.py --min-visits 5 --zen-profile "Default" --verbose

# See all available options
python3 migrate_arc_to_zen.py --help
```

## 📋 What Gets Migrated

- **Arc Spaces** → **Zen Workspaces** (each Arc space becomes a Zen workspace)
- **Space Icons** → **Workspace Icons** (Unicode emojis preserved: 🏠, 🌳, 🎬, ⚖️, etc.)
- **Space Colors** → **Workspace Themes** (Arc's subtle color tints accurately reproduced)
- **Pinned Tabs** → **Zen Pinned Tabs** (with folder structure preserved)
- **Essential Tabs** → **Essential Pinned Tabs** (Arc's top toolbar tabs with large icons)
- **Folder Hierarchy** → **Zen Folder Structure** (nested folders maintained)
- **Display Order** → **Zen Sidebar Order** (Arc visual ordering preserved)
- **Backup Bookmarks** → **Firefox Bookmarks** (additional backup as standard bookmarks)

## 🔧 How It Works

### Step 1: Analyze Your Arc Data
The tool reads your Arc browser's `StorableSidebar.json` to extract:
- Space names, structure, and icons (Unicode emojis when available)
- Pinned tabs with URLs and metadata
- Folder hierarchy within each space
- Visual ordering using container childrenIds

### Step 2: Map Workspaces
The tool helps you map Arc spaces to Zen workspaces:
```bash
python3 src/zen_workspace_mapper.py
```
This creates a mapping guide showing which Zen workspace UUID corresponds to each Arc space.

### Step 3: Import Pinned Tabs
Pinned tabs are imported into Zen's `zen_pins` table with proper:
- Workspace UUID assignment
- Folder hierarchy preservation
- Position ordering

### Step 4: Create Workspaces
Zen workspaces are created in the `zen_workspaces` table with proper container assignments and Arc space icons preserved as Unicode emojis.

## 🛡️ Safety Features

- **Read-only Arc access** - Your Arc data is never modified
- **Automatic backups** - Zen database is backed up before any changes
- **Dry-run mode** - Test migration without making changes
- **Validation** - Data integrity checks throughout the process

## 📁 File Structure

```
arc2zen/
├── migrate_arc_to_zen.py          # Main migration script
├── src/
│   ├── arc_pinned_tab_extractor.py # Extract Arc pinned tabs
│   ├── zen_pinned_tab_importer.py  # Import tabs to Zen
│   ├── zen_workspace_importer.py   # Create Zen workspaces
│   ├── zen_workspace_mapper.py     # Map Arc spaces to Zen workspaces
│   └── zen_schema_analyzer.py      # Analyze Zen database schema
├── .gitignore                     # Excludes generated files
└── README.md                      # This file
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
- `--min-visits N` - Only migrate bookmarks with N+ visits (default: 2)
- `--zen-profile NAME` - Specify target Zen profile name
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

**Technical Solution**: Extracts Unicode emojis from Arc's `customInfo.iconType.emoji_v2` field and stores them in Zen's `zen_workspaces.icon` column.

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
- **Location**: `~/Library/Application Support/Arc/User Data/Default/`
- **Format**: Chromium-based with Arc-specific extensions
- **Key File**: `StorableSidebar.json` contains spaces and pinned tabs

### Zen Browser Structure
- **Location**: `~/Library/Application Support/zen/Profiles/[profile]/`
- **Format**: Firefox-based
- **Key Files**: `places.sqlite` (bookmarks database), `prefs.js` (preferences)

### Database Schema
- **zen_pins** table: Stores pinned tabs with workspace UUIDs
- **zen_workspaces** table: Manages workspace definitions
- **moz_places** table: Standard Firefox bookmarks storage

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
