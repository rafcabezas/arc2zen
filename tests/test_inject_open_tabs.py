"""
Tests for inject_open_tabs.py

Covers:
  Fix — Windows: Zen profile found under %APPDATA%
  Fix — Windows: a running Zen is detected (pgrep does not exist there)
"""
import os
import subprocess
from pathlib import Path

import inject_open_tabs


def _fake_run(stdout, calls=None):
    def run(args, **kwargs):
        if calls is not None:
            calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
    return run


class TestWindowsSupport:
    def test_finds_profile_under_appdata(self, tmp_path, monkeypatch):
        profile = tmp_path / "zen" / "Profiles" / "abcd1234.Default (release)"
        profile.mkdir(parents=True)
        (profile / "zen-sessions.jsonlz4").write_bytes(b"")
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert Path(inject_open_tabs.find_zen_profile()) == profile

    def test_running_zen_detected_with_tasklist(self, monkeypatch):
        calls = []
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(subprocess, "run", _fake_run(
            "zen.exe                      4242 Console    1    350,000 K\n", calls))
        assert inject_open_tabs.is_zen_running() is True
        assert calls[0][0] == "tasklist"

    def test_no_zen_process_on_windows(self, monkeypatch):
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(subprocess, "run", _fake_run(
            "INFO: No tasks are running which match the specified criteria.\n"))
        assert inject_open_tabs.is_zen_running() is False
