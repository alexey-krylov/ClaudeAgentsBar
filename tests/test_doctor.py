"""The ``doctor`` preflight checks.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin


class TestDoctorChecks(unittest.TestCase):
    """In-plugin doctor checks behind ``claude-agents-bar doctor``."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cabar-doctor-"))
        self.addCleanup(self._wipe)

    def _wipe(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_tsv_fresh_returns_ok(self):
        sidecar = self.tmpdir / "agent-state.tsv"
        sidecar.write_text("abc\tidle\t1\tSessionStart\t/x\t1\n", encoding="utf-8")
        with patch.object(plugin.core, "SIDECAR_PATH", sidecar):
            status, _ = plugin._doctor_check_tsv_freshness(int(time.time()))
        self.assertEqual(status, "ok")

    def test_tsv_stale_returns_warn(self):
        sidecar = self.tmpdir / "agent-state.tsv"
        sidecar.write_text("row\n", encoding="utf-8")
        with patch.object(plugin.core, "SIDECAR_PATH", sidecar):
            # Simulate "last written 2 hours ago" — easier than sleeping
            # by setting an explicit mtime.
            os.utime(sidecar, (time.time(), time.time() - 7200))
            status, message = plugin._doctor_check_tsv_freshness(int(time.time()))
        self.assertEqual(status, "warn")
        self.assertIn("last updated", message)

    def test_tsv_missing_returns_warn(self):
        missing = self.tmpdir / "no-such.tsv"
        with patch.object(plugin.core, "SIDECAR_PATH", missing):
            status, _ = plugin._doctor_check_tsv_freshness(int(time.time()))
        self.assertEqual(status, "warn")

    # --- usage health (no daemon since ADR-0020) ------------------------ #

    def _check_usage(self, *, enabled=True, usage=None, binary="/usr/local/bin/claude",
                     now=None):
        if now is None:
            now = int(time.time())
        with patch.object(plugin.core, "usage_monitor_enabled", return_value=enabled), \
             patch.object(plugin.sidecars, "read_usage", return_value=usage), \
             patch.object(plugin.usage_monitor, "_claude_bin", return_value=binary):
            return plugin._doctor_check_usage(now)

    def test_usage_orphaned_statusline_is_warn(self):
        # 1.4.x wiring left behind by an upgrade that skipped `setup`.
        settings = self.tmpdir / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            '{"statusLine":{"type":"command",'
            '"command":"bash \\"/opt/hb/hooks/usage-sensor.sh\\""}}',
            encoding="utf-8",
        )
        with patch.object(Path, "home", return_value=self.tmpdir):
            status, msg = self._check_usage(usage=None)
        self.assertEqual(status, "warn")
        self.assertIn("setup", msg)

    def test_usage_off_is_ok(self):
        status, _ = self._check_usage(enabled=False)
        self.assertEqual(status, "ok")

    def test_usage_live_is_ok(self):
        now = int(time.time())
        usage = plugin.core.Usage(now, 42, now + 3600, 7, now + 86400)
        status, msg = self._check_usage(usage=usage, now=now)
        self.assertEqual(status, "ok")
        self.assertIn("42", msg)

    def test_usage_no_claude_binary_is_error(self):
        # The one failure the user has to act on: SwiftBar's stripped PATH.
        status, msg = self._check_usage(usage=None, binary=None)
        self.assertEqual(status, "err")
        self.assertIn("PATH", msg)

    def test_usage_no_snapshot_yet_is_warn(self):
        status, msg = self._check_usage(usage=None)
        self.assertEqual(status, "warn")
        self.assertIn("/usr/local/bin/claude", msg)

    def test_usage_stale_snapshot_treated_as_no_data(self):
        now = int(time.time())
        stale = plugin.core.Usage(now - 10_000, 42, now, 7, now)  # > 2*interval
        with patch.object(plugin.core, "CONFIG",
                          plugin.Config(usage_fetch_interval_sec=180)):
            status, _ = self._check_usage(usage=stale, now=now)
        self.assertEqual(status, "warn")

    def test_hook_registration_all_present_ok(self):
        settings = self.tmpdir / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        payload = {
            "hooks": {
                event: [{"hooks": [{
                    "type": "command",
                    "command": "${HOME}/.claude/hooks/agent-state.sh idle",
                }]}]
                for event in plugin._REQUIRED_HOOK_EVENTS
            }
        }
        settings.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(plugin.core, "HOME", self.tmpdir):
            status, _ = plugin._doctor_check_hook_registration()
        self.assertEqual(status, "ok")

    def test_hook_registration_missing_event_warns(self):
        # Drop two events from settings.json — doctor must report them.
        settings = self.tmpdir / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        payload = {
            "hooks": {
                event: [{"hooks": [{
                    "type": "command",
                    "command": "${HOME}/.claude/hooks/agent-state.sh idle",
                }]}]
                for event in plugin._REQUIRED_HOOK_EVENTS
                if event not in ("Notification", "Stop")
            }
        }
        settings.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(plugin.core, "HOME", self.tmpdir):
            status, message = plugin._doctor_check_hook_registration()
        self.assertEqual(status, "warn")
        self.assertIn("Notification", message)
        self.assertIn("Stop", message)

    def test_hook_registration_missing_settings_returns_err(self):
        # No settings.json on disk at all — a hard error, the plugin
        # can't possibly receive events without it.
        with patch.object(plugin.core, "HOME", self.tmpdir):
            status, _ = plugin._doctor_check_hook_registration()
        self.assertEqual(status, "err")

    def test_editor_app_present_ok(self):
        # CONFIG is frozen → can't mutate; swap the whole singleton.
        new_config = plugin.replace(plugin.CONFIG, editor_url_scheme="vscode://")
        with patch.object(
            plugin.doctor, "_EDITOR_SCHEME_APP", {"vscode://": str(self.tmpdir)},
        ), patch.object(plugin.core, "CONFIG", new_config):
            status, message = plugin._doctor_check_editor_app()
        self.assertEqual(status, "ok")
        self.assertIn(str(self.tmpdir), message)

    def test_editor_app_missing_warns(self):
        missing = self.tmpdir / "Nope.app"
        new_config = plugin.replace(plugin.CONFIG, editor_url_scheme="vscode://")
        with patch.object(
            plugin.doctor, "_EDITOR_SCHEME_APP", {"vscode://": str(missing)},
        ), patch.object(plugin.core, "CONFIG", new_config):
            status, message = plugin._doctor_check_editor_app()
        self.assertEqual(status, "warn")
        self.assertIn("isn't installed", message)

    def test_editor_app_custom_scheme_is_ok_without_check(self):
        # Schemes outside the allowlist are still legal at runtime
        # (extended via the editor_url_scheme allowlist) and we can't
        # know which .app knows the scheme — say so explicitly rather
        # than warning by default.
        new_config = plugin.replace(plugin.CONFIG, editor_url_scheme="myeditor://")
        with patch.object(plugin.doctor, "_EDITOR_SCHEME_APP", {}), \
             patch.object(plugin.core, "CONFIG", new_config):
            status, _ = plugin._doctor_check_editor_app()
        self.assertEqual(status, "ok")


if __name__ == "__main__":
    unittest.main()
