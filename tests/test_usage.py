"""Subscription usage: sidecars, alerts, render line, monitor mode/screen.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin


class TestUsageSidecars(unittest.TestCase):
    """Read/write round-trips for the usage snapshot and the alert progress
    sidecars (spec 0011), plus the fail-open parsing stance."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        usage = self._tmpdir / "agent-state.usage"
        alerts = self._tmpdir / "agent-state.usage-alerts"
        self._orig_usage = plugin.core.USAGE_PATH
        self._orig_alerts = plugin.core.USAGE_ALERTS_PATH
        self._orig_alerts_lock = plugin.core._USAGE_ALERTS_LOCK_DIR
        plugin.core.USAGE_PATH = usage
        plugin.core.USAGE_ALERTS_PATH = alerts
        plugin.core._USAGE_ALERTS_LOCK_DIR = alerts.with_suffix(
            alerts.suffix + ".lock.d"
        )
        self.usage = usage
        self.alerts = alerts

    def tearDown(self):
        import shutil
        plugin.core.USAGE_PATH = self._orig_usage
        plugin.core.USAGE_ALERTS_PATH = self._orig_alerts
        plugin.core._USAGE_ALERTS_LOCK_DIR = self._orig_alerts_lock
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # --- read_usage ---------------------------------------------------------

    def test_read_usage_absent_is_none(self):
        self.assertIsNone(plugin.read_usage())

    def test_read_usage_valid_row(self):
        self.usage.write_text("1700\t63\t9999999999\t7\t8888888888\n", encoding="utf-8")
        u = plugin.read_usage()
        self.assertEqual(u.record_ts, 1700)
        self.assertEqual(u.five_used, 63)
        self.assertEqual(u.five_resets_at, "9999999999")
        self.assertEqual(u.seven_used, 7)
        self.assertEqual(u.seven_resets_at, "8888888888")

    def test_read_usage_too_few_columns_is_none(self):
        self.usage.write_text("1700\t63\t9999999999\t7\n", encoding="utf-8")
        self.assertIsNone(plugin.read_usage())

    def test_read_usage_non_numeric_is_none(self):
        self.usage.write_text("1700\tNaNpercent\t9999999999\t7\t8888888888\n", encoding="utf-8")
        self.assertIsNone(plugin.read_usage())

    def test_read_usage_non_numeric_resets_is_none(self):
        self.usage.write_text("1700\t63\tlater\t7\t8888888888\n", encoding="utf-8")
        self.assertIsNone(plugin.read_usage())

    def test_read_usage_empty_file_is_none(self):
        self.usage.write_text("", encoding="utf-8")
        self.assertIsNone(plugin.read_usage())

    # --- read/write_usage_alerts -------------------------------------------

    def test_usage_alerts_round_trip(self):
        plugin.write_usage_alerts(("9999999999", 80))
        self.assertEqual(plugin.read_usage_alerts(), ("9999999999", 80))

    def test_write_usage_alerts_none_removes_file(self):
        plugin.write_usage_alerts(("9999999999", 50))
        self.assertTrue(self.alerts.exists())
        plugin.write_usage_alerts(None)
        self.assertFalse(self.alerts.exists())

    def test_read_usage_alerts_absent_is_none(self):
        self.assertIsNone(plugin.read_usage_alerts())

    def test_read_usage_alerts_corrupt_is_none(self):
        self.alerts.write_text("onlyonecolumn\n", encoding="utf-8")
        self.assertIsNone(plugin.read_usage_alerts())

    def test_read_usage_alerts_non_numeric_is_none(self):
        self.alerts.write_text("9999999999\tlots\n", encoding="utf-8")
        self.assertIsNone(plugin.read_usage_alerts())



