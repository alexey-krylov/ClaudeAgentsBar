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
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_ALERTS_PATH = alerts
        plugin.core._USAGE_ALERTS_LOCK_DIR = alerts.with_suffix(
            alerts.suffix + ".lock.d"
        )
        self.alerts = alerts
        self.now = 1_700_000_000
        # a window that hasn't expired yet
        self.window = str(self.now + 3600)

    def tearDown(self):
        import shutil
        plugin.core.USAGE_ALERTS_PATH = self._orig_alerts
        plugin.core._USAGE_ALERTS_LOCK_DIR = self._orig_alerts_lock
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
    """The two grey Statistics usage lines (:func:`render._print_usage_lines`)."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self.usage = self._tmpdir / "agent-state.usage"
        self._orig_usage = plugin.core.USAGE_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_PATH = self.usage
        # Master on by default for these tests (the lines are gated on it).
        plugin.core.CONFIG = plugin.Config(usage_monitor="on")

    def tearDown(self):
        import shutil
        plugin.core.USAGE_PATH = self._orig_usage
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _capture(self):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            plugin.render._print_usage_lines()
        return buf.getvalue()

    def test_master_off_prints_nothing(self):
        # Even with a fresh snapshot, feature off → no lines.
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




class TestUsageBarRendering(unittest.TestCase):
    """The pseudo-progress bar and row layout of the two usage lines."""

    def _plain(self, bar):
        # Strip the ANSI zone colour so the cells can be counted.
        return (
            bar.replace(plugin.core._ANSI_WORKING, "")
            .replace(plugin.core._ANSI_WAITING, "")
            .replace(plugin.core._ANSI_RESET, "")
        )

    def test_bar_is_always_ten_cells(self):
        for pct in (0, 1, 7, 50, 99, 100):
            self.assertEqual(len(self._plain(plugin.render._usage_bar(pct))), 10, pct)

    def test_bar_fill_rounds_half_up(self):
        filled = plugin.render._USAGE_BAR_FILLED
        self.assertEqual(self._plain(plugin.render._usage_bar(0)).count(filled), 0)
        self.assertEqual(self._plain(plugin.render._usage_bar(44)).count(filled), 4)
        self.assertEqual(self._plain(plugin.render._usage_bar(45)).count(filled), 5)
        self.assertEqual(self._plain(plugin.render._usage_bar(100)).count(filled), 10)

    def test_nonzero_never_renders_an_empty_bar(self):
        # 4 % rounds to 0 cells but has to look different from a clean 0 %.
        self.assertEqual(
            self._plain(plugin.render._usage_bar(4)).count(plugin.render._USAGE_BAR_FILLED), 1
        )

    def test_bar_takes_the_zone_colour(self):
        self.assertNotIn(plugin.core._ANSI_WORKING, plugin.render._usage_bar(59))
        self.assertIn(plugin.core._ANSI_WORKING, plugin.render._usage_bar(60))
        self.assertIn(plugin.core._ANSI_WAITING, plugin.render._usage_bar(85))

    def test_out_of_range_percentages_are_clamped(self):
        self.assertEqual(len(self._plain(plugin.render._usage_bar(-5))), 10)
        self.assertEqual(
            self._plain(plugin.render._usage_bar(140)).count(plugin.render._USAGE_BAR_FILLED), 10
        )

    def test_rows_align_on_label_and_percentage(self):
        # Same label width + right-aligned percentage → the bars start in the
        # same column and the percentages end in the same one, whatever the
        # numbers are. Compare the *visible* text: an ANSI escape has no width
        # on screen but plenty in ``len()``.
        a = self._plain(plugin.render._usage_row("Session", 7, 7, "3h"))
        b = self._plain(plugin.render._usage_row("Week", 7, 40, "1d"))
        bar_start = plugin.render._USAGE_BAR_FILLED
        self.assertEqual(a.index(bar_start), b.index(bar_start))
        self.assertEqual(a.index("%"), b.index("%"))

    def test_row_without_reset_time_has_no_trailing_separator(self):
        row = plugin.render._usage_row("Week", 7, 40, "")
        self.assertTrue(row.rstrip().endswith("%"))
        self.assertNotIn("·", row)


class TestUsageSnapshotParsing(unittest.TestCase):
    """Pure helpers that turn a ``get_usage`` payload into a snapshot row."""

    def test_iso_to_epoch_offset_form(self):
        # The form the API actually sends (microseconds + explicit offset).
        self.assertEqual(
            plugin.usage_monitor._iso_to_epoch("2026-08-27T16:49:59.947591+00:00"),
            1787849399,
        )

    def test_iso_to_epoch_accepts_z_suffix(self):
        # Python 3.9's fromisoformat rejects "Z" outright — we normalise it.
        self.assertEqual(
            plugin.usage_monitor._iso_to_epoch("2026-08-27T16:49:59+00:00"),
            plugin.usage_monitor._iso_to_epoch("2026-08-27T16:49:59Z"),
        )

    def test_iso_to_epoch_garbage_is_zero(self):
        for value in (None, "", "yesterday", 17, {}, True):
            self.assertEqual(plugin.usage_monitor._iso_to_epoch(value), 0, value)

    def test_snapshot_row_shape(self):
        row = plugin.usage_monitor.snapshot_from_rate_limits(
            {
                "five_hour": {"utilization": 10.9, "resets_at": "2026-08-27T16:49:59+00:00"},
                "seven_day": {"utilization": 41, "resets_at": "2026-08-28T19:59:59+00:00"},
            },
            1787838000,
        )
        # Percentages floor (10.9 → 10) so we never overstate usage.
        self.assertEqual(
            row, "1787838000\t10\t1787849399\t41\t1787947199"
        )

    def test_snapshot_needs_the_five_hour_window(self):
        m = plugin.usage_monitor.snapshot_from_rate_limits
        self.assertIsNone(m({"seven_day": {"utilization": 41, "resets_at": "2026-08-28T19:59:59+00:00"}}, 1))
        self.assertIsNone(m({"five_hour": None}, 1))
        self.assertIsNone(m({"five_hour": {"utilization": 10, "resets_at": None}}, 1))
        self.assertIsNone(m(None, 1))

    def test_snapshot_tolerates_a_missing_weekly_window(self):
        row = plugin.usage_monitor.snapshot_from_rate_limits(
            {
                "five_hour": {"utilization": 3, "resets_at": "2026-08-27T16:49:59+00:00"},
                "seven_day": None,
            },
            100,
        )
        self.assertEqual(row, "100\t3\t1787849399\t0\t0")

    def test_rate_limits_from_control_response(self):
        stdout = (
            '{"type":"system","subtype":"init"}\n'
            '{"type":"control_response","response":{"subtype":"success",'
            '"request_id":"cab_usage","response":{"rate_limits_available":true,'
            '"rate_limits":{"five_hour":{"utilization":10,"resets_at":null}}}}}\n'
        )
        self.assertEqual(
            plugin.usage_monitor.rate_limits_from_response(stdout),
            {"five_hour": {"utilization": 10, "resets_at": None}},
        )

    def test_rate_limits_none_when_plan_has_no_limits(self):
        # API-key / Bedrock / Vertex sessions: rate_limits_available false.
        stdout = (
            '{"type":"control_response","response":{"subtype":"success",'
            '"response":{"rate_limits_available":false,"rate_limits":null}}}'
        )
        self.assertIsNone(plugin.usage_monitor.rate_limits_from_response(stdout))

    def test_rate_limits_none_on_error_or_noise(self):
        m = plugin.usage_monitor.rate_limits_from_response
        self.assertIsNone(m(""))
        self.assertIsNone(m("not json at all\n{broken"))
        self.assertIsNone(m('{"type":"control_response","response":{"subtype":"error"}}'))


class TestCachedUtilization(unittest.TestCase):
    """Reading Claude Code's own ``cachedUsageUtilization`` out of ~/.claude.json."""

    def _doc(self, fetched_ms):
        return {
            "cachedUsageUtilization": {
                "fetchedAtMs": fetched_ms,
                "utilization": {"five_hour": {"utilization": 10, "resets_at": None}},
            }
        }

    def test_fresh_cache_returns_payload_and_its_own_timestamp(self):
        now = 1_700_000_000
        got = plugin.usage_monitor.cached_rate_limits(self._doc((now - 60) * 1000), now, 180)
        self.assertIsNotNone(got)
        rate_limits, fetched_ts = got
        # The timestamp is when Claude Code fetched it, not when we read it —
        # a cached snapshot must not masquerade as fresh.
        self.assertEqual(fetched_ts, now - 60)
        self.assertIn("five_hour", rate_limits)

    def test_cache_older_than_bound_is_ignored(self):
        now = 1_700_000_000
        self.assertIsNone(
            plugin.usage_monitor.cached_rate_limits(self._doc((now - 600) * 1000), now, 180)
        )

    def test_cache_from_the_future_is_ignored(self):
        now = 1_700_000_000
        self.assertIsNone(
            plugin.usage_monitor.cached_rate_limits(self._doc((now + 600) * 1000), now, 180)
        )

    def test_malformed_cache_is_ignored(self):
        m = plugin.usage_monitor.cached_rate_limits
        now = 1_700_000_000
        self.assertIsNone(m(None, now, 180))
        self.assertIsNone(m({}, now, 180))
        self.assertIsNone(m({"cachedUsageUtilization": {"fetchedAtMs": "soon"}}, now, 180))
        self.assertIsNone(
            m({"cachedUsageUtilization": {"fetchedAtMs": now * 1000}}, now, 180)
        )


