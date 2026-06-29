"""Quiet hours and keep-awake decisions; multi-workspace toggle.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import tempfile
import datetime as _dt
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin


class TestParseQuietWindow(unittest.TestCase):
    """Spec 0002 — strict ``HH:MM-HH:MM`` parsing."""

    def test_accepts_canonical_form(self):
        self.assertEqual(
            plugin._parse_quiet_window("23:00-09:00"),
            (_dt.time(23, 0), _dt.time(9, 0)),
        )

    def test_accepts_non_wrapping(self):
        self.assertEqual(
            plugin._parse_quiet_window("13:00-15:30"),
            (_dt.time(13, 0), _dt.time(15, 30)),
        )

    def test_rejects_start_equal_end(self):
        # Zero-length window — never quiet, never active.
        self.assertIsNone(plugin._parse_quiet_window("03:00-03:00"))

    def test_rejects_out_of_range_hours(self):
        self.assertIsNone(plugin._parse_quiet_window("24:00-09:00"))
        self.assertIsNone(plugin._parse_quiet_window("23:60-09:00"))

    def test_rejects_missing_zero_pad(self):
        # Strict format: 9:00 is not accepted, only 09:00.
        self.assertIsNone(plugin._parse_quiet_window("9:00-17:00"))

    def test_rejects_garbage(self):
        self.assertIsNone(plugin._parse_quiet_window("garbage"))
        self.assertIsNone(plugin._parse_quiet_window(""))
        self.assertIsNone(plugin._parse_quiet_window(None))



class TestQuietWindowActive(unittest.TestCase):
    """Spec 0002 — interval check with midnight wrap."""

    def test_non_wrapping_active_inside(self):
        self.assertTrue(plugin._quiet_window_active(
            _dt.time(14, 0), _dt.time(13, 0), _dt.time(15, 0),
        ))

    def test_non_wrapping_inactive_outside(self):
        self.assertFalse(plugin._quiet_window_active(
            _dt.time(12, 0), _dt.time(13, 0), _dt.time(15, 0),
        ))

    def test_non_wrapping_end_is_exclusive(self):
        # Half-open: 15:00 sharp → no longer quiet.
        self.assertFalse(plugin._quiet_window_active(
            _dt.time(15, 0), _dt.time(13, 0), _dt.time(15, 0),
        ))

    def test_wrapping_after_start(self):
        self.assertTrue(plugin._quiet_window_active(
            _dt.time(23, 30), _dt.time(23, 0), _dt.time(9, 0),
        ))

    def test_wrapping_before_end(self):
        self.assertTrue(plugin._quiet_window_active(
            _dt.time(2, 0), _dt.time(23, 0), _dt.time(9, 0),
        ))

    def test_wrapping_at_start_is_inclusive(self):
        self.assertTrue(plugin._quiet_window_active(
            _dt.time(23, 0), _dt.time(23, 0), _dt.time(9, 0),
        ))

    def test_wrapping_at_end_is_exclusive(self):
        self.assertFalse(plugin._quiet_window_active(
            _dt.time(9, 0), _dt.time(23, 0), _dt.time(9, 0),
        ))

    def test_wrapping_outside_window(self):
        self.assertFalse(plugin._quiet_window_active(
            _dt.time(12, 0), _dt.time(23, 0), _dt.time(9, 0),
        ))



class TestNextOccurrence(unittest.TestCase):
    """Spec 0002 — next wall-clock occurrence helper."""

    def test_today_when_in_future(self):
        now = _dt.datetime(2026, 5, 26, 8, 0, 0)
        nxt = plugin._next_occurrence(now, _dt.time(9, 0))
        self.assertEqual(nxt, _dt.datetime(2026, 5, 26, 9, 0, 0))

    def test_tomorrow_when_already_passed(self):
        now = _dt.datetime(2026, 5, 26, 10, 0, 0)
        nxt = plugin._next_occurrence(now, _dt.time(9, 0))
        self.assertEqual(nxt, _dt.datetime(2026, 5, 27, 9, 0, 0))

    def test_tomorrow_when_exactly_now(self):
        # Exactly-now target rolls to tomorrow — we want strict ">"
        # so a click at 09:00 sharp doesn't pause for 0 seconds.
        now = _dt.datetime(2026, 5, 26, 9, 0, 0)
        nxt = plugin._next_occurrence(now, _dt.time(9, 0))
        self.assertEqual(nxt, _dt.datetime(2026, 5, 27, 9, 0, 0))



class TestQuietStatus(unittest.TestCase):
    """Spec 0002 — composite Tools-submenu status."""

    def setUp(self):
        # Monday 2026-05-26 22:30 local — late evening, before the
        # canonical 23:00-09:00 window starts.
        self.evening = _dt.datetime(2026, 5, 25, 22, 30)
        # Same day at 23:30 — inside a wrapping 23:00-09:00 window.
        self.wrap_active = _dt.datetime(2026, 5, 25, 23, 30)

    def test_off_when_nothing_configured(self):
        status = plugin.quiet_status(self.evening, None, None)
        self.assertEqual(status, {"kind": "off"})

    def test_scheduled_inactive_emits_until_start(self):
        status = plugin.quiet_status(self.evening, "23:00-09:00", None)
        self.assertEqual(status["kind"], "scheduled_inactive")
        self.assertEqual(status["start"], "23:00")
        self.assertEqual(status["end"], "09:00")
        # 22:30 → 23:00 is 30 minutes.
        self.assertEqual(status["scheduled_until_start"], 30 * 60)

    def test_scheduled_active_wraps_midnight(self):
        status = plugin.quiet_status(self.wrap_active, "23:00-09:00", None)
        self.assertEqual(status["kind"], "scheduled_active")
        # 23:30 → next 09:00 = 9h 30m = 34200s.
        self.assertEqual(status["scheduled_remaining"], 9 * 3600 + 30 * 60)

    def test_paused_alone(self):
        until = self.evening + _dt.timedelta(minutes=15)
        status = plugin.quiet_status(self.evening, None, until)
        self.assertEqual(status["kind"], "paused")
        self.assertEqual(status["paused_remaining"], 15 * 60)

    def test_paused_overrides_scheduled_for_kind(self):
        # Both gates active — we surface the paused kind so Resume is
        # the relevant action; scheduled info still travels in the dict.
        until = self.wrap_active + _dt.timedelta(minutes=20)
        status = plugin.quiet_status(self.wrap_active, "23:00-09:00", until)
        self.assertEqual(status["kind"], "paused_and_scheduled_active")
        self.assertEqual(status["paused_remaining"], 20 * 60)
        self.assertEqual(status["start"], "23:00")

    def test_past_paused_until_is_treated_as_not_paused(self):
        status = plugin.quiet_status(
            self.evening, None, self.evening - _dt.timedelta(minutes=1),
        )
        self.assertEqual(status["kind"], "off")



class TestIsQuietNow(unittest.TestCase):
    """Convenience predicate over :func:`quiet_status`."""

    def test_off_means_not_quiet(self):
        now = _dt.datetime(2026, 5, 26, 12, 0)
        self.assertFalse(plugin.is_quiet_now(now, None, None))

    def test_paused_means_quiet(self):
        now = _dt.datetime(2026, 5, 26, 12, 0)
        self.assertTrue(plugin.is_quiet_now(
            now, None, now + _dt.timedelta(minutes=1),
        ))

    def test_scheduled_inactive_means_not_quiet(self):
        now = _dt.datetime(2026, 5, 26, 22, 30)
        self.assertFalse(plugin.is_quiet_now(now, "23:00-09:00", None))



class TestQuietHoursConfigLoad(unittest.TestCase):
    """Spec 0002 — Config validation for the two new fields."""

    def _load(self, data: dict) -> "plugin.Config":
        return plugin.Config._from_mapping(data)

    def test_accepts_valid_window(self):
        cfg = self._load({"quiet_hours": "23:00-09:00"})
        self.assertEqual(cfg.quiet_hours, "23:00-09:00")

    def test_explicit_null_disables(self):
        cfg = self._load({"quiet_hours": None})
        self.assertIsNone(cfg.quiet_hours)

    def test_malformed_window_falls_back_to_default(self):
        # The default is opinionated (an overnight window). Compare
        # against the dataclass field default rather than hard-coding
        # the literal so the test survives a default change.
        default = plugin.Config().quiet_hours
        cfg = self._load({"quiet_hours": "11pm-7am"})
        self.assertEqual(cfg.quiet_hours, default)

    def test_silences_filters_unknown_channels(self):
        cfg = self._load(
            {"quiet_hours_silences": ["sound", "wifi", "voice"]},
        )
        self.assertEqual(cfg.quiet_hours_silences, ("sound", "voice"))

    def test_silences_dedupes(self):
        cfg = self._load(
            {"quiet_hours_silences": ["sound", "sound", "banner"]},
        )
        self.assertEqual(cfg.quiet_hours_silences, ("sound", "banner"))

    def test_silences_empty_list(self):
        cfg = self._load({"quiet_hours_silences": []})
        self.assertEqual(cfg.quiet_hours_silences, ())

    def test_silences_non_list_keeps_default(self):
        default = plugin.Config().quiet_hours_silences
        cfg = self._load({"quiet_hours_silences": "all"})
        self.assertEqual(cfg.quiet_hours_silences, default)



class _FakeSession:
    """Minimal Session stand-in for the keep_awake decision helper."""

    def __init__(self, hook_state: str) -> None:
        self.hook_state = hook_state



class TestKeepAwakeDecide(unittest.TestCase):
    """Spec 0003 — :func:`keep_awake._decide_should_run`."""

    def test_off_never_runs(self):
        from claude_agents_bar import keep_awake
        self.assertFalse(keep_awake._decide_should_run("off", []))
        self.assertFalse(keep_awake._decide_should_run(
            "off", [_FakeSession("working")],
        ))

    def test_always_always_runs(self):
        from claude_agents_bar import keep_awake
        self.assertTrue(keep_awake._decide_should_run("always", []))
        self.assertTrue(keep_awake._decide_should_run(
            "always", [_FakeSession("idle")],
        ))

    def test_auto_runs_only_for_working(self):
        from claude_agents_bar import keep_awake
        self.assertFalse(keep_awake._decide_should_run("auto", []))
        # waiting is the user, not the model — don't keep awake.
        self.assertFalse(keep_awake._decide_should_run(
            "auto", [_FakeSession("waiting")],
        ))
        self.assertTrue(keep_awake._decide_should_run(
            "auto", [_FakeSession("idle"), _FakeSession("working")],
        ))



class TestKeepAwakeCurrentMode(unittest.TestCase):
    """Spec 0003 — sidecar takes precedence over config default."""

    def setUp(self):
        from claude_agents_bar import keep_awake
        self.keep_awake = keep_awake
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sidecar = Path(self.tmp.name) / "agent-state.keep-awake.mode"

    def test_falls_back_to_config_when_sidecar_absent(self):
        with patch.object(
            plugin.core, "KEEP_AWAKE_MODE_PATH", self.sidecar,
        ), patch.object(plugin.core, "CONFIG", plugin.Config(keep_awake="auto")):
            self.assertEqual(self.keep_awake.current_mode(), "auto")

    def test_sidecar_wins_over_config(self):
        self.sidecar.write_text("always\n", encoding="utf-8")
        with patch.object(
            plugin.core, "KEEP_AWAKE_MODE_PATH", self.sidecar,
        ), patch.object(plugin.core, "CONFIG", plugin.Config(keep_awake="off")):
            self.assertEqual(self.keep_awake.current_mode(), "always")

    def test_unknown_sidecar_falls_through(self):
        self.sidecar.write_text("garbage\n", encoding="utf-8")
        with patch.object(
            plugin.core, "KEEP_AWAKE_MODE_PATH", self.sidecar,
        ), patch.object(plugin.core, "CONFIG", plugin.Config(keep_awake="auto")):
            self.assertEqual(self.keep_awake.current_mode(), "auto")



class TestMultiWorkspaceEnabled(unittest.TestCase):
    """Tools → Multi-workspace toggle: sidecar wins over the config knob."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sidecar = Path(self.tmp.name) / "agent-state.multi-workspace.mode"

    def _ctx(self, config_value):
        return (
            patch.object(plugin.core, "MULTI_WORKSPACE_MODE_PATH", self.sidecar),
            patch.object(
                plugin.core, "CONFIG",
                plugin.Config(multi_workspace_mode=config_value),
            ),
        )

    def test_falls_back_to_config_when_sidecar_absent(self):
        p1, p2 = self._ctx(False)
        with p1, p2:
            self.assertFalse(plugin.core.multi_workspace_enabled())

    def test_sidecar_off_wins_over_config_on(self):
        self.sidecar.write_text("off\n", encoding="utf-8")
        p1, p2 = self._ctx(True)
        with p1, p2:
            self.assertFalse(plugin.core.multi_workspace_enabled())

    def test_sidecar_on_wins_over_config_off(self):
        self.sidecar.write_text("on\n", encoding="utf-8")
        p1, p2 = self._ctx(False)
        with p1, p2:
            self.assertTrue(plugin.core.multi_workspace_enabled())

    def test_unknown_sidecar_falls_through_to_config(self):
        self.sidecar.write_text("garbage\n", encoding="utf-8")
        p1, p2 = self._ctx(True)
        with p1, p2:
            self.assertTrue(plugin.core.multi_workspace_enabled())

    def test_write_round_trips(self):
        with patch.object(
            plugin.core, "MULTI_WORKSPACE_MODE_PATH", self.sidecar,
        ):
            self.assertEqual(plugin.core.write_multi_workspace_mode(False), 0)
            self.assertEqual(self.sidecar.read_text().strip(), "off")
            self.assertEqual(plugin.core.write_multi_workspace_mode(True), 0)
            self.assertEqual(self.sidecar.read_text().strip(), "on")



