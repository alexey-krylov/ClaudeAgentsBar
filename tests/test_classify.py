"""Session state classification and the render-group predicates.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import unittest

from _helpers import plugin, _make_session


class TestClassify(unittest.TestCase):
    """End-to-end rules for the four render buckets.

    ``_classify(state, now, stop_ts, effective_click_ts, last_event_kind)``
    — we synthesise timestamps relative to ``CONFIG`` so the tests stay
    readable even when the defaults change. The default kind is ``Stop``
    so the legacy idle-lifecycle tests still cover the FRESH grace
    window; tests at the bottom of this class pin the behavior for
    non-Stop kinds (the case that paints the row green on every IDE
    tab switch — see CHANGELOG).
    """

    def setUp(self):
        self.fresh = plugin.CONFIG.fresh_sec
        self.ack = plugin.CONFIG.ack_sec
        self.stop_ts = 1_700_000_000
        # 0 = "no click happened after the most recent Stop"
        self.no_click = 0

    def test_waiting_is_active(self):
        self.assertEqual(
            plugin._classify(
                "waiting", self.stop_ts, self.stop_ts, self.no_click, "Notification",
            ),
            plugin.RenderGroup.ACTIVE,
        )

    def test_working_is_active_regardless_of_age(self):
        # Active sessions stay active in classification — the watchdog demotes
        # them upstream in ``build_session`` before this is called.
        very_old = self.stop_ts + 10_000_000
        self.assertEqual(
            plugin._classify(
                "working", very_old, self.stop_ts, self.no_click, "PreToolUse",
            ),
            plugin.RenderGroup.ACTIVE,
        )

    def test_idle_no_click_under_fresh_window(self):
        # First minute after Stop, no click yet → 🟢.
        now = self.stop_ts + 60
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, self.no_click, "Stop"),
            plugin.RenderGroup.FRESH,
        )

    def test_idle_no_click_in_ack_window(self):
        # fresh_sec has elapsed without a click → auto-promote to 🔵.
        now = self.stop_ts + self.fresh + 1
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, self.no_click, "Stop"),
            plugin.RenderGroup.ACKNOWLEDGED,
        )

    def test_idle_no_click_past_ack_window(self):
        # Both fresh and ack windows have elapsed without any click → ⚪.
        now = self.stop_ts + self.fresh + self.ack + 1
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, self.no_click, "Stop"),
            plugin.RenderGroup.STALE,
        )

    def test_idle_click_during_fresh_promotes_immediately(self):
        # Click landed at +30s, well inside fresh window. Now is +90s.
        # The click moves us straight to 🔵 — fresh ends at click_ts, not
        # at stop_ts + fresh_sec.
        click_ts = self.stop_ts + 30
        now = self.stop_ts + 90
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, click_ts, "Stop"),
            plugin.RenderGroup.ACKNOWLEDGED,
        )

    def test_idle_click_resets_stale_timer(self):
        # User clicked late in the ack window. ack timer restarts from
        # click_ts, so an extra ack_sec must pass before STALE.
        click_ts = self.stop_ts + self.fresh + self.ack - 60
        now_just_after = click_ts + self.ack - 1
        now_after_window = click_ts + self.ack + 1
        self.assertEqual(
            plugin._classify("idle", now_just_after, self.stop_ts, click_ts, "Stop"),
            plugin.RenderGroup.ACKNOWLEDGED,
        )
        self.assertEqual(
            plugin._classify("idle", now_after_window, self.stop_ts, click_ts, "Stop"),
            plugin.RenderGroup.STALE,
        )

    # ------------------------- FRESH guard tests ----------------------- #
    # These are the cases that triggered the "everything turned green
    # after I clicked through some tabs" report. FRESH must only fire on
    # an actual Stop event; anything else collapses the fresh window.

    def test_idle_session_start_kind_is_not_fresh(self):
        # An IDE tab switch fires SessionStart → our hook resolves to
        # idle with last_event_kind="SessionStart". This must NOT paint
        # the row green: nothing was just "finished".
        now = self.stop_ts + 60
        self.assertEqual(
            plugin._classify(
                "idle", now, self.stop_ts, self.no_click, "SessionStart",
            ),
            plugin.RenderGroup.ACKNOWLEDGED,
        )

    def test_idle_empty_kind_is_not_fresh(self):
        # Empty kind = session has no TSV row at all (fallback in
        # build_session) OR watchdog cleared the kind on a stale
        # "working". Neither is a real Stop, so no green.
        now = self.stop_ts + 60
        self.assertEqual(
            plugin._classify(
                "idle", now, self.stop_ts, self.no_click, "",
            ),
            plugin.RenderGroup.ACKNOWLEDGED,
        )

    def test_idle_non_stop_kind_falls_through_to_stale(self):
        # Same as above but timestamps are old: no FRESH grace, no ACK
        # grace either — straight to STALE.
        now = self.stop_ts + self.ack + 1
        self.assertEqual(
            plugin._classify(
                "idle", now, self.stop_ts, self.no_click, "SessionStart",
            ),
            plugin.RenderGroup.STALE,
        )



class TestPredicates(unittest.TestCase):
    def test_interactive_entrypoints(self):
        self.assertTrue(plugin._is_interactive(_make_session(entrypoint="claude-vscode")))
        self.assertTrue(plugin._is_interactive(_make_session(entrypoint="cli")))

    def test_unset_entrypoint_treated_as_interactive(self):
        # Backwards-compat: older transcripts may not carry an entrypoint at
        # all; we'd rather show one too many sessions than swallow real ones.
        self.assertTrue(plugin._is_interactive(_make_session(entrypoint="")))

    def test_headless_entrypoints_filtered(self):
        self.assertFalse(plugin._is_interactive(_make_session(entrypoint="sdk-cli")))
        self.assertFalse(plugin._is_interactive(_make_session(entrypoint="some-future-bot")))


if __name__ == "__main__":
    unittest.main()