class TestUsageFetch(unittest.TestCase):
    """Source precedence in :func:`usage_monitor.fetch`: cache → CLI → stale cache."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._orig_usage = plugin.core.USAGE_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_PATH = self._tmpdir / "agent-state.usage"
        plugin.core.CONFIG = plugin.Config(usage_fetch_interval_sec=180)
        self.now = 1_700_000_000
        self.reset_iso = "2026-08-27T16:49:59+00:00"

    def tearDown(self):
        import shutil
        plugin.core.USAGE_PATH = self._orig_usage
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _limits(self, pct):
        return {"five_hour": {"utilization": pct, "resets_at": self.reset_iso}}

    def _cache_doc(self, pct, age_sec):
        return {
            "cachedUsageUtilization": {
                "fetchedAtMs": (self.now - age_sec) * 1000,
                "utilization": self._limits(pct),
            }
        }

    def _run(self, cache_doc, query_result):
        with patch.object(plugin.usage_monitor, "_read_claude_json", return_value=cache_doc), \
             patch.object(plugin.usage_monitor, "_query_claude", return_value=query_result) as q:
            rc = plugin.usage_monitor.fetch(self.now)
        row = (
            plugin.core.USAGE_PATH.read_text(encoding="utf-8")
            if plugin.core.USAGE_PATH.exists() else ""
        )
        return rc, row, q.call_count

    def test_fresh_cache_short_circuits_the_cli(self):
        rc, row, calls = self._run(self._cache_doc(11, 60), self._limits(99))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, 0)          # no process spawned at all
        self.assertEqual(row.split("\t")[1], "11")
        self.assertEqual(row.split("\t")[0], str(self.now - 60))  # cache's own ts

    def test_stale_cache_falls_through_to_the_cli(self):
        rc, row, calls = self._run(self._cache_doc(11, 600), self._limits(22))
        self.assertEqual(rc, 0)
        self.assertEqual(calls, 1)
        self.assertEqual(row.split("\t")[1], "22")
        self.assertEqual(row.split("\t")[0], str(self.now))

    def test_failed_cli_falls_back_to_an_hour_old_cache(self):
        rc, row, _ = self._run(self._cache_doc(11, 1800), None)
        self.assertEqual(rc, 0)
        self.assertEqual(row.split("\t")[1], "11")

    def test_nothing_written_when_every_source_fails(self):
        rc, row, _ = self._run(None, None)
        self.assertEqual(rc, 1)
        self.assertEqual(row, "")

    def test_previous_snapshot_survives_a_failed_fetch(self):
        plugin.core.USAGE_PATH.write_text("1\t5\t2\t6\t3", encoding="utf-8")
        rc, row, _ = self._run(None, None)
        self.assertEqual(rc, 1)
        self.assertEqual(row, "1\t5\t2\t6\t3")   # left alone to age out


class TestUsageReconcile(unittest.TestCase):
    """Throttling in :func:`usage_monitor.reconcile` — the spawn is patched out."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._orig_fetch_path = plugin.core.USAGE_FETCH_PATH
        self._orig_config = plugin.core.CONFIG
        plugin.core.USAGE_FETCH_PATH = self._tmpdir / "usage.fetch"
        plugin.core.CONFIG = plugin.Config(usage_fetch_interval_sec=180)
        self.now = 1_700_000_000

    def tearDown(self):
        import shutil
        plugin.core.USAGE_FETCH_PATH = self._orig_fetch_path
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self, now):
        with patch.object(plugin.usage_monitor, "_spawn_fetch") as spawn:
            plugin.usage_monitor.reconcile(now)
        return spawn.call_count

    def test_first_tick_fetches(self):
        self.assertEqual(self._run(self.now), 1)

    def test_marker_is_written_before_the_spawn(self):
        # Written up front so a hung fetch can't make the next tick spawn a
        # second one.
        self._run(self.now)
        self.assertEqual(plugin.usage_monitor._read_fetch_ts(), self.now)

    def test_within_interval_is_a_noop(self):
        self._run(self.now)
        self.assertEqual(self._run(self.now + 179), 0)

    def test_past_interval_fetches_again(self):
        self._run(self.now)
        self.assertEqual(self._run(self.now + 180), 1)

    def test_feature_off_never_fetches(self):
        plugin.core.CONFIG = plugin.Config(usage_monitor="off")
        self.assertEqual(self._run(self.now), 0)

    def test_corrupt_marker_reads_as_never_fetched(self):
        plugin.core.USAGE_FETCH_PATH.write_text("half past three", encoding="utf-8")
        self.assertEqual(self._run(self.now), 1)