class TestKeepAwakeConfigLoad(unittest.TestCase):
    """Spec 0003 — Config validation for ``keep_awake``."""

    def _load(self, data: dict) -> "plugin.Config":
        return plugin.Config._from_mapping(data)

    def test_accepts_valid_modes(self):
        for mode in ("off", "auto", "always"):
            self.assertEqual(self._load({"keep_awake": mode}).keep_awake, mode)

    def test_invalid_mode_falls_back(self):
        self.assertEqual(self._load({"keep_awake": "yes"}).keep_awake, "off")
        self.assertEqual(self._load({"keep_awake": None}).keep_awake, "off")



class TestReadQuietUntil(unittest.TestCase):
    """Round-trip of the ad-hoc pause sidecar parser."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sidecar = Path(self.tmp.name) / "agent-state.quiet-until"

    def _read(self):
        with patch.object(plugin.core, "QUIET_UNTIL_PATH", self.sidecar):
            return plugin.read_quiet_until()

    def test_missing_file_returns_none(self):
        self.assertIsNone(self._read())

    def test_empty_file_returns_none(self):
        self.sidecar.write_text("", encoding="utf-8")
        self.assertIsNone(self._read())

    def test_unparseable_returns_none(self):
        self.sidecar.write_text("not a date\n", encoding="utf-8")
        self.assertIsNone(self._read())

    def test_past_timestamp_returns_none(self):
        past = _dt.datetime.now() - _dt.timedelta(hours=1)
        self.sidecar.write_text(
            past.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8",
        )
        self.assertIsNone(self._read())

    def test_future_timestamp_returns_datetime(self):
        future = (_dt.datetime.now() + _dt.timedelta(hours=1)).replace(
            microsecond=0,
        )
        self.sidecar.write_text(
            future.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8",
        )
        got = self._read()
        self.assertEqual(got, future)


if __name__ == "__main__":
    unittest.main()
