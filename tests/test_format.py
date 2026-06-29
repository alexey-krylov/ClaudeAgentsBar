"""Pure formatting helpers (shorten, humanize, context, labels, quoting).

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from _helpers import plugin, _make_session


class TestShorten(unittest.TestCase):
    def test_short_text_passes_through(self):
        self.assertEqual(plugin._shorten("Hello"), "Hello")

    def test_collapses_whitespace(self):
        self.assertEqual(plugin._shorten("foo   bar\n\tbaz"), "foo bar baz")

    def test_truncates_with_ellipsis(self):
        result = plugin._shorten("x" * 200)
        self.assertLessEqual(len(result), plugin.CONFIG.title_max)
        self.assertTrue(result.endswith("…"))



class TestHumanizeAge(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(plugin._humanize_age(0), "0s")
        self.assertEqual(plugin._humanize_age(45), "45s")

    def test_minutes(self):
        self.assertEqual(plugin._humanize_age(60), "1m")
        self.assertEqual(plugin._humanize_age(150), "2m")

    def test_hours_round(self):
        self.assertEqual(plugin._humanize_age(3600), "1h")

    def test_hours_minutes(self):
        self.assertEqual(plugin._humanize_age(3600 + 600), "1h 10m")

    def test_hours_with_seconds_remainder(self):
        # 2h plus 30s — minutes round down to 0, so we render "2h".
        self.assertEqual(plugin._humanize_age(7200 + 30), "2h")



class TestFormatContextLeft(unittest.TestCase):
    def test_empty_context_reads_as_full_window(self):
        # Fresh session: nothing consumed → 100% left.
        self.assertEqual(
            plugin._format_context_left(0, total=200_000),
            "100% — 0k/200k",
        )

    def test_half_full(self):
        self.assertEqual(
            plugin._format_context_left(100_000, total=200_000),
            "50% — 100k/200k",
        )

    def test_typical_live_session(self):
        # Real-world numbers pulled from a transcript on disk: ~27K read from
        # cache + tiny input/creation deltas.
        used = 1 + 257 + 27_036
        self.assertEqual(
            plugin._format_context_left(used, total=200_000),
            "86% — 27k/200k",
        )

    def test_over_budget_clamps_to_zero(self):
        # A transcript can briefly exceed the nominal window between the last
        # turn and the auto-compact; "0%" reads better than a negative number.
        self.assertEqual(
            plugin._format_context_left(250_000, total=200_000),
            "0% — 250k/200k",
        )

    def test_invalid_total_yields_empty(self):
        # Defensive: a misconfigured/zero total shouldn't crash with ZeroDiv.
        self.assertEqual(plugin._format_context_left(1000, total=0), "")



class TestFormatContextWarning(unittest.TestCase):
    """Inline ``⚠ {pct}%`` marker between session title and the age label."""

    def test_below_threshold_yields_empty(self):
        # 50% used vs threshold 80% — no warning, the submenu gauge already
        # covers the green-zone information.
        self.assertEqual(
            plugin._format_context_warning(100_000, total=200_000, threshold=80),
            "",
        )

    def test_at_threshold_renders_yellow(self):
        # Boundary: exactly at the threshold counts as "above" — earliest
        # signal the user can act on.
        out = plugin._format_context_warning(160_000, total=200_000, threshold=80)
        self.assertIn("⚠ 80%", out)
        self.assertIn(plugin._ANSI_WORKING, out)
        self.assertTrue(out.endswith(plugin._ANSI_RESET))

    def test_below_ninety_stays_yellow(self):
        out = plugin._format_context_warning(178_000, total=200_000, threshold=80)
        self.assertIn("⚠ 89%", out)
        self.assertIn(plugin._ANSI_WORKING, out)
        self.assertNotIn(plugin._ANSI_WAITING, out)

    def test_at_or_above_ninety_flips_to_red(self):
        # Crossing 90 % is the "auto-compact soon" line — escalate the
        # ANSI colour from yellow to red so the row visibly screams.
        out = plugin._format_context_warning(180_000, total=200_000, threshold=80)
        self.assertIn("⚠ 90%", out)
        self.assertIn(plugin._ANSI_WAITING, out)

    def test_over_budget_clamps_to_hundred(self):
        # Same clamp semantics as ``_format_context_left`` — a transcript
        # that briefly overshoots its window should not print "104%".
        out = plugin._format_context_warning(250_000, total=200_000, threshold=80)
        self.assertIn("⚠ 100%", out)
        self.assertIn(plugin._ANSI_WAITING, out)

    def test_invalid_total_yields_empty(self):
        self.assertEqual(
            plugin._format_context_warning(1000, total=0, threshold=80),
            "",
        )



class TestLastUsageTokens(unittest.TestCase):
    """Parse the last ``"usage":{…}`` block out of a JSONL transcript tail."""

    def _write(self, body: str) -> Path:
        # tmp file lives in the test's tempdir; cleaned up via addCleanup.
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        path = Path(path)
        path.write_text(body, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_returns_last_match_when_multiple_present(self):
        # Older usage block (low numbers) followed by the freshest one — the
        # parser must surface the most recent because that's the live size.
        body = (
            '{"type":"assistant","message":{"usage":{"input_tokens":1,'
            '"cache_creation_input_tokens":100,"cache_read_input_tokens":1000,'
            '"output_tokens":50}}}\n'
            '{"type":"assistant","message":{"usage":{"input_tokens":2,'
            '"cache_creation_input_tokens":300,"cache_read_input_tokens":27000,'
            '"output_tokens":350,"server_tool_use":{"web_search_requests":0}}}}\n'
        )
        self.assertEqual(plugin.last_usage_tokens(self._write(body)), 27_302)

    def test_returns_none_when_no_usage_blocks(self):
        body = '{"type":"user","message":{"content":"hi"}}\n'
        self.assertIsNone(plugin.last_usage_tokens(self._write(body)))

    def test_returns_none_for_empty_file(self):
        self.assertIsNone(plugin.last_usage_tokens(self._write("")))

    def test_returns_none_for_missing_file(self):
        self.assertIsNone(plugin.last_usage_tokens(Path("/nonexistent/path.jsonl")))



class TestCleanText(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(plugin._clean_text(""), "")

    def test_slash_command_no_args(self):
        self.assertEqual(
            plugin._clean_text("<command-message>cmd</command-message>"),
            "/cmd",
        )

    def test_slash_command_with_args(self):
        self.assertEqual(
            plugin._clean_text(
                "<command-message>example-cmd</command-message>"
                "<command-args>arg1 arg2</command-args>"
            ),
            "/example-cmd arg1 arg2",
        )

    def test_strips_xml_wrappers(self):
        self.assertEqual(
            plugin._clean_text("<system-reminder>hi</system-reminder> there"),
            "hi there",
        )

    def test_plain_text_passes_through(self):
        self.assertEqual(plugin._clean_text("just a question"), "just a question")



class TestContentToTitle(unittest.TestCase):
    def test_string_content(self):
        self.assertEqual(plugin._content_to_title("hi"), "hi")

    def test_list_with_text(self):
        self.assertEqual(
            plugin._content_to_title([{"type": "text", "text": "hi"}]),
            "hi",
        )

    def test_list_with_image(self):
        self.assertEqual(
            plugin._content_to_title([{"type": "image"}]),
            "[image]",
        )

    def test_list_mixed(self):
        self.assertEqual(
            plugin._content_to_title(
                [{"type": "image"}, {"type": "text", "text": "look"}]
            ),
            "[image] look",
        )

    def test_unknown_chunk_types_ignored(self):
        self.assertEqual(plugin._content_to_title([{"type": "weirdo"}]), "")

    def test_non_dict_chunks_skipped(self):
        self.assertEqual(plugin._content_to_title([None, "junk", {"type": "text", "text": "ok"}]), "ok")



class TestProjectName(unittest.TestCase):
    def test_cwd_wins(self):
        self.assertEqual(
            plugin._project_name("/Users/x/Projects/fleet", "anything"),
            "fleet",
        )

    def test_slug_fallback(self):
        self.assertEqual(
            plugin._project_name("", "-Users-alexey-Projects-SB-fleet"),
            "fleet",
        )

    def test_completely_empty(self):
        self.assertEqual(plugin._project_name("", ""), "")



class TestRightLabel(unittest.TestCase):
    def setUp(self):
        # Pin the locale so assertions stay independent of the dev machine's
        # system language — without this the duration carries Russian / Chinese
        # suffixes on workstations whose default locale isn't English.
        self._orig_lang_cache = plugin.core._LANG_CACHE
        plugin.core._LANG_CACHE = "en"

    def tearDown(self):
        plugin.core._LANG_CACHE = self._orig_lang_cache

    def test_working_shows_state_duration(self):
        # Yellow bullet already encodes "working", so the right-hand text is
        # the duration of the current cycle — not "now - last_event_ts",
        # which gets bumped by every PreToolUse.
        s = _make_session(
            hook_state="working", age_sec=3, state_duration_sec=180
        )
        self.assertEqual(s.right_label, "3m")

    def test_waiting_wraps_duration_with_marker(self):
        # Waiting rows carry a localized "waiting {duration}" marker word so
        # the blocked state reads explicitly — not just the bare red duration.
        s = _make_session(
            hook_state="waiting", age_sec=10, state_duration_sec=45
        )
        self.assertEqual(s.right_label, "waiting 45s")
        # The marker must be more than the plain duration the working row uses.
        self.assertIn("45s", s.right_label)
        self.assertNotEqual(s.right_label, "45s")

    def test_idle_keeps_age(self):
        s = _make_session(
            hook_state="idle", age_sec=120, state_duration_sec=0
        )
        self.assertEqual(s.right_label, "2m")



class TestSwiftbarQuote(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(plugin._swiftbar_quote("hello"), '"hello"')

    def test_path_with_spaces(self):
        self.assertEqual(
            plugin._swiftbar_quote("/foo bar/baz"),
            '"/foo bar/baz"',
        )

    def test_neutralises_embedded_double_quotes(self):
        # Embedded double-quotes would close our wrapping prematurely;
        # replace with single quotes so the value still parses safely.
        self.assertEqual(plugin._swiftbar_quote('a "b" c'), '"a \'b\' c"')


if __name__ == "__main__":
    unittest.main()