class TestUsageAlertsReconcile(unittest.TestCase):
    """The per-tick threshold logic in :func:`usage_alerts.reconcile`.

    ``_fire`` (which spawns the bash notifier) is patched out; ``read_usage``
    is patched to inject a snapshot. The alerts sidecar and the live
    ``CONFIG`` flag are redirected per-test.
    """

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        alerts = self._tmpdir / "agent-state.usage-alerts"
        self._orig_alerts = plugin.core.USAGE_ALERTS_PATH
        self._orig_alerts_lock = plugin.core._USAGE_ALERTS_LOCK_DIR
        self._orig_mode = plugin.core.USAGE_MONITOR_MODE_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_ALERTS_PATH = alerts
        plugin.core._USAGE_ALERTS_LOCK_DIR = alerts.with_suffix(
            alerts.suffix + ".lock.d"
        )
        # Redirect the monitor-mode sidecar to an absent tmp path so the master
        # gate falls through to CONFIG.usage_monitor (set per-test).
        plugin.core.USAGE_MONITOR_MODE_PATH = self._tmpdir / "usage-monitor.mode"
        self.alerts = alerts
        self.now = 1_700_000_000
        # a window that hasn't expired yet
        self.window = str(self.now + 3600)

    def tearDown(self):
        import shutil
        plugin.core.USAGE_ALERTS_PATH = self._orig_alerts
        plugin.core._USAGE_ALERTS_LOCK_DIR = self._orig_alerts_lock
        plugin.core.USAGE_MONITOR_MODE_PATH = self._orig_mode
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _set_on(self, on=True):
        # Master on; ``on`` toggles the notify_on_usage sub-flag.
        plugin.core.CONFIG = plugin.Config(
            usage_monitor="on", notify_on_usage=on
        )

    def test_master_off_does_nothing(self):
        plugin.core.CONFIG = plugin.Config(
            usage_monitor="off", notify_on_usage=True
        )
        fired = self._run(self._usage(72))
        self.assertEqual(fired, [])
        self.assertFalse(self.alerts.exists())

    def _usage(self, five_used, resets_at=None):
        return plugin.core.Usage(
            record_ts=self.now,
            five_used=five_used,
            five_resets_at=resets_at or self.window,
            seven_used=7,
            seven_resets_at=str(self.now + 345600),
        )

    def _run(self, usage):
        fired = []
        with patch.object(
            plugin.usage_alerts, "_fire",
            side_effect=lambda pct, kind, reset_secs: fired.append((pct, kind)),
        ), patch.object(plugin.sidecars, "read_usage", return_value=usage):
            plugin.usage_alerts.reconcile(self.now)
        return fired

    def test_feature_off_does_nothing(self):
        self._set_on(False)
        fired = self._run(self._usage(72))
        self.assertEqual(fired, [])
        self.assertFalse(self.alerts.exists())

    def test_no_snapshot_does_nothing(self):
        self._set_on(True)
        fired = self._run(None)
        self.assertEqual(fired, [])

    def test_expired_window_does_nothing(self):
        self._set_on(True)
        # resets_at already in the past → stale snapshot, no alert
        fired = self._run(self._usage(72, resets_at=str(self.now - 10)))
        self.assertEqual(fired, [])

    def test_fires_first_threshold_with_actual_pct(self):
        self._set_on(True)
        fired = self._run(self._usage(52))
        self.assertEqual(fired, [(52, "A")])
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 50))

    def test_below_first_threshold_no_fire(self):
        self._set_on(True)
        fired = self._run(self._usage(49))
        self.assertEqual(fired, [])

    def test_boundary_fifty_fires(self):
        self._set_on(True)
        fired = self._run(self._usage(50))
        self.assertEqual(fired, [(50, "A")])
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 50))

    def test_no_refire_same_window(self):
        self._set_on(True)
        plugin.write_usage_alerts((self.window, 50))
        fired = self._run(self._usage(55))
        self.assertEqual(fired, [])
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 50))

    def test_collapse_multi_threshold_single_fire(self):
        # 48% → 72% between ticks crosses 50/60/70 at once: ONE banner at the
        # actual 72%, counter jumps straight to 70 — not three banners.
        self._set_on(True)
        fired = self._run(self._usage(72))
        self.assertEqual(fired, [(72, "A")])
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 70))

    def test_critical_fires_kind_b(self):
        self._set_on(True)
        plugin.write_usage_alerts((self.window, 90))
        fired = self._run(self._usage(96))
        self.assertEqual(fired, [(96, "B")])
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 95))

    def test_new_window_resets_progress(self):
        # Previous window was at 90; a new resets_at means the window rolled
        # over, so the counter resets and a low pct fires nothing but records
        # the fresh window.
        self._set_on(True)
        plugin.write_usage_alerts(("1111111111", 90))
        fired = self._run(self._usage(20))
        self.assertEqual(fired, [])
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 0))

    def test_fire_and_write_happen_under_lock(self):
        # Regression: SwiftBar runs the plugin concurrently (scheduled tick +
        # hook-fired refreshallplugins). If the read→decide→fire→write section
        # weren't serialised, two ticks could both read max_fired=0, both cross
        # 50, and both fire — a double banner + double "you've hit N%" speech.
        # reconcile now holds _usage_alerts_lock across the whole section, so at
        # the moment _fire runs the lock dir exists — a second tick would block
        # on it and, once it acquired the lock, observe the persisted counter
        # and stay quiet.
        self._set_on(True)
        lock_dir = plugin.core._USAGE_ALERTS_LOCK_DIR
        held = []
        with patch.object(
            plugin.usage_alerts, "_fire",
            side_effect=lambda pct, kind, reset_secs: held.append(lock_dir.exists()),
        ), patch.object(plugin.sidecars, "read_usage", return_value=self._usage(52)):
            plugin.usage_alerts.reconcile(self.now)
        self.assertEqual(held, [True])          # fired while the lock was held
        self.assertFalse(lock_dir.exists())     # and released it afterwards
        self.assertEqual(plugin.read_usage_alerts(), (self.window, 50))



