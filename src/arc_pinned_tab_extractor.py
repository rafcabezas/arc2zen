#!/usr/bin/env python3
"""
Arc Pinned Tab Extractor

Extracts pinned tabs with complete folder structure from Arc's StorableSidebar.json.
This provides the actual user-organized pinned tabs, not browsing history.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ArcPinnedTab:
    """Represents a pinned tab from Arc with its folder context."""
    url: str
    title: str
    space_id: str
    space_name: str
    folder_path: List[str]  # Path from space root to tab (e.g., ["Finances"])
    tab_id: str
    parent_id: str
    index: int  # Original position in Arc sidebar
    is_essential: bool = False  # True if this was an Essential tab in Arc

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

@dataclass
class ArcFolder:
    """Represents a folder in Arc's sidebar."""
    folder_id: str
    title: str
    parent_id: str
    space_id: str
    children_ids: List[str]
    index: int  # Position in Arc sidebar

@dataclass
class ArcOpenTab:
    """Represents an open (unpinned) tab from Arc."""
    url: str
    title: str
    space_id: str
    space_name: str
    tab_id: str
    index: int

    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class ArcSpace:
    """Represents an Arc space with its pinned tabs, open tabs, and folders."""
    space_id: str
    space_name: str
    pinned_tabs: List[ArcPinnedTab]
    folders: List[ArcFolder]
    open_tabs: List[ArcOpenTab]
    icon: Optional[str] = None
    color: Optional[dict] = None
    profile: Optional[str] = None  # Arc profile name e.g. 'Default', 'Profile 1', 'Profile 2'

    def __str__(self):
        icon_str = f" ({self.icon})" if self.icon else ""
        return f"ArcSpace(name='{self.space_name}'{icon_str}, pinned={len(self.pinned_tabs)}, open={len(self.open_tabs)}, folders={len(self.folders)})"


