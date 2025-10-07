#!/usr/bin/env python3
"""
Arc Profile Discovery Module

Discovers and maps Arc browser spaces (Chromium profiles) for migration.
Based on Phase 1 investigation findings.
"""

import plistlib
import json
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class ArcProfile:
    """Represents an Arc browser space (Chromium profile)."""
    profile_id: str
    profile_path: Path
    display_name: str
    is_active: bool
    has_history: bool
    has_sessions: bool

    def __str__(self):
        return f"ArcProfile(id='{self.profile_id}', name='{self.display_name}', active={self.is_active})"


class ArcProfileDiscovery:
    """Discovers and analyzes Arc browser profiles/spaces."""

    def __init__(self):
	if os.name == "nt":
		self.home_dir = os.path.expanduser(r"~\")
		self.arc_data_dir = self.home_dir / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4/LocalCache/Local/Arc/User Data"
		self.arc_prefs_main = self.home_dir / "AppData/Local/Packages/TheBrowserCompany.Arc_ttt1ap7aakyb4/LocalCache/Local/company.thebrowser.arc.plist"
	else:
        	self.home_dir = Path.home()
        	self.arc_data_dir = self.home_dir / "Library/Application Support/Arc/User Data"
        	self.arc_prefs_main = self.home_dir / "Library/Preferences/company.thebrowser.Browser.plist"
	"""No dia in windows"""
        self.arc_prefs_dia = self.home_dir / "Library/Preferences/company.thebrowser.dia.plist"

    def check_arc_installation(self) -> bool:
        """Check if Arc browser is installed and has data."""
        if not self.arc_data_dir.exists():
            logger.error(f"Arc data directory not found: {self.arc_data_dir}")
            return False

        if not self.arc_prefs_main.exists():
            logger.error(f"Arc main preferences not found: {self.arc_prefs_main}")
            return False

        logger.info("✅ Arc browser installation detected")
        return True

    def discover_profiles(self) -> List[ArcProfile]:
        """Discover all Arc profiles/spaces."""
        if not self.check_arc_installation():
            return []

        profiles = []

        # Find all Profile directories
        profile_dirs = list(self.arc_data_dir.glob("Profile *"))

        # Also check Default profile
        default_dir = self.arc_data_dir / "Default"
        if default_dir.exists():
            profile_dirs.append(default_dir)

        logger.info(f"Found {len(profile_dirs)} potential profiles")

        for profile_dir in sorted(profile_dirs):
            profile = self._analyze_profile(profile_dir)
            if profile:
                profiles.append(profile)
                logger.info(f"  ✅ {profile}")
            else:
                logger.warning(f"  ❌ Skipped invalid profile: {profile_dir.name}")

        # Get active profiles from preferences
        active_profiles = self._get_active_profiles()
        for profile in profiles:
            profile.is_active = profile.profile_id in active_profiles

        logger.info(f"Discovered {len(profiles)} valid Arc profiles")
        return profiles

    def _analyze_profile(self, profile_dir: Path) -> Optional[ArcProfile]:
        """Analyze a single profile directory."""
        profile_id = profile_dir.name

        # Check for essential files
        preferences_file = profile_dir / "Preferences"
        history_file = profile_dir / "History"
        sessions_dir = profile_dir / "Sessions"

        # Must have at least preferences or history to be valid
        if not preferences_file.exists() and not history_file.exists():
            return None

        # Determine display name
        display_name = self._get_profile_display_name(profile_id, profile_dir)

        # Check what data is available
        has_history = history_file.exists() and history_file.stat().st_size > 0
        has_sessions = sessions_dir.exists() and any(sessions_dir.iterdir())

        return ArcProfile(
            profile_id=profile_id,
            profile_path=profile_dir,
            display_name=display_name,
            is_active=False,  # Will be set later
            has_history=has_history,
            has_sessions=has_sessions
        )

    def _get_profile_display_name(self, profile_id: str, profile_dir: Path) -> str:
        """Get human-readable name for profile."""
        # Based on Phase 1 findings, Arc doesn't store custom space names
        # Use profile ID with friendly formatting
        if profile_id == "Default":
            return "Default Space"
        elif profile_id.startswith("Profile "):
            return f"Arc Space {profile_id.split()[-1]}"
        else:
            return profile_id

    def _get_active_profiles(self) -> set:
        """Get list of active profile IDs from Arc preferences."""
        active_profiles = set()

        try:
            # Check main preferences
            if self.arc_prefs_main.exists():
                with open(self.arc_prefs_main, 'rb') as f:
                    main_prefs = plistlib.load(f)

                # Look for profile references in various settings
                for key, value in main_prefs.items():
                    if isinstance(value, dict):
                        for profile_key in value.keys():
                            if isinstance(profile_key, str) and (
                                profile_key.startswith("Profile ") or profile_key == "Default"
                            ):
                                active_profiles.add(profile_key)

            # Check dia preferences for additional profile info
            if self.arc_prefs_dia.exists():
                with open(self.arc_prefs_dia, 'rb') as f:
                    dia_prefs = plistlib.load(f)

                # Check persisted window data
                window_data_str = dia_prefs.get('persistedWindowData', '')
                if window_data_str:
                    try:
                        window_data = json.loads(window_data_str)
                        for window in window_data.get('windows', []):
                            profile_id = window.get('profileID')
                            if profile_id:
                                active_profiles.add(profile_id)
                    except json.JSONDecodeError:
                        pass

                # Check assistant personality settings (contains profile refs)
                for key in ['supertabAssistantPersonalityInfluences',
                           'supertabAssistantPersonalityQuestionGuidelines']:
                    if key in dia_prefs:
                        for profile_id in dia_prefs[key].keys():
                            if profile_id.startswith("Profile ") or profile_id == "Default":
                                active_profiles.add(profile_id)

        except Exception as e:
            logger.warning(f"Could not read Arc preferences: {e}")

        logger.info(f"Found {len(active_profiles)} active profiles: {sorted(active_profiles)}")
        return active_profiles

    def get_migration_summary(self, profiles: List[ArcProfile]) -> Dict:
        """Generate migration summary statistics."""
        total_profiles = len(profiles)
        active_profiles = len([p for p in profiles if p.is_active])
        profiles_with_history = len([p for p in profiles if p.has_history])
        profiles_with_sessions = len([p for p in profiles if p.has_sessions])

        return {
            'total_profiles': total_profiles,
            'active_profiles': active_profiles,
            'profiles_with_history': profiles_with_history,
            'profiles_with_sessions': profiles_with_sessions,
            'migration_ready': profiles_with_history > 0
        }


def main():
    """CLI interface for Arc profile discovery."""
    print("🔍 Arc Profile Discovery")
    print("=" * 40)

    discovery = ArcProfileDiscovery()
    profiles = discovery.discover_profiles()

    if not profiles:
        print("❌ No Arc profiles found!")
        return

    print(f"\n📊 Discovery Results:")
    for i, profile in enumerate(profiles, 1):
        status = "🟢 Active" if profile.is_active else "⚫ Inactive"
        history = "📚" if profile.has_history else "❌"
        sessions = "📁" if profile.has_sessions else "❌"

        print(f"{i:2}. {profile.display_name:<15} {status}")
        print(f"    Path: {profile.profile_path}")
        print(f"    History: {history}  Sessions: {sessions}")

    summary = discovery.get_migration_summary(profiles)
    print(f"\n📈 Migration Summary:")
    print(f"  Total spaces: {summary['total_profiles']}")
    print(f"  Active spaces: {summary['active_profiles']}")
    print(f"  With bookmarks/history: {summary['profiles_with_history']}")
    print(f"  With current tabs: {summary['profiles_with_sessions']}")
    print(f"  Migration ready: {'✅ Yes' if summary['migration_ready'] else '❌ No'}")


if __name__ == "__main__":
    main()