class TestUsageRenderLine(unittest.TestCase):
    """The grey Tools usage line (:func:`render._print_usage_line`)."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.usage = self._tmpdir / "agent-state.usage"
        self._orig_usage = plugin.core.USAGE_PATH
        self._orig_mode = plugin.core.USAGE_MONITOR_MODE_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_PATH = self.usage
        # Master on by default for these tests (the line is gated on it); the
        # mode sidecar points at an absent path so it falls through to CONFIG.
        plugin.core.USAGE_MONITOR_MODE_PATH = self._tmpdir / "usage-monitor.mode"
        plugin.core.CONFIG = plugin.Config(usage_monitor="on")

    def tearDown(self):
        import shutil
        plugin.core.USAGE_PATH = self._orig_usage
        plugin.core.USAGE_MONITOR_MODE_PATH = self._orig_mode
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _capture(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            plugin.render._print_usage_line()
        return buf.getvalue()

    def test_master_off_prints_nothing(self):
        # Even with a fresh snapshot, monitor off → no line.
        plugin.core.CONFIG = plugin.Config(usage_monitor="off")
        future = int(time.time()) + 10080
        self.usage.write_text(
            f"{int(time.time())}\t63\t{future}\t7\t{future}\n", encoding="utf-8"
        )
        self.assertEqual(self._capture(), "")

    def test_stale_record_ts_prints_nothing(self):
        # record_ts far in the past (background session died) → hidden even
        # though the window itself hasn't expired.
        now = int(time.time())
        self.usage.write_text(
            f"{now - 100000}\t63\t{now + 10080}\t7\t{now + 10080}\n", encoding="utf-8"
        )
        self.assertEqual(self._capture(), "")

    def test_colored_numbers(self):
        now = int(time.time())
        self.usage.write_text(
            f"{now}\t72\t{now + 10080}\t90\t{now + 10080}\n", encoding="utf-8"
        )
        out = self._capture()
        self.assertIn("ansi=true", out)
        self.assertIn(plugin.core._ANSI_WORKING, out)  # 72 → yellow
        self.assertIn(plugin.core._ANSI_WAITING, out)  # 90 → red

    def test_no_snapshot_prints_nothing(self):
        self.assertEqual(self._capture(), "")

    def test_expired_window_prints_nothing(self):
        # resets_at in the past → stale, hidden
        self.usage.write_text(
            f"{int(time.time())}\t63\t{int(time.time()) - 10}\t7\t{int(time.time()) + 99999}\n",
            encoding="utf-8",
        )
        self.assertEqual(self._capture(), "")

    def test_snapshot_prints_grey_line(self):
        future = int(time.time()) + 10080  # ~2h48m
        self.usage.write_text(
            f"{int(time.time())}\t63\t{future}\t7\t{future}\n", encoding="utf-8"
        )
        out = self._capture()
        # grey, passive Tools sub-item ("--" prefix — a top-level line would get
        # a SwiftBar refresh/about submenu; this is just text)
        self.assertTrue(out.startswith("--"))
        self.assertIn("color=#999999", out)
        self.assertIn("63", out)   # session %
        self.assertIn("7", out)    # weekly %
        # weekly reset time present (future → non-empty); no pacing target
        self.assertNotIn("/", out.split(" | ", 1)[0])  # no "wk%/target%" form
        # the visual separator must NOT be a bare pipe (would break SwiftBar)
        label = out.split(" | ", 1)[0]
        self.assertNotIn(" | ", label)

    def test_until_rounds_to_whole_hours_half_up(self):
        f = plugin.render._format_until
        self.assertEqual(f(2 * 3600 + 29 * 60, "en"), "2h")   # 2:29 → 2h
        self.assertEqual(f(2 * 3600 + 30 * 60, "en"), "3h")   # 2:30 → 3h
        self.assertEqual(f(2 * 3600 + 55 * 60, "en"), "3h")
        self.assertEqual(f(42 * 60, "en"), "1h")              # 0:42 → 1h
        self.assertEqual(f(4 * 60, "en"), "1h")               # floored at 1h
        self.assertEqual(f(0, "en"), "")
        self.assertEqual(f(4 * 86400, "en"), "4d")            # whole days
        self.assertEqual(f(2 * 3600 + 30 * 60, "ru"), "3ч")   # localized



class TestUsageMonitorMode(unittest.TestCase):
    """Sidecar-override + write for the usage-monitor master switch."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._orig_path = plugin.core.USAGE_MONITOR_MODE_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_MONITOR_MODE_PATH = self._tmpdir / "mode"
        self.path = plugin.core.USAGE_MONITOR_MODE_PATH

    def tearDown(self):
        import shutil
        plugin.core.USAGE_MONITOR_MODE_PATH = self._orig_path
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_sidecar_on_beats_config_off(self):
        plugin.core.CONFIG = plugin.Config(usage_monitor="off")
        self.path.write_text("on\n", encoding="utf-8")
        self.assertEqual(plugin.core.usage_monitor_mode(), "on")
        self.assertTrue(plugin.core.usage_monitor_enabled())

    def test_absent_falls_back_to_config(self):
        plugin.core.CONFIG = plugin.Config(usage_monitor="on")
        self.assertEqual(plugin.core.usage_monitor_mode(), "on")

    def test_garbage_falls_back_to_config(self):
        plugin.core.CONFIG = plugin.Config(usage_monitor="off")
        self.path.write_text("wat\n", encoding="utf-8")
        self.assertEqual(plugin.core.usage_monitor_mode(), "off")

    def test_write_round_trip(self):
        self.assertEqual(plugin.core.write_usage_monitor_mode("on"), 0)
        self.assertEqual(self.path.read_text(encoding="utf-8").strip(), "on")

    def test_write_rejects_invalid(self):
        self.assertEqual(plugin.core.write_usage_monitor_mode("bogus"), 1)
        self.assertFalse(self.path.exists())