class ArcPinnedTabExtractor:
    """Extracts pinned tabs with folder structure from Arc's StorableSidebar.json."""

    def __init__(self):
        if os.name == "nt":
            self.home_dir = Path(os.path.expanduser("~\\"))
            self.arc_sidebar_file = self.home_dir / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4/LocalCache/Local/Arc/StorableSidebar.json"
        else:
            self.home_dir = Path.home()
            self.arc_sidebar_file = self.home_dir / "Library/Application Support/Arc/StorableSidebar.json"

    def extract_pinned_tabs(self, skip_default_essentials: bool = True) -> List[ArcSpace]:
        """Extract all pinned tabs organised by spaces.

        Args:
            skip_default_essentials: When True (default), tabs from the Arc
                'Default' profile topApps container (typically YouTube and
                Google Calendar) are not imported as Zen essentials.  Set to
                False to include them.
        """
        self.skip_default_essentials = skip_default_essentials
        """Extract all pinned tabs organized by spaces with folder structure."""
        if not self.arc_sidebar_file.exists():
            logger.error(f"Arc StorableSidebar.json not found: {self.arc_sidebar_file}")
            return []

        try:
            with open(self.arc_sidebar_file, 'r', encoding="utf-8") as f:
                sidebar_data = json.load(f)

            logger.info("✅ Loaded Arc StorableSidebar.json")

            # Try local sidebar first (fast path for most users)
            arc_spaces = self._parse_local_sidebar_data(sidebar_data)

            # If local sidebar is empty/incomplete, fall back to sync data
            if not arc_spaces:
                logger.info("⚠️  Local sidebar empty, trying sync data fallback...")
                arc_spaces = self._parse_sidebar_data(sidebar_data)

            return arc_spaces

        except Exception as e:
            logger.error(f"Failed to parse StorableSidebar.json: {e}")
            return []

    def _parse_local_sidebar_data(self, data: Dict) -> List[ArcSpace]:
        """Parse the local sidebar data structure (much simpler approach)."""
        arc_spaces = []

        # Get space information from sync data
        space_models = data.get('firebaseSyncState', {}).get('syncData', {}).get('spaceModels', [])
        spaces_info = {}

        # Build space lookup with icons
        i = 0
        while i < len(space_models):
            if isinstance(space_models[i], str) and i + 1 < len(space_models):
                space_id = space_models[i]
                space_data = space_models[i + 1].get('value', {})
                space_name = space_data.get('title', f'Space {space_id}')

                # Extract icon from customInfo if available
                icon = None
                custom_info = space_data.get('customInfo', {})
                icon_type = custom_info.get('iconType', {})
                if 'emoji_v2' in icon_type:
                    icon = icon_type['emoji_v2']
                    logger.info(f"  🎨 Found icon for {space_name}: {icon}")

                # Extract profile information for Essential tabs mapping
                profile = None
                profile_data = space_data.get('profile', {})
                if 'custom' in profile_data and '_0' in profile_data['custom']:
                    custom_data = profile_data['custom']['_0']
                    profile = custom_data.get('directoryBasename')

                # If no profile is set (Personal space), map to "Default" profile
                if profile is None:
                    profile = "Default"

                # Extract color from windowTheme if available
                color = None
                window_theme = custom_info.get('windowTheme', {})
                if window_theme:
                    primary_palette = window_theme.get('primaryColorPalette', {})
                    if primary_palette:
                        # Use midTone as the main color representation
                        mid_tone = primary_palette.get('midTone', {})
                        if mid_tone and 'red' in mid_tone and 'green' in mid_tone and 'blue' in mid_tone:
                            # Extract RGB values (Arc uses extended sRGB with values that can be negative)
                            r = max(0, min(1, mid_tone['red']))  # Clamp to 0-1 range
                            g = max(0, min(1, mid_tone['green']))
                            b = max(0, min(1, mid_tone['blue']))
                            color = {'r': r, 'g': g, 'b': b}
                            logger.info(f"  🎨 Found color for {space_name}: RGB({r:.3f}, {g:.3f}, {b:.3f})")

                spaces_info[space_id] = {
                    'name': space_name,
                    'icon': icon,
                    'profile': profile,
                    'color': color
                }
                i += 2
            else:
                i += 1

        # Local sidebar spaces are authoritative; sync cache may be empty or stale
        logger.info(f"  ℹ️  spaces_info from sync cache: {len(spaces_info)} spaces")
        for c in data.get('sidebar', {}).get('containers', []):
            sp = c.get('spaces', [])
            for j in range(len(sp) - 1):
                if isinstance(sp[j], str) and isinstance(sp[j + 1], dict):
                    sd = sp[j + 1]
                    sid = sd.get('id') or sp[j]
                    dirname = (sd.get('profile', {}).get('custom', {})
                               .get('_0', {}).get('directoryBasename'))
                    if sid not in spaces_info:
                        ci = sd.get('customInfo', {})
                        spaces_info[sid] = {
                            'name': sd.get('title', f'Space {sid}'),
                            'icon': ci.get('iconType', {}).get('emoji_v2'),
                            'profile': None,
                            'color': None,
                        }
                    spaces_info[sid]['profile'] = dirname or 'Default'
                    logger.info(f"  👤 {spaces_info[sid]['name']} ({sid[:8]}) → {spaces_info[sid]['profile']} (local sidebar)")

        # Get all items from local sidebar
        containers = data.get('sidebar', {}).get('containers', [])
        if len(containers) > 1 and 'items' in containers[1]:
            items = containers[1]['items']
            logger.info(f"Found {len(items)} items in local sidebar")

            # Build items lookup
            items_lookup = {}
            i = 0
            while i < len(items):
                if isinstance(items[i], str) and i + 1 < len(items):
                    item_id = items[i]
                    item_data = items[i + 1]
                    items_lookup[item_id] = item_data

                    # Debug: Show what type of item this is
                    if logger.level <= logging.DEBUG:
                        data_section = item_data.get('data', {})
                        if 'tab' in data_section:
                            logger.debug(f"  🔍 Found tab item: {item_id[:20]}... - {item_data.get('title', 'Untitled')}")
                        elif 'list' in data_section:
                            logger.debug(f"  🔍 Found folder item: {item_id[:20]}... - {item_data.get('title', 'Untitled')}")
                        elif 'itemContainer' in data_section:
                            container_type = data_section['itemContainer'].get('containerType', {})
                            logger.debug(f"  🔍 Found container item: {item_id[:20]}... - Type: {list(container_type.keys())}")
                        else:
                            logger.debug(f"  🔍 Found unknown item type: {item_id[:20]}... - Keys: {list(data_section.keys())}")

                    i += 2
                else:
                    i += 1

            logger.debug(f"Built items_lookup with {len(items_lookup)} items")

            # Process items in original order to preserve sidebar ordering
            pinned_tabs_by_space = {space_id: [] for space_id in spaces_info.keys()}
            folders_by_space = {space_id: [] for space_id in spaces_info.keys()}

            # Track the global index to preserve original order
            global_index = 0

            # Process items in the order they appear in the items array
            i = 0
            while i < len(items):
                if isinstance(items[i], str) and i + 1 < len(items):
                    item_id = items[i]
                    item_data = items[i + 1]

                    # Check which space this item belongs to
                    for space_id, space_info in spaces_info.items():
                        space_name = space_info['name']
                        if self._item_belongs_to_space(item_id, space_id, items_lookup, data):
                            data_section = item_data.get('data', {})


                            if 'tab' in data_section:
                                # This is a pinned tab
                                tab_info = data_section['tab']
                                url = tab_info.get('savedURL', '')
                                title = item_data.get('title') or tab_info.get('savedTitle', 'Untitled')

                                if url:  # Only include tabs with URLs
                                    folder_path = self._get_folder_path_local(item_data.get('parentID'), items_lookup, space_id, data)


                                    pinned_tab = ArcPinnedTab(
                                        url=url,
                                        title=title,
                                        space_id=space_id,
                                        space_name=space_name,
                                        folder_path=folder_path,
                                        tab_id=item_id,
                                        parent_id=item_data.get('parentID', ''),
                                        index=global_index  # Preserve original order
                                    )
                                    pinned_tabs_by_space[space_id].append(pinned_tab)

                            elif 'list' in data_section:
                                # This is a folder
                                folder = ArcFolder(
                                    folder_id=item_id,
                                    title=item_data.get('title', 'Untitled Folder'),
                                    parent_id=item_data.get('parentID', ''),
                                    space_id=space_id,
                                    children_ids=item_data.get('childrenIds', []),
                                    index=global_index  # Preserve original order
                                )
                                folders_by_space[space_id].append(folder)

                            global_index += 1
                            break  # Item belongs to one space only


                    i += 2
                else:
                    i += 1

            # Create ArcSpace objects using Arc's correct visual ordering
            # Use the sidebar spaces array to preserve Arc's space ordering
            if len(containers) > 1 and 'spaces' in containers[1]:
                sidebar_spaces = containers[1]['spaces']
                logger.debug(f"Processing {len(sidebar_spaces) // 2} spaces from sidebar")
                for i in range(0, len(sidebar_spaces), 2):
                    if i + 1 < len(sidebar_spaces):
                        space_id = sidebar_spaces[i]
                        space_info = spaces_info.get(space_id, {'name': f'Space {space_id}', 'icon': None})
                        space_name = space_info['name']
                        space_icon = space_info['icon']

                        logger.debug(f"  🔍 Processing space: {space_name} ({space_id})")

                        # Get the correct visual order using container childrenIds
                        display_order = self._get_space_display_order(space_id, items_lookup, data)
                        logger.debug(f"    Display order has {len(display_order)} items")


                        if display_order:
                            # Process items in Arc's exact display order with recursive folder extraction
                            pinned_tabs = []
                            folders = []
                            next_index = 0

                            def process_items_recursive(item_ids, current_folder_path=[]):
                                nonlocal next_index
                                for item_id in item_ids:
                                    item_data = items_lookup.get(item_id, {})
                                    if not item_data:
                                        continue

                                    data_section = item_data.get('data', {})

                                    if 'tab' in data_section:
                                        # This is a pinned tab
                                        tab_info = data_section['tab']
                                        url = tab_info.get('savedURL', '')
                                        title = item_data.get('title') or tab_info.get('savedTitle', 'Untitled')

                                        if url and self._item_belongs_to_space(item_id, space_id, items_lookup, data):
                                            pinned_tab = ArcPinnedTab(
                                                url=url,
                                                title=title,
                                                space_id=space_id,
                                                space_name=space_name,
                                                folder_path=current_folder_path.copy(),  # Use current folder path
                                                tab_id=item_id,
                                                parent_id=item_data.get('parentID', ''),
                                                index=next_index
                                            )
                                            pinned_tabs.append(pinned_tab)
                                            next_index += 1

                                    elif 'list' in data_section:
                                        # This is a folder
                                        if self._item_belongs_to_space(item_id, space_id, items_lookup, data):
                                            folder_title = item_data.get('title', 'Untitled Folder')
                                            folder = ArcFolder(
                                                folder_id=item_id,
                                                title=folder_title,
                                                parent_id=item_data.get('parentID', ''),
                                                space_id=space_id,
                                                children_ids=item_data.get('childrenIds', []),
                                                index=next_index
                                            )
                                            folders.append(folder)
                                            next_index += 1

                                            # Recursively process folder contents
                                            folder_children = item_data.get('childrenIds', [])
                                            if folder_children:
                                                # Create new folder path for children
                                                child_folder_path = current_folder_path + [folder_title]
                                                process_items_recursive(folder_children, child_folder_path)

                            # Start recursive processing with top-level display order
                            process_items_recursive(display_order)

                            # Also extract unpinned (open) tabs
                            unpinned_order = self._get_unpinned_tab_order(space_id, items_lookup, data)
                            open_tabs = []
                            open_index = 0
                            for item_id in unpinned_order:
                                item_data = items_lookup.get(item_id, {})
                                if not item_data:
                                    continue
                                data_section = item_data.get('data', {})
                                if 'tab' in data_section:
                                    tab_info = data_section['tab']
                                    url = tab_info.get('savedURL', '')
                                    title = item_data.get('title') or tab_info.get('savedTitle', 'Untitled')
                                    if url:
                                        open_tab = ArcOpenTab(
                                            url=url,
                                            title=title,
                                            space_id=space_id,
                                            space_name=space_name,
                                            tab_id=item_id,
                                            index=open_index
                                        )
                                        open_tabs.append(open_tab)
                                        open_index += 1
                        else:
                            # Fallback to old method if display order not found
                            pinned_tabs = pinned_tabs_by_space.get(space_id, [])
                            folders = folders_by_space.get(space_id, [])
                            open_tabs = []
                            # Sort by original index as fallback
                            pinned_tabs.sort(key=lambda tab: tab.index)
                            folders.sort(key=lambda folder: folder.index)

                        logger.info(f"  ✅ {space_name}: {len(pinned_tabs)} pinned tabs, {len(open_tabs)} open tabs, {len(folders)} folders")
                        space_color = space_info.get('color')
                        space_profile = space_info.get('profile')
                        arc_spaces.append(ArcSpace(space_id, space_name, pinned_tabs, folders, open_tabs, space_icon, space_color, space_profile))
            else:
                # Fallback to original method if sidebar spaces not found
                for space_id, space_info in spaces_info.items():
                    space_name = space_info['name']
                    space_icon = space_info['icon']
                    pinned_tabs = pinned_tabs_by_space[space_id]
                    folders = folders_by_space[space_id]
                    open_tabs = []

                    # Sort pinned tabs and folders by their original index to preserve order
                    pinned_tabs.sort(key=lambda tab: tab.index)
                    folders.sort(key=lambda folder: folder.index)
                    logger.info(f"  ✅ {space_name}: {len(pinned_tabs)} pinned tabs, {len(open_tabs)} open tabs, {len(folders)} folders")
                    space_color = space_info.get('color')
                    space_profile = space_info.get('profile')
                    arc_spaces.append(ArcSpace(space_id, space_name, pinned_tabs, folders, open_tabs, space_icon, space_color, space_profile))

        # Extract Essential tabs and distribute them to their appropriate workspaces
        essential_tabs_by_space = self._extract_essential_tabs_distributed(data, spaces_info)
        if essential_tabs_by_space:
            total_essential_tabs = sum(len(tabs) for tabs in essential_tabs_by_space.values())
            logger.info(f"  🌟 Found {total_essential_tabs} Essential tabs distributed across workspaces")

            # Add Essential tabs to their corresponding spaces
            for space in arc_spaces:
                if space.space_id in essential_tabs_by_space:
                    essential_tabs = essential_tabs_by_space[space.space_id]
                    space.pinned_tabs.extend(essential_tabs)
                    logger.info(f"    ⭐ Added {len(essential_tabs)} Essential tabs to {space.space_name}")

            # Handle orphaned Essential tabs by dropping them (from inactive profiles)
            if "orphaned" in essential_tabs_by_space:
                orphaned_tabs = essential_tabs_by_space["orphaned"]
                if orphaned_tabs:
                    logger.info(f"  📦 Found {len(orphaned_tabs)} orphaned Essential tabs from inactive profiles")
                    logger.info(f"    📦 Dropping {len(orphaned_tabs)} orphaned Essential tabs (no matching active workspace)")

        logger.info(f"Found {len(arc_spaces)} spaces with pinned tabs")
        return arc_spaces

    def _extract_essential_tabs_distributed(self, data: Dict, spaces_info: Dict) -> Dict[str, List[ArcPinnedTab]]:
        """Extract Essential tabs from topApps containers and distribute them to appropriate spaces.

        Essential tabs in Arc appear at the top with large icons and are stored
        in containers with containerType.topApps rather than spaceItems.

        Returns a dictionary mapping space_id -> list of Essential tabs for that space.
        Orphaned tabs (no matching space) are stored under the "orphaned" key.
        """
        essential_tabs_by_space = {}

        # Get all items from local sidebar
        containers = data.get('sidebar', {}).get('containers', [])
        if len(containers) <= 1 or 'items' not in containers[1]:
            return essential_tabs_by_space

        items = containers[1]['items']

        # Build items lookup (items is stored as alternating id/data pairs)
        items_lookup = {}
        i = 0
        while i < len(items):
            if isinstance(items[i], str) and i + 1 < len(items):
                item_id = items[i]
                item_data = items[i + 1]
                items_lookup[item_id] = item_data
                i += 2
            else:
                i += 1

        # Map each profile to the FIRST space that uses it.
        # Arc replicates essentials to every space in a profile, but in Zen
        # each workspace is independent, so we assign to the primary space only.
        # The 'Default' profile is included — callers can filter it via
        # skip_default_essentials if they don't want YouTube / Google Calendar.
        profile_to_space: Dict[str, str] = {}
        for space_id, space_info in spaces_info.items():
            profile = space_info.get('profile')
            if profile and profile not in profile_to_space:
                profile_to_space[profile] = space_id
                logger.info(f"    📌 Profile '{profile}' → space '{space_info['name']}' (primary)")

        # Look for topApps containers and map them to spaces
        for item_id, item_data in items_lookup.items():
            container_type = item_data.get('data', {}).get('itemContainer', {}).get('containerType', {})

            # Check if this is a topApps container
            if 'topApps' in container_type:
                logger.info(f"  🔍 Found topApps container: {item_id}")

                # Extract profile information from topApps container
                topapps_data = container_type['topApps']['_0']
                directory_basename = None

                if 'custom' in topapps_data and '_0' in topapps_data['custom']:
                    custom_data = topapps_data['custom']['_0']
                    directory_basename = custom_data.get('directoryBasename')
                elif 'default' in topapps_data:
                    directory_basename = "Default"

                # Get the children IDs for this topApps container
                children_ids = item_data.get('childrenIds', [])

                # The Default profile is skipped by default (YouTube / Google
                # Calendar) but kept if the caller sets skip_default_essentials
                # to False.  Other users may want those tabs.
                if directory_basename == 'Default' and self.skip_default_essentials:
                    logger.info("    ⏭️  Skipping Default profile essentials (use --include-default-essentials to import them)")
                    continue

                # Assign to the single primary space for this profile.
                target_space_id = profile_to_space.get(directory_basename)

                if not target_space_id:
                    if directory_basename == 'Default' and spaces_info:
                        # Default profile has no explicit space owner — it appeared
                        # in ALL Arc spaces globally, so assign to the first space.
                        target_space_id = next(iter(spaces_info))
                        logger.info(f"    ℹ️  Default profile → assigning to first space '{spaces_info[target_space_id]['name']}' (global essentials)")
                    else:
                        logger.info(f"    📝 Profile '{directory_basename}' not in profile_to_space — trying intelligent assignment")
                        target_space_id = self._assign_essential_tab_to_space(children_ids, items_lookup, spaces_info)
                else:
                    logger.info(f"    ✅ Profile '{directory_basename}' → space '{spaces_info[target_space_id]['name']}'")

                target_space_name = spaces_info.get(target_space_id, {}).get('name', 'Essential')

                for idx, tab_id in enumerate(children_ids):
                    tab_data = items_lookup.get(tab_id, {})
                    tab_info = tab_data.get('data', {}).get('tab', {})

                    if tab_info and tab_info.get('savedURL'):
                        url = tab_info.get('savedURL', '')
                        title = tab_info.get('savedTitle', url)

                        essential_tab = ArcPinnedTab(
                            url=url,
                            title=title,
                            space_id=target_space_id,
                            space_name=target_space_name,
                            folder_path=[],
                            tab_id=tab_id,
                            parent_id=item_id,
                            index=idx,
                            is_essential=True
                        )

                        essential_tabs_by_space.setdefault(target_space_id, []).append(essential_tab)

                        if target_space_id == 'orphaned':
                            logger.info(f"    📦 Orphaned Essential tab: {title} (Profile: {directory_basename})")
                        else:
                            logger.info(f"    ⭐ Essential tab for {target_space_name}: {title}")

        return essential_tabs_by_space

    def _assign_essential_tab_to_space(self, children_ids: List[str], items_lookup: Dict, spaces_info: Dict) -> str:
        """Intelligently assign orphaned essential tabs to spaces based on URL patterns and content."""

        if not children_ids:
            return "orphaned"

        # Analyze URLs in the essential tabs to find patterns
        tab_urls = []
        tab_titles = []
        debug_content = []

        for tab_id in children_ids:
            tab_data = items_lookup.get(tab_id, {})
            tab_info = tab_data.get('data', {}).get('tab', {})

            if tab_info:
                url = tab_info.get('savedURL', '')
                title = tab_info.get('savedTitle', '')
                if url:
                    tab_urls.append(url.lower())
                    debug_content.append(f"URL: {url}")
                if title:
                    tab_titles.append(title.lower())
                    debug_content.append(f"Title: {title}")

        # Debug: Show what content we're analyzing
        logger.info("    🔍 Analyzing orphaned essential tabs content:")
        for content in debug_content[:5]:  # Show first 5 entries
            logger.info(f"      - {content}")
        if len(debug_content) > 5:
            logger.info(f"      - ... and {len(debug_content) - 5} more")

        # Check for space-specific patterns with more conservative matching
        # Count matches for each space to find the best fit
        space_scores = {}

        for space_id, space_info in spaces_info.items():
            space_name = space_info['name'].lower()
            score = 0

            # Generic: match space name against URLs (exact, lowercase)
            # This works for any space whose name appears in the URLs of its tabs.
            score += sum(1 for url in tab_urls if space_name in url)

            if score > 0:
                space_scores[space_id] = score

        # Return the space with the highest score (minimum 1 — at least one URL match)
        if space_scores:
            best_space_id = max(space_scores.keys(), key=lambda s: space_scores[s])
            best_score = space_scores[best_space_id]
            best_space_name = spaces_info[best_space_id]['name']

            if best_score >= 1:
                logger.info(f"    🎯 Assigning essential tabs to '{best_space_name}' (score: {best_score})")
                return best_space_id

        # No intelligent match found
        return "orphaned"

    def _item_belongs_to_space(self, item_id: str, target_space_id: str, items_lookup: Dict, data: Dict) -> bool:
        """Check if an item belongs to a specific space."""
        item_data = items_lookup.get(item_id, {})
        parent_id = item_data.get('parentID')

        if not parent_id:
            return False

        # Get the space's container IDs
        space_container_ids = self._get_space_container_ids(target_space_id, data)

        # Check if the item's parent is directly one of this space's containers
        if parent_id in space_container_ids:
            return True

        # Check if the item's parent is a folder that belongs to this space (recursive check)
        if parent_id in items_lookup:
            return self._item_belongs_to_space(parent_id, target_space_id, items_lookup, data)

        return False

    def _get_space_container_ids(self, space_id: str, data: Dict) -> List[str]:
        """Get the container IDs for a specific space."""
        containers = data.get('sidebar', {}).get('containers', [])
        if len(containers) > 1 and 'spaces' in containers[1]:
            spaces = containers[1]['spaces']

            # Find the space in the spaces array (stored as alternating id/data pairs)
            for i in range(0, len(spaces), 2):
                if i + 1 < len(spaces) and isinstance(spaces[i], str):
                    if spaces[i] == space_id:
                        space_data = spaces[i + 1]
                        container_ids = space_data.get('containerIDs', [])
                        logger.debug(f"      🔍 Space container IDs: {container_ids}")
                        return container_ids

        logger.debug(f"      ⚠️  No container IDs found for space {space_id}")
        return []

    def _is_pinned_content(self, item_id: str, items_lookup: Dict, data: Dict) -> bool:
        """Check if an item is pinned content (not in unpinned container)."""
        item_data = items_lookup.get(item_id, {})
        parent_id = item_data.get('parentID')

        if not parent_id:
            return False

        # Check if it has a data.tab (tab) or data.list (folder) structure
        data_section = item_data.get('data', {})
        if not ('tab' in data_section or 'list' in data_section):
            return False

        # Check if the parent is NOT an "unpinned" container
        # We check the hierarchy to see if it eventually leads to an "unpinned" container
        return not self._is_in_unpinned_container(item_id, items_lookup, data)

    def _is_in_unpinned_container(self, item_id: str, items_lookup: Dict, data: Dict) -> bool:
        """Check if an item is in an unpinned container hierarchy."""
        item_data = items_lookup.get(item_id, {})
        parent_id = item_data.get('parentID')

        if not parent_id:
            return False

        # If the parent is "unpinned", this item is in unpinned container
        if parent_id == 'unpinned':
            return True

        # If the parent is a folder, check recursively
        if parent_id in items_lookup:
            return self._is_in_unpinned_container(parent_id, items_lookup, data)

        # If the parent is not in items (it's a container), check if it's the unpinned container
        # by checking if it immediately follows the 'unpinned' marker in containerIDs
        containers = data.get('sidebar', {}).get('containers', [])
        if len(containers) > 1 and 'spaces' in containers[1]:
            spaces = containers[1]['spaces']

            for i in range(0, len(spaces), 2):
                if i + 1 < len(spaces) and isinstance(spaces[i], str):
                    space_data = spaces[i + 1]
                    container_ids = space_data.get('containerIDs', [])

                    if parent_id in container_ids:
                        # Check if this container immediately follows 'unpinned' marker
                        try:
                            unpinned_idx = container_ids.index('unpinned')
                            if unpinned_idx + 1 < len(container_ids) and container_ids[unpinned_idx + 1] == parent_id:
                                return True
                        except ValueError:
                            pass
                        return False

        return False

    def _get_space_display_order(self, space_id: str, items_lookup: Dict, data: Dict) -> List[str]:
        """Get the display order of items in a space using container childrenIds.

        The containerIDs array contains markers ('pinned', 'unpinned') followed by
        their respective container UUIDs. The order of markers can vary, so we
        identify containers by which marker immediately precedes them.
        """
        space_container_ids = self._get_space_container_ids(space_id, data)
        if not space_container_ids:
            logger.debug("      ⚠️  No container IDs, returning empty display order")
            return []

        containers = data.get('sidebar', {}).get('containers', [])
        if len(containers) > 1 and 'items' in containers[1]:
            items = containers[1]['items']

            # Find the container UUID that comes immediately after the 'pinned' marker
            pinned_container_uuid = None
            for idx, cid in enumerate(space_container_ids):
                if cid == 'pinned' and idx + 1 < len(space_container_ids):
                    next_cid = space_container_ids[idx + 1]
                    if next_cid not in ('pinned', 'unpinned'):
                        pinned_container_uuid = next_cid
                    break

            if pinned_container_uuid:
                # Look up this container in items to get its childrenIds
                for i in range(0, len(items), 2):
                    if i + 1 < len(items) and items[i] == pinned_container_uuid:
                        children_ids = items[i + 1].get('childrenIds', [])
                        if children_ids:
                            return children_ids
                        break

            # Fallback: combine all non-marker containers
            combined = []
            for cid in space_container_ids:
                if cid in ('pinned', 'unpinned'):
                    continue
                for i in range(0, len(items), 2):
                    if i + 1 < len(items) and items[i] == cid:
                        combined.extend(items[i + 1].get('childrenIds', []))
                        break
            return combined

        return []

    def _get_unpinned_tab_order(self, space_id: str, items_lookup: Dict, data: Dict) -> List[str]:
        """Get the display order of unpinned (open) tabs in a space.

        Finds the container UUID immediately following the 'unpinned' marker.
        """
        space_container_ids = self._get_space_container_ids(space_id, data)
        if not space_container_ids:
            return []

        containers = data.get('sidebar', {}).get('containers', [])
        if len(containers) > 1 and 'items' in containers[1]:
            items = containers[1]['items']

            # Find the container UUID that comes immediately after the 'unpinned' marker
            unpinned_container_uuid = None
            for idx, cid in enumerate(space_container_ids):
                if cid == 'unpinned' and idx + 1 < len(space_container_ids):
                    next_cid = space_container_ids[idx + 1]
                    if next_cid not in ('pinned', 'unpinned'):
                        unpinned_container_uuid = next_cid
                    break

            if unpinned_container_uuid:
                for i in range(0, len(items), 2):
                    if i + 1 < len(items) and items[i] == unpinned_container_uuid:
                        children_ids = items[i + 1].get('childrenIds', [])
                        if children_ids:
                            return children_ids
                        break
        else:
            logger.debug("      ⚠️  No items found in containers, returning empty")

        return []

    def _get_folder_path_local(self, parent_id: str, items_lookup: Dict, space_id: str, data: Dict) -> List[str]:
        """Build the folder path from space root to the item."""
        if not parent_id:
            return []

        parent_data = items_lookup.get(parent_id)
        if not parent_data:
            return []

        # If parent is a folder, include it in path
        parent_data_section = parent_data.get('data', {})
        if 'list' in parent_data_section:
            parent_title = parent_data.get('title', 'Unknown Folder')
            grandparent_path = self._get_folder_path_local(parent_data.get('parentID'), items_lookup, space_id, data)
            return grandparent_path + [parent_title]

        # If parent is not a folder, continue up the hierarchy
        return self._get_folder_path_local(parent_data.get('parentID'), items_lookup, space_id, data)

    def _parse_sidebar_data(self, data: Dict) -> List[ArcSpace]:
        """Parse the complete sidebar data structure from sync data."""
        arc_spaces = []

        # Get space models from sync data
        space_models = data.get('firebaseSyncState', {}).get('syncData', {}).get('spaceModels', [])

        # Process space models in pairs (id, data)
        i = 0
        while i < len(space_models):
            if isinstance(space_models[i], str):
                space_id = space_models[i]
                if i + 1 < len(space_models) and isinstance(space_models[i + 1], dict):
                    space_data = space_models[i + 1].get('value', {})
                    space_name = space_data.get('title', f'Space {space_id}')

                    # Extract icon and color
                    icon = None
                    color = None
                    custom_info = space_data.get('customInfo', {})

                    # Extract icon
                    icon_type = custom_info.get('iconType', {})
                    if 'emoji_v2' in icon_type:
                        icon = icon_type['emoji_v2']
                        logger.info(f"  🎨 Found icon for {space_name}: {icon}")

                    # Extract color
                    window_theme = custom_info.get('windowTheme', {})
                    if window_theme:
                        primary_palette = window_theme.get('primaryColorPalette', {})
                        if primary_palette:
                            mid_tone = primary_palette.get('midTone', {})
                            if mid_tone and 'red' in mid_tone and 'green' in mid_tone and 'blue' in mid_tone:
                                r = max(0, min(1, mid_tone['red']))
                                g = max(0, min(1, mid_tone['green']))
                                b = max(0, min(1, mid_tone['blue']))
                                color = {'r': r, 'g': g, 'b': b}
                                logger.info(f"  🎨 Found color for {space_name}: RGB({r:.3f}, {g:.3f}, {b:.3f})")

                    logger.info(f"📍 Processing space: {space_name}")

                    # Find pinned container for this space
                    pinned_container_id = self._find_pinned_container(data, space_id)
                    if pinned_container_id:
                        arc_space = self._extract_space_content(data, space_id, space_name, pinned_container_id)
                        # Update icon and color on the extracted space
                        arc_space.icon = icon
                        arc_space.color = color
                        if arc_space.pinned_tabs:
                            arc_spaces.append(arc_space)
                i += 2
            else:
                i += 1

        logger.info(f"Found {len(arc_spaces)} spaces with pinned tabs")
        return arc_spaces

    def _find_pinned_container(self, data: Dict, space_id: str) -> Optional[str]:
        """Find the pinned container ID for a given space."""
        # Look in containerModels for this space
        container_models = data.get('firebaseSyncState', {}).get('syncData', {}).get('containerModels', [])

        i = 0
        while i < len(container_models):
            if isinstance(container_models[i], str):
                container_id = container_models[i]
                if i + 1 < len(container_models) and isinstance(container_models[i + 1], dict):
                    container_data = container_models[i + 1].get('value', {})

                    # Check if this container belongs to our space and is pinned
                    container_space_id = container_data.get('spaceID')
                    container_type = container_data.get('containerType', {})

                    if container_space_id == space_id and container_type.get('pinned') is not None:
                        logger.debug(f"Found pinned container {container_id} for space {space_id}")
                        return container_id

                i += 2
            else:
                i += 1

        return None

    def _extract_space_content(self, data: Dict, space_id: str, space_name: str, pinned_container_id: str) -> ArcSpace:
        """Extract tabs and folders for a specific space."""
        sidebar_items = data.get('firebaseSyncState', {}).get('syncData', {}).get('items', [])

        # Build lookup of all sidebar items
        items_lookup = {}
        folders = []
        pinned_tabs = []

        i = 0
        while i < len(sidebar_items):
            if isinstance(sidebar_items[i], str):
                item_id = sidebar_items[i]
                if i + 1 < len(sidebar_items) and isinstance(sidebar_items[i + 1], dict):
                    item_data = sidebar_items[i + 1].get('value', {})
                    items_lookup[item_id] = item_data
                i += 2
            else:
                i += 1

        # Track index for ordering
        index_counter = 0

        # Find all items that belong to the pinned container
        for item_id, item_data in items_lookup.items():
            parent_id = item_data.get('parentID')

            # Check if this item is directly in the pinned container or in a child of it
            if self._is_in_pinned_container(item_id, pinned_container_id, items_lookup):
                data_section = item_data.get('data', {})

                if 'tab' in data_section:
                    # This is a pinned tab
                    tab_info = data_section['tab']
                    url = tab_info.get('savedURL', '')
                    title = item_data.get('title') or tab_info.get('savedTitle', 'Untitled')

                    if url:  # Only include tabs with URLs
                        folder_path = self._get_folder_path(parent_id, items_lookup, pinned_container_id)

                        pinned_tab = ArcPinnedTab(
                            url=url,
                            title=title,
                            space_id=space_id,
                            space_name=space_name,
                            folder_path=folder_path,
                            tab_id=item_id,
                            parent_id=parent_id,
                            index=index_counter
                        )
                        pinned_tabs.append(pinned_tab)
                        index_counter += 1

                elif 'list' in data_section:
                    # This is a folder
                    folder = ArcFolder(
                        folder_id=item_id,
                        title=item_data.get('title', 'Untitled Folder'),
                        parent_id=parent_id,
                        space_id=space_id,
                        children_ids=item_data.get('childrenIds', []),
                        index=index_counter
                    )
                    folders.append(folder)
                    index_counter += 1

        logger.info(f"  ✅ {space_name}: {len(pinned_tabs)} pinned tabs, {len(folders)} folders")
        return ArcSpace(space_id, space_name, pinned_tabs, folders, None, None)

    def _is_in_pinned_container(self, item_id: str, pinned_container_id: str, items_lookup: Dict) -> bool:
        """Check if an item is within the pinned container hierarchy."""
        if item_id == pinned_container_id:
            return True

        item_data = items_lookup.get(item_id)
        if not item_data:
            return False

        parent_id = item_data.get('parentID')
        if parent_id == pinned_container_id:
            return True

        if parent_id and parent_id in items_lookup:
            return self._is_in_pinned_container(parent_id, pinned_container_id, items_lookup)

        return False

    def _get_folder_path(self, parent_id: str, items_lookup: Dict, pinned_container_id: str) -> List[str]:
        """Build the folder path from the pinned container to the item."""
        if not parent_id or parent_id == pinned_container_id:
            return []

        parent_data = items_lookup.get(parent_id)
        if not parent_data:
            return []

        parent_title = parent_data.get('title', 'Unknown Folder')
        grandparent_path = self._get_folder_path(parent_data.get('parentID'), items_lookup, pinned_container_id)

        return grandparent_path + [parent_title]

    def export_to_json(self, arc_spaces: List[ArcSpace], output_file: Path) -> bool:
        """Export extracted pinned tabs to JSON file."""
        try:
            export_data = {
                'export_timestamp': datetime.now(timezone.utc).isoformat(),
                'total_spaces': len(arc_spaces),
                'spaces': []
            }

            for space in arc_spaces:
                space_data = {
                    'space_id': space.space_id,
                    'space_name': space.space_name,
                    'icon': space.icon,
                    'color': space.color,
                    'profile': space.profile,
                    'total_pinned_tabs': len(space.pinned_tabs),
                    'total_open_tabs': len(space.open_tabs),
                    'total_folders': len(space.folders),
                    'pinned_tabs': [tab.to_dict() for tab in space.pinned_tabs],
                    'open_tabs': [tab.to_dict() for tab in space.open_tabs],
                    'folders': [asdict(folder) for folder in space.folders]
                }
                export_data['spaces'].append(space_data)

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)

            logger.info(f"✅ Exported pinned tabs to {output_file}")
            return True

        except Exception as e:
            logger.error(f"Failed to export to JSON: {e}")
            return False

    def get_extraction_summary(self, arc_spaces: List[ArcSpace]) -> Dict:
        """Generate summary statistics for extraction."""
        total_pinned = sum(len(space.pinned_tabs) for space in arc_spaces)
        total_open = sum(len(space.open_tabs) for space in arc_spaces)
        total_folders = sum(len(space.folders) for space in arc_spaces)

        return {
            'total_spaces': len(arc_spaces),
            'total_pinned_tabs': total_pinned,
            'total_open_tabs': total_open,
            'total_folders': total_folders,
            'spaces_summary': [
                {
                    'name': space.space_name,
                    'pinned_tabs': len(space.pinned_tabs),
                    'open_tabs': len(space.open_tabs),
                    'folders': len(space.folders)
                }
                for space in arc_spaces
            ]
        }


def main():
    """CLI interface for Arc pinned tab extraction."""
    print("📌 Arc Pinned Tab Extractor")
    print("=" * 40)

    extractor = ArcPinnedTabExtractor()
    arc_spaces = extractor.extract_pinned_tabs()

    if not arc_spaces:
        print("❌ No pinned tabs found!")
        return

    # Export to JSON
    output_file = Path("arc_pinned_tabs_export.json")
    success = extractor.export_to_json(arc_spaces, output_file)

    if success:
        summary = extractor.get_extraction_summary(arc_spaces)

        print("\n📊 Extraction Summary:")
        print(f"  Total spaces: {summary['total_spaces']}")
        print(f"  Total pinned tabs: {summary['total_pinned_tabs']}")
        print(f"  Total folders: {summary['total_folders']}")
        print(f"\n💾 Exported to: {output_file.absolute()}")

        print("\n📋 Per-space breakdown:")
        for space_info in summary['spaces_summary']:
            print(f"  • {space_info['name']}: {space_info['pinned_tabs']} tabs, {space_info['folders']} folders")


if __name__ == "__main__":
    main()