class TestLegacyRetirement(unittest.TestCase):
    """Upgrades that never re-ran ``setup`` still lose the 1.4 background session."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        self._orig_ping = plugin.usage_monitor._LEGACY_PING_PATH
        self._orig_dir = plugin.usage_monitor._LEGACY_MONITOR_DIR
        plugin.usage_monitor._LEGACY_PING_PATH = self._tmpdir / "usage-monitor.ping"
        plugin.usage_monitor._LEGACY_MONITOR_DIR = self._tmpdir / "cab-usage-monitor"

    def tearDown(self):
        import shutil
        plugin.usage_monitor._LEGACY_PING_PATH = self._orig_ping
        plugin.usage_monitor._LEGACY_MONITOR_DIR = self._orig_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _run(self):
        with patch.object(plugin.usage_monitor, "kill_legacy_screen") as kill:
            plugin.usage_monitor._retire_legacy()
        return kill.call_count

    def test_no_leftovers_costs_nothing(self):
        # The common case on a clean install: two exists() calls, no `screen`.
        self.assertEqual(self._run(), 0)

    def test_ping_marker_triggers_the_kill_and_is_removed(self):
        plugin.usage_monitor._LEGACY_PING_PATH.write_text("1700000000\n")
        self.assertEqual(self._run(), 1)
        self.assertFalse(plugin.usage_monitor._LEGACY_PING_PATH.exists())

    def test_empty_workdir_is_removed(self):
        plugin.usage_monitor._LEGACY_MONITOR_DIR.mkdir()
        self.assertEqual(self._run(), 1)
        self.assertFalse(plugin.usage_monitor._LEGACY_MONITOR_DIR.exists())

    def test_workdir_with_user_files_is_left_alone(self):
        d = plugin.usage_monitor._LEGACY_MONITOR_DIR
        d.mkdir()
        (d / "notes.txt").write_text("mine")
        self.assertEqual(self._run(), 1)
        self.assertTrue((d / "notes.txt").exists())

    def test_retirement_runs_on_every_fetch(self):
        with patch.object(plugin.usage_monitor, "_retire_legacy") as retire, \
             patch.object(plugin.usage_monitor, "_read_claude_json", return_value=None), \
             patch.object(plugin.usage_monitor, "_query_claude", return_value=None):
            plugin.usage_monitor.fetch(1_700_000_000)
        self.assertEqual(retire.call_count, 1)


if __name__ == "__main__":
    unittest.main()
