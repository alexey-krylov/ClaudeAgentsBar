"""Config dataclass loading, locale completeness, title opt-in flag.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import unittest

from _helpers import plugin


class TestConfigLoad(unittest.TestCase):
    def test_defaults(self):
        config = plugin.Config()
        self.assertEqual(config.window_sec, 180 * 60)
        self.assertEqual(config.fresh_sec, 60 * 60)
        self.assertEqual(config.ack_sec, 60 * 60)
        self.assertEqual(config.watchdog_sec, 90)
        self.assertEqual(config.title_max, 60)
        # Default points at Claude.app's tray template so the menubar
        # shows the Claude mark when Claude.app is installed; the
        # plugin's icon-resolver degrades silently when it isn't.
        self.assertTrue(config.menubar_icon.startswith("template:"))
        self.assertIn("Claude.app", config.menubar_icon)

    def test_window_minutes_to_seconds(self):
        config = plugin.Config._from_mapping({"window_minutes": 60})
        self.assertEqual(config.window_sec, 3600)

    def test_fresh_minutes_fractional(self):
        config = plugin.Config._from_mapping({"fresh_minutes": 0.5})
        self.assertEqual(config.fresh_sec, 30)

    def test_ack_minutes_override(self):
        config = plugin.Config._from_mapping({"ack_minutes": 15})
        self.assertEqual(config.ack_sec, 900)

    def test_watchdog_seconds_int(self):
        config = plugin.Config._from_mapping({"watchdog_seconds": 45})
        self.assertEqual(config.watchdog_sec, 45)

    def test_menubar_icon_override(self):
        config = plugin.Config._from_mapping({"menubar_icon": "✨"})
        self.assertEqual(config.menubar_icon, "✨")

    def test_invalid_value_keeps_default(self):
        # A garbage value for one knob must not destroy the others.
        config = plugin.Config._from_mapping(
            {"watchdog_seconds": "not-an-int", "window_minutes": 120}
        )
        self.assertEqual(config.watchdog_sec, 90)
        self.assertEqual(config.window_sec, 7200)

    def test_unknown_keys_ignored(self):
        config = plugin.Config._from_mapping(
            {"future_knob": "ignore me", "window_minutes": 60}
        )
        self.assertEqual(config.window_sec, 3600)

    def test_comment_keys_ignored(self):
        # Keys starting with ``//`` are valid documentation-style comments.
        config = plugin.Config._from_mapping(
            {"// comment": "anything", "window_minutes": 30}
        )
        self.assertEqual(config.window_sec, 1800)

    def test_compact_default_false(self):
        self.assertFalse(plugin.Config().compact)

    def test_compact_bool_override(self):
        config = plugin.Config._from_mapping({"compact": True})
        self.assertTrue(config.compact)

    def test_compact_non_bool_ignored(self):
        # ``bool("false") == True`` — the generic coercion path would
        # silently accept the wrong type, so the loader requires a real
        # JSON boolean. Strings, ints, etc. fall back to the default.
        for bogus in ("true", "false", 1, 0, None):
            config = plugin.Config._from_mapping({"compact": bogus})
            self.assertFalse(
                config.compact,
                f"non-bool compact={bogus!r} must not enable compact mode",
            )

    def test_context_window_tokens_default(self):
        # Default tracks Opus 4.7 / Opus 4.6 / Sonnet 4.6 — the current
        # Anthropic API default tier (since 2026-04-23).
        self.assertEqual(plugin.Config().context_window_tokens, 1_000_000)

    def test_context_window_tokens_override_down(self):
        # Haiku 4.5 / Sonnet 4.5 users override down to 200K.
        config = plugin.Config._from_mapping({"context_window_tokens": 200_000})
        self.assertEqual(config.context_window_tokens, 200_000)

    def test_context_window_tokens_rejects_non_positive(self):
        # ``_format_context_left`` returns an empty string for total<=0,
        # which would silently hide the row. Loader must reject and keep
        # the default loud-and-visible.
        for bogus in (0, -1, -200_000):
            config = plugin.Config._from_mapping({"context_window_tokens": bogus})
            self.assertEqual(
                config.context_window_tokens, 1_000_000,
                f"non-positive context_window_tokens={bogus!r} must fall back",
            )

    def test_context_window_tokens_rejects_garbage(self):
        # Non-numeric strings can't be coerced to int → fall back to default.
        config = plugin.Config._from_mapping({"context_window_tokens": "nope"})
        self.assertEqual(config.context_window_tokens, 1_000_000)

    def test_context_warning_threshold_default(self):
        # Default 80 % matches Claude Code's own yellow zone — early enough
        # to act on, late enough to avoid noise on regular sessions.
        self.assertEqual(plugin.Config().context_warning_threshold, 80)

    def test_context_warning_threshold_override(self):
        config = plugin.Config._from_mapping({"context_warning_threshold": 70})
        self.assertEqual(config.context_warning_threshold, 70)

    def test_context_warning_threshold_rejects_out_of_range(self):
        # 0 fires unconditionally and >100 is unreachable — neither is
        # what the user wants, so fall back to the default rather than
        # quietly clamping into a confused state.
        for bogus in (0, -10, 101, 200):
            config = plugin.Config._from_mapping(
                {"context_warning_threshold": bogus}
            )
            self.assertEqual(
                config.context_warning_threshold, 80,
                f"out-of-range threshold={bogus!r} must fall back",
            )

    def test_context_warning_threshold_rejects_garbage(self):
        config = plugin.Config._from_mapping({"context_warning_threshold": "nope"})
        self.assertEqual(config.context_warning_threshold, 80)

    def test_multi_workspace_mode_default(self):
        self.assertIs(plugin.Config().multi_workspace_mode, True)

    def test_multi_workspace_mode_accepts_bool(self):
        config = plugin.Config._from_mapping({"multi_workspace_mode": False})
        self.assertIs(config.multi_workspace_mode, False)

    def test_multi_workspace_mode_rejects_non_bool(self):
        # The string "false" is truthy — must not be coerced into flipping
        # the flag. Non-bool values keep the default.
        config = plugin.Config._from_mapping({"multi_workspace_mode": "false"})
        self.assertIs(config.multi_workspace_mode, True)

    def test_notify_audio_default(self):
        self.assertIs(plugin.Config().notify_audio, True)

    def test_notify_audio_accepts_bool(self):
        config = plugin.Config._from_mapping({"notify_audio": False})
        self.assertIs(config.notify_audio, False)

    def test_notify_audio_rejects_non_bool(self):
        # The string "false" is truthy — must not be coerced into flipping
        # the flag. Non-bool values keep the default.
        config = plugin.Config._from_mapping({"notify_audio": "false"})
        self.assertIs(config.notify_audio, True)

    def test_notify_on_usage_default(self):
        self.assertIs(plugin.Config().notify_on_usage, True)

    def test_notify_on_usage_accepts_bool(self):
        config = plugin.Config._from_mapping({"notify_on_usage": False})
        self.assertIs(config.notify_on_usage, False)

    def test_notify_on_usage_rejects_non_bool(self):
        # Same guard as notify_audio: a truthy string must not flip the flag.
        config = plugin.Config._from_mapping({"notify_on_usage": "false"})
        self.assertIs(config.notify_on_usage, True)

    def test_usage_monitor_default_on(self):
        self.assertEqual(plugin.Config().usage_monitor, "on")

    def test_usage_monitor_accepts_on_off(self):
        self.assertEqual(
            plugin.Config._from_mapping({"usage_monitor": "on"}).usage_monitor,
            "on",
        )

    def test_usage_monitor_rejects_unknown(self):
        # Invalid → keep the default (now "on").
        config = plugin.Config._from_mapping({"usage_monitor": "maybe"})
        self.assertEqual(config.usage_monitor, "on")

    def test_usage_fetch_interval_default(self):
        self.assertEqual(plugin.Config().usage_fetch_interval_sec, 180)

    def test_usage_fetch_interval_minutes_to_seconds(self):
        config = plugin.Config._from_mapping({"usage_fetch_interval_min": 5})
        self.assertEqual(config.usage_fetch_interval_sec, 300)

    def test_usage_fetch_interval_sub_minute_dropped(self):
        # Below the 1-minute floor → keep the default with a warning.
        config = plugin.Config._from_mapping({"usage_fetch_interval_min": 0.2})
        self.assertEqual(config.usage_fetch_interval_sec, 180)

    def test_notify_summary_marker_default(self):
        self.assertEqual(plugin.Config().notify_summary_marker, "-- ")

    def test_notify_summary_marker_override(self):
        config = plugin.Config._from_mapping({"notify_summary_marker": ">> "})
        self.assertEqual(config.notify_summary_marker, ">> ")

    def test_notify_summary_marker_null_disables(self):
        # An explicit JSON null means "feature off" — mirrors the bash
        # _cfg_string_or_null reader. Empty string, not the default.
        config = plugin.Config._from_mapping({"notify_summary_marker": None})
        self.assertEqual(config.notify_summary_marker, "")

    def test_notify_summary_marker_rejects_non_string(self):
        config = plugin.Config._from_mapping({"notify_summary_marker": 42})
        self.assertEqual(config.notify_summary_marker, "-- ")

    def test_editor_focus_settle_default(self):
        self.assertEqual(plugin.Config().editor_focus_settle_sec, 0.1)

    def test_editor_focus_settle_override(self):
        config = plugin.Config._from_mapping({"editor_focus_settle_sec": 0.2})
        self.assertEqual(config.editor_focus_settle_sec, 0.2)

    def test_editor_focus_settle_rejects_out_of_range(self):
        for bad in (-0.1, 5.1, 100):
            config = plugin.Config._from_mapping({"editor_focus_settle_sec": bad})
            self.assertEqual(config.editor_focus_settle_sec, 0.1)

    def test_editor_focus_settle_rejects_garbage(self):
        config = plugin.Config._from_mapping({"editor_focus_settle_sec": "nope"})
        self.assertEqual(config.editor_focus_settle_sec, 0.1)

    def test_editor_url_scheme_default(self):
        self.assertEqual(plugin.Config().editor_url_scheme, "vscode://")

    def test_editor_url_scheme_known_schemes_accepted(self):
        # Every entry in the allow-list must round-trip through the loader.
        # Add a real Code-OSS fork's scheme to ``_EDITOR_URL_SCHEME_ALLOWLIST``
        # before extending this list.
        for scheme in plugin._EDITOR_URL_SCHEME_ALLOWLIST:
            config = plugin.Config._from_mapping({"editor_url_scheme": scheme})
            self.assertEqual(config.editor_url_scheme, scheme)

    def test_editor_url_scheme_rejects_unknown(self):
        # Unknown schemes fall back to the default rather than being passed
        # through to ``open``. A malicious config (e.g. written by another
        # process under the same uid) otherwise turns every row click into
        # a launcher for an arbitrary registered URL handler.
        for hostile in (
            "file:///",
            "evil://",
            "javascript:alert(1)",
            "ssh://attacker.example",
            "vscode",                # missing :// suffix
            "VSCode://",             # case-sensitive
            "",
        ):
            config = plugin.Config._from_mapping({"editor_url_scheme": hostile})
            self.assertEqual(
                config.editor_url_scheme, "vscode://",
                f"hostile scheme {hostile!r} must fall back to default",
            )



class TestUseSessionTitlesConfig(unittest.TestCase):
    """``use_session_titles_for_menubar`` — bool, default off (VSCode-consistent)."""

    def test_default_off(self):
        self.assertFalse(plugin.Config().use_session_titles_for_menubar)

    def test_true_override(self):
        config = plugin.Config._from_mapping(
            {"use_session_titles_for_menubar": True}
        )
        self.assertTrue(config.use_session_titles_for_menubar)

    def test_non_bool_ignored(self):
        # bool("false") == True, so the loader requires a real JSON boolean.
        for bogus in ("true", "false", 1, 0, None):
            config = plugin.Config._from_mapping(
                {"use_session_titles_for_menubar": bogus}
            )
            self.assertFalse(
                config.use_session_titles_for_menubar, msg=repr(bogus)
            )



class TestLocaleCompleteness(unittest.TestCase):
    """Every non-English locale must define the same key set as en.json.

    Guards against shipping a half-translated locale — a missing key would
    silently fall back to English in the UI, which is exactly the runglish
    we don't want.
    """

    def test_all_locales_have_same_keys_as_english(self):
        en = set(k for k in plugin.core.STRINGS["en"] if k != "_meta")
        for code, strings in plugin.core.STRINGS.items():
            keys = set(k for k in strings if k != "_meta")
            self.assertEqual(
                keys, en, msg=f"locale {code!r} key set differs from en"
            )

    def test_new_indicator_keys_present_everywhere(self):
        for key in ("label.blocked", "tooltip.cwd_collision", "tooltip.worktree"):
            for code, strings in plugin.core.STRINGS.items():
                self.assertIn(key, strings, msg=f"{key!r} missing from {code!r}")


if __name__ == "__main__":
    unittest.main()