class TestUsageMonitorReconcile(unittest.TestCase):
    """Lifecycle decisions in :func:`usage_monitor.reconcile` — spawn/kill/ping
    are patched out so only the state machine is exercised."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._orig_ping = plugin.core.USAGE_MONITOR_PING_PATH
        self._orig_mode = plugin.core.USAGE_MONITOR_MODE_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_MONITOR_PING_PATH = self._tmpdir / "ping"
        plugin.core.USAGE_MONITOR_MODE_PATH = self._tmpdir / "mode"
        self.now = 1_700_000_000

    def tearDown(self):
        import shutil
        plugin.core.USAGE_MONITOR_PING_PATH = self._orig_ping
        plugin.core.USAGE_MONITOR_MODE_PATH = self._orig_mode
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _set_mode(self, mode):
        plugin.core.CONFIG = plugin.Config(
            usage_monitor=mode, usage_ping_interval_sec=300
        )

    def _run(self, sessions):
        calls = {"spawn": 0, "kill": 0}
        with patch.object(plugin.usage_monitor, "_live_sessions", return_value=sessions), \
             patch.object(plugin.usage_monitor, "_spawn",
                          side_effect=lambda: calls.__setitem__("spawn", calls["spawn"] + 1)), \
             patch.object(plugin.usage_monitor, "_kill",
                          side_effect=lambda *a, **k: calls.__setitem__("kill", calls["kill"] + 1)):
            plugin.usage_monitor.reconcile(self.now)
        return calls

    _ONE = ["34777.cab-usage-mon"]
    _TWO = ["34777.cab-usage-mon", "34778.cab-usage-mon"]

    def test_off_alive_kills(self):
        self._set_mode("off")
        calls = self._run(self._ONE)
        self.assertEqual(calls["kill"], 1)
        self.assertEqual(calls["spawn"], 0)

    def test_off_dead_noop(self):
        self._set_mode("off")
        calls = self._run([])
        self.assertEqual(calls, {"spawn": 0, "kill": 0})

    def test_off_duplicates_killed(self):
        self._set_mode("off")
        calls = self._run(self._TWO)
        self.assertEqual(calls["kill"], 1)   # _kill(sessions) → one call, all tokens
        self.assertEqual(calls["spawn"], 0)

    def test_on_dead_spawns_and_stamps(self):
        self._set_mode("on")
        calls = self._run([])
        self.assertEqual(calls["spawn"], 1)
        self.assertEqual(calls["kill"], 0)
        self.assertEqual(plugin.usage_monitor._read_ping(), self.now)

    def test_on_alive_within_interval_noop(self):
        self._set_mode("on")
        plugin.usage_monitor._write_ping(self.now - 100)  # < 300s ago
        calls = self._run(self._ONE)
        self.assertEqual(calls, {"spawn": 0, "kill": 0})

    def test_on_alive_past_interval_recycles(self):
        # A long-lived session goes stale, so on the interval we recycle it
        # (kill + respawn) rather than ping — that forces fresh rate_limits.
        self._set_mode("on")
        plugin.usage_monitor._write_ping(self.now - 400)  # >= 300s ago
        calls = self._run(self._ONE)
        self.assertEqual(calls["kill"], 1)
        self.assertEqual(calls["spawn"], 1)
        self.assertEqual(plugin.usage_monitor._read_ping(), self.now)

    def test_on_duplicates_collapse_regardless_of_interval(self):
        # Leaked duplicates collapse to one fresh session even well within the
        # recycle interval — they burn quota, so don't wait.
        self._set_mode("on")
        plugin.usage_monitor._write_ping(self.now - 1)  # freshly stamped
        calls = self._run(self._TWO)
        self.assertEqual(calls["kill"], 1)
        self.assertEqual(calls["spawn"], 1)
        self.assertEqual(plugin.usage_monitor._read_ping(), self.now)



class TestScreenSessionParser(unittest.TestCase):
    """`screen -ls` output parsing for usage-monitor liveness + dedup."""

    def test_session_present(self):
        out = (
            "There is a screen on:\n"
            "\t34777.cab-usage-mon\t(Detached)\n"
            "1 Socket in /tmp/screens.\n"
        )
        self.assertEqual(
            plugin.usage_monitor._session_tokens(out, "cab-usage-mon"),
            ["34777.cab-usage-mon"],
        )

    def test_no_sessions(self):
        out = "No Sockets found in /tmp/screens.\n"
        self.assertEqual(
            plugin.usage_monitor._session_tokens(out, "cab-usage-mon"), []
        )

    def test_similar_name_not_matched(self):
        out = "\t999.cab-usage-mon-2\t(Detached)\n"
        self.assertEqual(
            plugin.usage_monitor._session_tokens(out, "cab-usage-mon"), []
        )

    def test_duplicates_all_returned(self):
        out = (
            "There are screens on:\n"
            "\t37614.cab-usage-mon\t(Detached)\n"
            "\t30473.cab-usage-mon\t(Detached)\n"
            "\t27581.cab-usage-mon\t(Detached)\n"
            "3 Sockets in /tmp/screens.\n"
        )
        self.assertEqual(
            plugin.usage_monitor._session_tokens(out, "cab-usage-mon"),
            ["37614.cab-usage-mon", "30473.cab-usage-mon", "27581.cab-usage-mon"],
        )


if __name__ == "__main__":
    unittest.main()
