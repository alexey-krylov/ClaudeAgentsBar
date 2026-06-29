"""Idle-reminder config, sidecar persistence, and reconciliation.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin, _make_session


class TestIdleReminderConfig(unittest.TestCase):
    """``notify_idle_interval_min`` → ``notify_idle_interval_sec``.

    The knob doubles as the on/off switch, so the loader can't use the
    generic ``take`` helper: an explicit ``null`` and ``0`` must both map
    to "off" (sec 0) rather than "keep the default", and a negative value
    clamps to off instead of scheduling a reminder in the past.
    """

    def test_default_enabled_at_thirty_minutes(self):
        self.assertEqual(plugin.Config().notify_idle_interval_sec, 30 * 60)

    def test_minutes_to_seconds(self):
        config = plugin.Config._from_mapping({"notify_idle_interval_min": 30})
        self.assertEqual(config.notify_idle_interval_sec, 1800)

    def test_fractional_minutes(self):
        config = plugin.Config._from_mapping({"notify_idle_interval_min": 0.5})
        self.assertEqual(config.notify_idle_interval_sec, 30)

    def test_zero_disables(self):
        config = plugin.Config._from_mapping({"notify_idle_interval_min": 0})
        self.assertEqual(config.notify_idle_interval_sec, 0)

    def test_null_disables(self):
        config = plugin.Config._from_mapping({"notify_idle_interval_min": None})
        self.assertEqual(config.notify_idle_interval_sec, 0)

    def test_negative_clamps_to_off(self):
        config = plugin.Config._from_mapping({"notify_idle_interval_min": -5})
        self.assertEqual(config.notify_idle_interval_sec, 0)

    def test_non_number_keeps_default(self):
        config = plugin.Config._from_mapping(
            {"notify_idle_interval_min": "soon"}
        )
        self.assertEqual(config.notify_idle_interval_sec, 30 * 60)

    def test_bool_keeps_default(self):
        # JSON ``true`` is a bool (subclass of int) — a config mistake, not
        # a duration; the loader rejects it and keeps the default.
        config = plugin.Config._from_mapping(
            {"notify_idle_interval_min": True}
        )
        self.assertEqual(config.notify_idle_interval_sec, 30 * 60)

    def test_absent_keeps_default(self):
        config = plugin.Config._from_mapping({"window_minutes": 60})
        self.assertEqual(config.notify_idle_interval_sec, 30 * 60)



class TestIdleRemindersSidecar(unittest.TestCase):
    """Round-trip + fail-soft for the ``agent-state.idle-reminders`` sidecar."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        path = self._tmpdir / "idle-reminders"
        self._orig_path = plugin.core.IDLE_REMINDERS_PATH
        self._orig_lock = plugin.core._IDLE_REMINDERS_LOCK_DIR
        plugin.core.IDLE_REMINDERS_PATH = path
        plugin.core._IDLE_REMINDERS_LOCK_DIR = path.with_suffix(
            path.suffix + ".lock.d"
        )
        self.path = path

    def tearDown(self):
        import shutil
        plugin.core.IDLE_REMINDERS_PATH = self._orig_path
        plugin.core._IDLE_REMINDERS_LOCK_DIR = self._orig_lock
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_file_reads_empty(self):
        self.assertEqual(plugin.read_idle_reminders(), {})

    def test_round_trip(self):
        state = {"a": (1700, 1), "b": (1800, 3)}
        plugin.write_idle_reminders(state)
        self.assertEqual(plugin.read_idle_reminders(), state)

    def test_empty_state_removes_file(self):
        plugin.write_idle_reminders({"a": (1700, 1)})
        self.assertTrue(self.path.exists())
        plugin.write_idle_reminders({})
        self.assertFalse(self.path.exists())

    def test_corrupt_rows_dropped(self):
        # Missing column, non-int numbers — each dropped, good rows kept.
        self.path.write_text(
            "good\t1700\t2\n"
            "missingcol\t1700\n"
            "notint\tabc\t1\n"
            "alsogood\t1800\t1\n",
            encoding="utf-8",
        )
        self.assertEqual(
            plugin.read_idle_reminders(),
            {"good": (1700, 2), "alsogood": (1800, 1)},
        )



