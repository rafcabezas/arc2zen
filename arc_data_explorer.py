#!/usr/bin/env python3
"""
Arc Browser Data Explorer
Helper script to investigate Arc browser data structures for migration.
"""

import json
import os
import sys
from pathlib import Path

ARC_DATA_DIR = Path.home() / "Library/Application Support/Arc/User Data/Default"

def explore_preferences():
    """Examine Arc's Preferences file for spaces/tabs data."""
    prefs_file = ARC_DATA_DIR / "Preferences"

    if not prefs_file.exists():
        print("❌ Preferences file not found")
        return

    try:
        with open(prefs_file) as f:
            data = json.load(f)

        print("🔍 Arc Preferences Analysis")
        print("=" * 40)

        # Look for space/tab related keys
        relevant_keys = []
        for key in data.keys():
            if any(word in key.lower() for word in ['space', 'tab', 'pin', 'group', 'bookmark']):
                relevant_keys.append(key)

        print(f"📁 Relevant keys found: {len(relevant_keys)}")
        for key in relevant_keys:
            print(f"  • {key}: {type(data[key])}")
            if isinstance(data[key], dict) and len(data[key]) > 0:
                print(f"    └─ Sub-keys: {list(data[key].keys())[:5]}")

        # Check for Arc-specific sections
        arc_sections = ['browser', 'session', 'extensions']
        for section in arc_sections:
            if section in data:
                print(f"\n📂 {section.upper()} section:")
                if isinstance(data[section], dict):
                    for k, v in data[section].items():
                        print(f"  • {k}: {type(v)}")
                        if isinstance(v, (list, dict)) and len(str(v)) < 200:
                            print(f"    └─ {v}")

    except Exception as e:
        print(f"❌ Error reading preferences: {e}")

def explore_browser_files():
    """Examine Arc's .company.thebrowser.Browser.* files."""
    browser_files = list(ARC_DATA_DIR.glob(".company.thebrowser.Browser.*"))

    print(f"\n🗂️  Arc Browser Files Analysis")
    print("=" * 40)
    print(f"📊 Found {len(browser_files)} browser files")

    # Sort by size (largest first)
    browser_files.sort(key=lambda f: f.stat().st_size, reverse=True)

    for i, file in enumerate(browser_files[:3]):  # Check top 3 largest files
        print(f"\n📄 File {i+1}: {file.name}")
        print(f"   Size: {file.stat().st_size:,} bytes")
        print(f"   Modified: {file.stat().st_mtime}")

        try:
            with open(file) as f:
                data = json.load(f)

            print(f"   📋 Top-level keys: {list(data.keys())[:10]}")

            # Look for non-STS data
            non_sts_keys = [k for k in data.keys() if k != 'sts']
            if non_sts_keys:
                print(f"   🎯 Non-STS keys: {non_sts_keys}")
                for key in non_sts_keys[:3]:
                    print(f"      • {key}: {type(data[key])}")

        except Exception as e:
            print(f"   ❌ Error reading: {e}")

def search_for_spaces():
    """Search for any mention of 'space' in Arc data."""
    print(f"\n🔍 Searching for 'space' mentions")
    print("=" * 40)

    # Search in Preferences
    prefs_file = ARC_DATA_DIR / "Preferences"
    if prefs_file.exists():
        try:
            with open(prefs_file) as f:
                content = f.read()

            if 'space' in content.lower():
                print("✅ Found 'space' in Preferences file")
                # Count occurrences
                count = content.lower().count('space')
                print(f"   📊 {count} occurrences found")

                # Show context around first few occurrences
                import re
                matches = re.finditer(r'.{0,30}space.{0,30}', content, re.IGNORECASE)
                for i, match in enumerate(matches):
                    if i >= 3: break  # Show first 3 matches
                    print(f"   • Match {i+1}: ...{match.group()}...")
            else:
                print("❌ No 'space' mentions in Preferences")

        except Exception as e:
            print(f"❌ Error searching Preferences: {e}")

def main():
    """Main exploration function."""
    print("🔎 Arc Browser Data Structure Explorer")
    print("=" * 50)
    print(f"📁 Data directory: {ARC_DATA_DIR}")

    if not ARC_DATA_DIR.exists():
        print("❌ Arc data directory not found!")
        return

    explore_preferences()
    explore_browser_files()
    search_for_spaces()

    print(f"\n✅ Exploration complete!")

if __name__ == "__main__":
    main()