class TestIdleRemindersReconcile(unittest.TestCase):
    """The per-tick escalation logic in :func:`idle_reminders.reconcile`.

    ``_fire`` (which spawns the bash script) is patched out so tests only
    exercise the timing/state machine. The sidecar path and the live
    ``CONFIG`` interval are redirected per-test.
    """

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        path = self._tmpdir / "idle-reminders"
        self._orig_path = plugin.core.IDLE_REMINDERS_PATH
        self._orig_lock = plugin.core._IDLE_REMINDERS_LOCK_DIR
        self._orig_config = plugin.core.CONFIG
        plugin.core.IDLE_REMINDERS_PATH = path
        plugin.core._IDLE_REMINDERS_LOCK_DIR = path.with_suffix(
            path.suffix + ".lock.d"
        )
        self.path = path
        self.now = 1_700_000_000
        self.interval = 1200  # 20 min

    def tearDown(self):
        import shutil
        plugin.core.IDLE_REMINDERS_PATH = self._orig_path
        plugin.core._IDLE_REMINDERS_LOCK_DIR = self._orig_lock
        plugin.core.CONFIG = self._orig_config
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _set_interval(self, sec):
        plugin.core.CONFIG = plugin.Config(notify_idle_interval_sec=sec)

    def _fresh(self, sid, elapsed):
        """A 🟢 FRESH session whose Stop was ``elapsed`` seconds ago."""
        return _make_session(
            id=sid,
            group=plugin.RenderGroup.FRESH,
            last_event_ts=self.now - elapsed,
        )

    def _run(self, sessions):
        fired = []
        with patch.object(
            plugin.idle_reminders, "_fire",
            side_effect=lambda s: fired.append(s.id),
        ):
            plugin.idle_reminders.reconcile(sessions, self.now)
        return fired

    def test_feature_off_does_nothing(self):
        self._set_interval(0)
        fired = self._run([self._fresh("a", self.interval * 5)])
        self.assertEqual(fired, [])
        self.assertFalse(self.path.exists())

    def test_fires_first_reminder_past_threshold(self):
        self._set_interval(self.interval)
        fired = self._run([self._fresh("a", self.interval + 100)])
        self.assertEqual(fired, ["a"])
        self.assertEqual(plugin.read_idle_reminders(), {"a": (self.now - self.interval - 100, 1)})

    def test_no_fire_before_threshold(self):
        self._set_interval(self.interval)
        fired = self._run([self._fresh("a", self.interval - 100)])
        self.assertEqual(fired, [])
        # fired == 0 is the default-absent state, so nothing is persisted.
        self.assertEqual(plugin.read_idle_reminders(), {})

    def test_non_fresh_ignored(self):
        self._set_interval(self.interval)
        ack = _make_session(
            id="b", group=plugin.RenderGroup.ACKNOWLEDGED,
            last_event_ts=self.now - self.interval * 3,
        )
        active = _make_session(
            id="c", group=plugin.RenderGroup.ACTIVE,
            last_event_ts=self.now - self.interval * 3,
        )
        fired = self._run([ack, active])
        self.assertEqual(fired, [])
        self.assertEqual(plugin.read_idle_reminders(), {})

    def test_does_not_refire_within_same_window(self):
        self._set_interval(self.interval)
        # Already fired reminder #1; elapsed is past interval but below the
        # doubled (2*interval) threshold → no new fire.
        stop_ts = self.now - (self.interval + 200)
        plugin.write_idle_reminders({"a": (stop_ts, 1)})
        fired = self._run([self._fresh("a", self.interval + 200)])
        self.assertEqual(fired, [])
        self.assertEqual(plugin.read_idle_reminders(), {"a": (stop_ts, 1)})

    def test_fires_second_reminder_at_double(self):
        self._set_interval(self.interval)
        stop_ts = self.now - (self.interval * 2 + 50)
        plugin.write_idle_reminders({"a": (stop_ts, 1)})
        fired = self._run([self._fresh("a", self.interval * 2 + 50)])
        self.assertEqual(fired, ["a"])
        self.assertEqual(plugin.read_idle_reminders(), {"a": (stop_ts, 2)})

    def test_new_stop_resets_counter(self):
        self._set_interval(self.interval)
        # Old episode had fired twice; the session finished again (new
        # stop_ts), so the schedule restarts from reminder #1.
        plugin.write_idle_reminders({"a": (self.now - 99999, 2)})
        fired = self._run([self._fresh("a", self.interval + 10)])
        self.assertEqual(fired, ["a"])
        self.assertEqual(
            plugin.read_idle_reminders(), {"a": (self.now - self.interval - 10, 1)}
        )

    def test_catch_up_collapses_to_single_fire(self):
        # A long gap between ticks (e.g. machine asleep) crosses several
        # thresholds at once. 2500s past 1200s crosses 1200 (2^0) and 2400
        # (2^1); 4800 (2^2) is still ahead. The counter jumps straight to the
        # current level (2) but only ONE reminder fires — no back-to-back burst.
        self._set_interval(self.interval)
        fired = self._run([self._fresh("a", 2500)])
        self.assertEqual(fired, ["a"])
        self.assertEqual(plugin.read_idle_reminders(), {"a": (self.now - 2500, 2)})

    def test_left_fresh_session_pruned_from_sidecar(self):
        # A session that was being reminded but is no longer FRESH this tick
        # (clicked / promoted / gone) drops out of the rebuilt state.
        self._set_interval(self.interval)
        plugin.write_idle_reminders({"gone": (self.now - 5000, 2)})
        fired = self._run([])  # no FRESH sessions this tick
        self.assertEqual(fired, [])
        self.assertEqual(plugin.read_idle_reminders(), {})


if __name__ == "__main__":
    unittest.main()
