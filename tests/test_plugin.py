"""Unit tests for the pure helpers and the config loader in the
:mod:`claude_agents_bar` package.

Run with::

    /usr/bin/python3 -m unittest discover -s tests -v

Stdlib only — no pytest, no third-party deps. The plugin file in the
repo root (``claude-agents.5s.py``) is a thin SwiftBar shim; the real
implementation lives one directory below in the regular ``claude_agents_bar``
package, which we import normally after pinning ``sys.path`` to the repo
root.

Most patches use ``patch.object(plugin.core, ...)`` rather than
``patch.object(plugin, ...)`` — module-level globals (``CONFIG``,
``SIDECAR_PATH``, ``HOME``, ``_lang``) are read via the submodule
namespace (e.g. ``core.CONFIG.title_max``) so the substitution must
land on the *defining* module, not the re-export.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import claude_agents_bar as plugin  # noqa: E402


# --------------------------------------------------------------------------- #
# Pure formatting helpers                                                      #
# --------------------------------------------------------------------------- #


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


class TestUserPromptText(unittest.TestCase):
    """Strip noise out of a ``type:"user"`` event so only real prompts remain."""

    def test_plain_text_chunk_returns_text(self):
        content = [{"type": "text", "text": "Hello"}]
        self.assertEqual(plugin._user_prompt_text(content), "Hello")

    def test_tool_result_is_dropped(self):
        # Claude Code stores assistant tool-results as user-events for
        # transcript continuity; they must not surface as the row title.
        content = [{"type": "tool_result", "content": "File created"}]
        self.assertEqual(plugin._user_prompt_text(content), "")

    def test_system_reminder_wrapper_is_dropped(self):
        # IDE/harness injects these; the user didn't type them.
        for prefix in (
            "<system-reminder>", "<ide_opened_file>", "<command-name>",
            "<command-stdout>", "<local-command-stdout>", "<ide_selection>",
        ):
            content = [{"type": "text", "text": f"{prefix}noise</tag>"}]
            self.assertEqual(
                plugin._user_prompt_text(content), "",
                f"prefix {prefix!r} must be filtered out",
            )

    def test_interrupted_marker_is_dropped(self):
        # Claude Code injects this synthetic line when a tool call is
        # cancelled — not a user prompt.
        content = [{"type": "text", "text": "[Request interrupted by user for tool use]"}]
        self.assertEqual(plugin._user_prompt_text(content), "")

    def test_empty_content_returns_empty(self):
        self.assertEqual(plugin._user_prompt_text(None), "")
        self.assertEqual(plugin._user_prompt_text([]), "")
        self.assertEqual(plugin._user_prompt_text(""), "")

    def test_string_content_unwraps_slash_commands(self):
        # _clean_text turns <command-message> + <command-args> into '/foo bar'.
        content = "<command-message>foo</command-message><command-args>bar</command-args>"
        # Starts with '<' so _user_prompt_text skips it — that's fine, the
        # alternative would mis-classify legitimate user inline XML; slash
        # commands themselves rarely become the aiTitle fallback anyway.
        self.assertEqual(plugin._user_prompt_text(content), "")


class TestLastUserMessagePreview(unittest.TestCase):
    """Tail-scan a JSONL transcript for the freshest real user prompt."""

    def _write(self, body: str) -> Path:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        path = Path(path)
        path.write_text(body, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_picks_latest_user_prompt_not_first(self):
        # The whole point of the fallback: when the conversation has moved
        # on, the latest prompt is more informative than the opening one.
        body = (
            '{"type":"user","message":{"content":[{"type":"text","text":"First question"}]}}\n'
            '{"type":"assistant","message":{"usage":{"input_tokens":1,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"output_tokens":1}}}\n'
            '{"type":"user","message":{"content":[{"type":"text","text":"Follow-up question"}]}}\n'
        )
        self.assertEqual(
            plugin.last_user_message_preview(self._write(body)),
            "Follow-up question",
        )

    def test_skips_tool_results_between_prompts(self):
        body = (
            '{"type":"user","message":{"content":[{"type":"text","text":"Real prompt"}]}}\n'
            '{"type":"user","message":{"content":[{"type":"tool_result","content":"tool output"}]}}\n'
        )
        self.assertEqual(
            plugin.last_user_message_preview(self._write(body)),
            "Real prompt",
        )

    def test_skips_system_reminder_wrappers(self):
        body = (
            '{"type":"user","message":{"content":[{"type":"text","text":"Real prompt"}]}}\n'
            '{"type":"user","message":{"content":[{"type":"text","text":"<system-reminder>do X</system-reminder>"}]}}\n'
        )
        self.assertEqual(
            plugin.last_user_message_preview(self._write(body)),
            "Real prompt",
        )

    def test_returns_empty_when_no_real_prompts(self):
        body = (
            '{"type":"user","message":{"content":[{"type":"tool_result","content":"x"}]}}\n'
            '{"type":"user","message":{"content":[{"type":"text","text":"<system-reminder>noise</system-reminder>"}]}}\n'
        )
        self.assertEqual(plugin.last_user_message_preview(self._write(body)), "")

    def test_returns_empty_for_empty_file(self):
        self.assertEqual(plugin.last_user_message_preview(self._write("")), "")

    def test_returns_empty_for_missing_file(self):
        self.assertEqual(
            plugin.last_user_message_preview(Path("/nonexistent/path.jsonl")),
            "",
        )

    def test_malformed_lines_dont_crash(self):
        # A truncated JSON line in the middle of the tail must not abort
        # the scan — we silently skip and keep going.
        body = (
            '{"type":"user","message":{"content":[{"type":"text","text":"Good"}]}}\n'
            '{"type":"user","message":{"content":[{"type":"text",\n'  # truncated
            '{"type":"user","message":{"content":[{"type":"text","text":"Latest"}]}}\n'
        )
        self.assertEqual(
            plugin.last_user_message_preview(self._write(body)),
            "Latest",
        )


class TestSummariseToolUse(unittest.TestCase):
    """Format one ``tool_use`` chunk for the hover tooltip."""

    def test_bash_uses_command(self):
        self.assertEqual(
            plugin._summarise_tool_use("Bash", {"command": "pytest -q"}),
            "Bash: pytest -q",
        )

    def test_read_uses_file_path(self):
        self.assertEqual(
            plugin._summarise_tool_use("Read", {"file_path": "main.py"}),
            "Read: main.py",
        )

    def test_unknown_tool_falls_back_to_first_string_arg(self):
        # Sensible default for tools we haven't explicitly mapped — still
        # better than just rendering the bare tool name.
        self.assertEqual(
            plugin._summarise_tool_use("MyTool", {"thing": "the-thing"}),
            "MyTool: the-thing",
        )

    def test_no_useful_input_falls_back_to_name(self):
        # TodoWrite-style tools whose input is a list of objects (no string
        # values at the top level) — render just the bare tool name rather
        # than dumping JSON in the tooltip.
        self.assertEqual(
            plugin._summarise_tool_use("TodoWrite", {"todos": [{"x": 1}]}),
            "TodoWrite",
        )

    def test_multiline_input_collapsed_to_single_line(self):
        # NSMenuItem.toolTip respects newlines but a multi-line tooltip
        # crowds the menu. Collapse whitespace runs.
        out = plugin._summarise_tool_use(
            "Bash", {"command": "echo foo\n   echo bar\n\techo baz"},
        )
        self.assertEqual(out, "Bash: echo foo echo bar echo baz")

    def test_long_preview_not_truncated_in_tooltip(self):
        # Tooltips have plenty of room and the full command/path is the
        # whole point of surfacing it — no truncation, unlike a menu row.
        long = "x" * 200
        out = plugin._summarise_tool_use("Read", {"file_path": long})
        self.assertEqual(out, "Read: " + long)

    def test_empty_name_returns_empty(self):
        self.assertEqual(plugin._summarise_tool_use("", {"command": "x"}), "")

    def test_non_dict_input_returns_just_name(self):
        self.assertEqual(plugin._summarise_tool_use("Bash", None), "Bash")
        self.assertEqual(plugin._summarise_tool_use("Bash", "raw"), "Bash")


class TestLastToolUseSummary(unittest.TestCase):
    """Tail-scan a JSONL transcript for the freshest ``tool_use`` chunk."""

    def _write(self, body: str) -> Path:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        path = Path(path)
        path.write_text(body, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_returns_latest_tool_use(self):
        body = (
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"old.py"}}]}}\n'
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"pytest"}}]}}\n'
        )
        self.assertEqual(
            plugin.last_tool_use_summary(self._write(body)),
            "Bash: pytest",
        )

    def test_returns_empty_when_no_tool_use(self):
        body = (
            '{"type":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n'
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n'
        )
        self.assertEqual(plugin.last_tool_use_summary(self._write(body)), "")

    def test_returns_empty_for_empty_file(self):
        self.assertEqual(plugin.last_tool_use_summary(self._write("")), "")

    def test_returns_empty_for_missing_file(self):
        self.assertEqual(
            plugin.last_tool_use_summary(Path("/nonexistent/path.jsonl")),
            "",
        )

    def test_malformed_lines_dont_crash(self):
        body = (
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"a.py"}}]}}\n'
            'not-json{\n'
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"b.py"}}]}}\n'
        )
        self.assertEqual(
            plugin.last_tool_use_summary(self._write(body)),
            "Edit: b.py",
        )


class TestDisplayTitleFallback(unittest.TestCase):
    """``TranscriptMeta.display_title`` picks the right source by priority."""

    def test_ai_title_wins_when_present(self):
        meta = plugin.TranscriptMeta(
            ai_title="Generated summary",
            raw_title="First user message",
            last_user_message="Latest user prompt",
        )
        self.assertEqual(meta.display_title, "Generated summary")

    def test_last_user_message_is_preferred_over_raw_when_ai_missing(self):
        # The whole point of the new feature: when aiTitle hasn't been
        # generated yet, show what the user *just* asked, not their opening.
        meta = plugin.TranscriptMeta(
            ai_title="",
            raw_title="First user message",
            last_user_message="Follow-up about feature X",
        )
        self.assertEqual(meta.display_title, "Follow-up about feature X")

    def test_raw_title_used_when_no_ai_and_no_last(self):
        # Tail might not yield a parseable last_user_message (e.g. a single
        # giant pasted code block that pushed the prompt outside the tail
        # window). Fall back to first user message rather than rendering
        # the row as untitled.
        meta = plugin.TranscriptMeta(
            ai_title="",
            raw_title="First user message",
            last_user_message="",
        )
        self.assertEqual(meta.display_title, "First user message")


# --------------------------------------------------------------------------- #
# State classification                                                         #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Title extraction                                                             #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Predicates                                                                   #
# --------------------------------------------------------------------------- #


def _make_session(**overrides):
    """Build a ``Session`` with sensible defaults; tests override the field they care about."""
    defaults = dict(
        id="sid",
        hook_state="idle",
        group=plugin.RenderGroup.STALE,
        last_event_ts=0,
        age_sec=0,
        title="title",
        project="project",
        git_branch="",
        cwd="",
        entrypoint="",
    )
    defaults.update(overrides)
    return plugin.Session(**defaults)


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

    def test_waiting_shows_state_duration(self):
        s = _make_session(
            hook_state="waiting", age_sec=10, state_duration_sec=45
        )
        self.assertEqual(s.right_label, "45s")

    def test_idle_keeps_age(self):
        s = _make_session(
            hook_state="idle", age_sec=120, state_duration_sec=0
        )
        self.assertEqual(s.right_label, "2m")


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


# --------------------------------------------------------------------------- #
# Sidecar parsing                                                              #
# --------------------------------------------------------------------------- #


class TestParseSidecar(unittest.TestCase):
    def test_valid_row(self):
        # 6-column row: state_since is parsed independently of last_event_ts,
        # so the plugin can render "has been working for N seconds" even after
        # multiple PreToolUse events have bumped last_event_ts.
        raw = "sid1\tworking\t1700000050\tPreToolUse\t/tmp\t1700000000\n"
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid1"})
        snap = result["sid1"]
        self.assertEqual(snap.state, "working")
        self.assertEqual(snap.last_event_ts, 1700000050)
        self.assertEqual(snap.last_event_kind, "PreToolUse")
        self.assertEqual(snap.cwd, "/tmp")
        self.assertEqual(snap.state_since, 1700000000)

    def test_legacy_five_column_row_defaults_state_since_to_ts(self):
        # Rows written by the pre-state_since hook version must still parse;
        # treating state_since == last_event_ts means duration starts counting
        # afresh from the next hook event, which is the safest fallback.
        raw = "sid1\tworking\t1700000000\tPreToolUse\t/tmp\n"
        snap = plugin._parse_sidecar(raw)["sid1"]
        self.assertEqual(snap.state_since, 1700000000)

    def test_garbage_state_since_falls_back_to_ts(self):
        raw = "sid1\tworking\t1700000000\tPreToolUse\t/tmp\tnope\n"
        snap = plugin._parse_sidecar(raw)["sid1"]
        self.assertEqual(snap.state_since, 1700000000)

    def test_invalid_state_skipped(self):
        raw = "sid1\tbogus\t1700000000\tPreToolUse\t/tmp\n"
        self.assertEqual(plugin._parse_sidecar(raw), {})

    def test_non_int_timestamp_skipped(self):
        raw = "sid1\tworking\tnot-a-number\tPreToolUse\t/tmp\n"
        self.assertEqual(plugin._parse_sidecar(raw), {})

    def test_too_few_columns_skipped(self):
        raw = "sid1\tworking\t1700000000\n"
        self.assertEqual(plugin._parse_sidecar(raw), {})

    def test_multiple_rows_independent(self):
        raw = (
            "sid1\tworking\t1700000000\tPreToolUse\t/tmp\n"
            "sid2\tidle\t1700001000\tStop\t/var\n"
        )
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid1", "sid2"})
        self.assertEqual(result["sid2"].state, "idle")

    def test_last_write_wins(self):
        raw = (
            "sid1\tworking\t1700000000\tPreToolUse\t/a\n"
            "sid1\tidle\t1700000100\tStop\t/a\n"
        )
        result = plugin._parse_sidecar(raw)
        self.assertEqual(result["sid1"].state, "idle")
        self.assertEqual(result["sid1"].last_event_ts, 1700000100)


# --------------------------------------------------------------------------- #
# Clicks sidecar parsing                                                       #
# --------------------------------------------------------------------------- #


class TestParseClicks(unittest.TestCase):
    def test_valid_row(self):
        raw = "sid1\t1700000123\n"
        self.assertEqual(plugin._parse_clicks(raw), {"sid1": 1700000123})

    def test_non_int_timestamp_skipped(self):
        # Garbage in one row must not poison the others.
        raw = "sid1\tbroken\nsid2\t1700000200\n"
        self.assertEqual(plugin._parse_clicks(raw), {"sid2": 1700000200})

    def test_too_few_columns_skipped(self):
        self.assertEqual(plugin._parse_clicks("sid1\n"), {})

    def test_last_write_wins(self):
        # The recorder rewrites the row in place, but if a duplicate ever
        # leaked through the lock we'd still want the latest entry.
        raw = "sid1\t1700000000\nsid1\t1700000050\n"
        self.assertEqual(plugin._parse_clicks(raw), {"sid1": 1700000050})


# --------------------------------------------------------------------------- #
# ack_fresh selection rules                                                    #
# --------------------------------------------------------------------------- #


class TestAckFresh(unittest.TestCase):
    """*Tools → Acknowledge all* must only touch sessions currently in 🟢.

    ``ack_fresh`` reuses :func:`collect_sessions` for its source of truth,
    so the tests have to set up a tiny fake of the on-disk layout: one
    JSONL per session under a mocked ``PROJECTS_DIR``, plus a sidecar
    TSV row for non-idle sessions. JSONL mtimes are stamped explicitly
    so age computations are deterministic.
    """

    def setUp(self):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        sidecar = self._tmpdir / "state.tsv"
        clicks = self._tmpdir / "clicks.tsv"
        dismiss = self._tmpdir / "dismiss"
        self._orig_projects = plugin.PROJECTS_DIR
        self._orig_sidecar = plugin.SIDECAR_PATH
        self._orig_clicks = plugin.CLICKS_PATH
        self._orig_dismiss = plugin.DISMISS_PATH
        self._orig_sidecar_lock = plugin._SIDECAR_LOCK_DIR
        self._orig_clicks_lock = plugin._CLICKS_LOCK_DIR
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = sidecar
        plugin.core.CLICKS_PATH = clicks
        # Redirect DISMISS_PATH too — without this the user's real cutoff
        # file (set by *Forget all sessions*) leaks into the test and
        # filters out every fake session whose synthetic ``now`` predates
        # the real cutoff.
        plugin.core.DISMISS_PATH = dismiss
        plugin.core._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        plugin.core._CLICKS_LOCK_DIR = clicks.with_suffix(clicks.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.clicks = clicks
        self.now = 1_700_000_000
        self.fresh = plugin.CONFIG.fresh_sec

    def tearDown(self):
        import shutil
        plugin.core.PROJECTS_DIR = self._orig_projects
        plugin.core.SIDECAR_PATH = self._orig_sidecar
        plugin.core.CLICKS_PATH = self._orig_clicks
        plugin.core.DISMISS_PATH = self._orig_dismiss
        plugin.core._SIDECAR_LOCK_DIR = self._orig_sidecar_lock
        plugin.core._CLICKS_LOCK_DIR = self._orig_clicks_lock
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_session(self, sid, mtime, sidecar_row=None):
        """Create a JSONL on disk with the given mtime, plus optional sidecar row.

        ``sidecar_row`` items: ``(state, ts)``. When ``None`` the session
        has no sidecar entry and falls back to JSONL mtime — same path as
        a freshly-finished session whose Stop hook hasn't been
        registered yet.
        """
        project_dir = self.projects / f"-fake-{sid}"
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        jsonl.write_bytes(b"")
        os.utime(jsonl, (mtime, mtime))
        if sidecar_row is not None:
            state, ts = sidecar_row
            existing = self.sidecar.read_text() if self.sidecar.exists() else ""
            self.sidecar.write_text(
                existing + f"{sid}\t{state}\t{ts}\tStop\t/tmp\n",
                encoding="utf-8",
            )

    def _read_clicks(self):
        if not self.clicks.exists():
            return {}
        return plugin._parse_clicks(self.clicks.read_text(encoding="utf-8"))

    def test_promotes_fresh_session(self):
        self._make_session("fresh", self.now - 60, ("idle", self.now - 60))
        self.assertEqual(plugin.ack_fresh(self.now), 1)
        self.assertEqual(self._read_clicks(), {"fresh": self.now})

    def test_session_without_sidecar_row_is_invisible(self):
        # Stronger guarantee than just "not FRESH": after dropping the
        # JSONL-mtime fallback in collect_sessions, a session without a
        # TSV row doesn't appear in the menu at all. Otherwise an IDE
        # tab switch (which updates JSONL mtime as Claude Code appends
        # the SessionStart event) would put the session into the menu
        # as ACK/STALE — exactly the "I just clicked a tab and 9 blue
        # sessions appeared" report this branch fixes.
        self._make_session("untracked", self.now - 60)
        self.assertEqual(plugin.collect_sessions(self.now), [])
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {})

    def test_skips_working_session(self):
        self._make_session("alive", self.now - 5, ("working", self.now - 5))
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {})

    def test_skips_stale_session(self):
        old = self.now - self.fresh - 1
        self._make_session("old", old, ("idle", old))
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {})

    def test_skips_already_clicked_session(self):
        stop_ts = self.now - 60
        self._make_session("seen", stop_ts, ("idle", stop_ts))
        self.clicks.write_text(f"seen\t{stop_ts + 1}\n", encoding="utf-8")
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {"seen": stop_ts + 1})

    def test_mixed_set_targets_only_fresh(self):
        self._make_session("fresh1", self.now - 30, ("idle", self.now - 30))
        self._make_session("fresh2", self.now - 90, ("idle", self.now - 90))
        self._make_session(
            "stale",
            self.now - self.fresh - 100,
            ("idle", self.now - self.fresh - 100),
        )
        self._make_session("active", self.now - 5, ("working", self.now - 5))
        self.assertEqual(plugin.ack_fresh(self.now), 2)
        self.assertEqual(
            self._read_clicks(),
            {"fresh1": self.now, "fresh2": self.now},
        )


# --------------------------------------------------------------------------- #
# Dismiss cutoff                                                               #
# --------------------------------------------------------------------------- #


class TestReadDismissTs(unittest.TestCase):
    """``read_dismiss_ts`` should fail-open: any read error returns 0."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".dismiss"
        )
        self._tmp.close()
        self._original = plugin.DISMISS_PATH
        plugin.core.DISMISS_PATH = Path(self._tmp.name)

    def tearDown(self):
        plugin.core.DISMISS_PATH = self._original
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_missing_file_returns_zero(self):
        Path(self._tmp.name).unlink()
        self.assertEqual(plugin.read_dismiss_ts(), 0)

    def test_valid_timestamp(self):
        Path(self._tmp.name).write_text("1700000123\n", encoding="utf-8")
        self.assertEqual(plugin.read_dismiss_ts(), 1700000123)

    def test_garbage_returns_zero(self):
        # A corrupt cutoff file must not silently hide every live session.
        Path(self._tmp.name).write_text("not-a-number", encoding="utf-8")
        self.assertEqual(plugin.read_dismiss_ts(), 0)


# --------------------------------------------------------------------------- #
# Per-row Forget sidecar                                                       #
# --------------------------------------------------------------------------- #


class TestForgetSidecar(unittest.TestCase):
    """Per-row *Forget* hides a session until its ``last_event_ts`` is past
    the recorded ``forget_ts`` — same cutoff semantics as the global dismiss,
    just keyed by session id. A fresh event re-surfaces the row.
    """

    def setUp(self):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        sidecar = self._tmpdir / "state.tsv"
        clicks = self._tmpdir / "clicks.tsv"
        forget = self._tmpdir / "forget.tsv"
        dismiss = self._tmpdir / "dismiss"
        self._orig_projects = plugin.PROJECTS_DIR
        self._orig_sidecar = plugin.SIDECAR_PATH
        self._orig_clicks = plugin.CLICKS_PATH
        self._orig_forget = plugin.FORGET_PATH
        self._orig_dismiss = plugin.DISMISS_PATH
        self._orig_sidecar_lock = plugin._SIDECAR_LOCK_DIR
        self._orig_clicks_lock = plugin._CLICKS_LOCK_DIR
        self._orig_forget_lock = plugin._FORGET_LOCK_DIR
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = sidecar
        plugin.core.CLICKS_PATH = clicks
        plugin.core.FORGET_PATH = forget
        plugin.core.DISMISS_PATH = dismiss
        plugin.core._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        plugin.core._CLICKS_LOCK_DIR = clicks.with_suffix(clicks.suffix + ".lock.d")
        plugin.core._FORGET_LOCK_DIR = forget.with_suffix(forget.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.forget = forget
        self.now = 1_700_000_000

    def tearDown(self):
        import shutil
        plugin.core.PROJECTS_DIR = self._orig_projects
        plugin.core.SIDECAR_PATH = self._orig_sidecar
        plugin.core.CLICKS_PATH = self._orig_clicks
        plugin.core.FORGET_PATH = self._orig_forget
        plugin.core.DISMISS_PATH = self._orig_dismiss
        plugin.core._SIDECAR_LOCK_DIR = self._orig_sidecar_lock
        plugin.core._CLICKS_LOCK_DIR = self._orig_clicks_lock
        plugin.core._FORGET_LOCK_DIR = self._orig_forget_lock
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_session(self, sid, mtime, sidecar_row=None):
        project_dir = self.projects / f"-fake-{sid}"
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        jsonl.write_bytes(b"")
        os.utime(jsonl, (mtime, mtime))
        if sidecar_row is not None:
            state, ts = sidecar_row
            existing = self.sidecar.read_text() if self.sidecar.exists() else ""
            self.sidecar.write_text(
                existing + f"{sid}\t{state}\t{ts}\tStop\t/tmp\n",
                encoding="utf-8",
            )

    def _ids(self, sessions):
        return sorted(s.id for s in sessions)

    def test_missing_file_returns_empty(self):
        # No forget sidecar yet — read_forget must not error.
        self.assertEqual(plugin.read_forget(), {})

    def test_forget_hides_idle_session(self):
        # forget_ts >= last_event_ts ⇒ row is hidden.
        self._make_session("ghost", self.now - 60, ("idle", self.now - 60))
        self.forget.write_text(f"ghost\t{self.now - 30}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), [])

    def test_fresh_event_resurfaces_forgotten_session(self):
        # A new event past forget_ts must bring the row back — that's the
        # built-in escape hatch when the user changes their mind.
        self._make_session("ghost", self.now - 10, ("idle", self.now - 10))
        self.forget.write_text(f"ghost\t{self.now - 60}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), ["ghost"])

    def test_forget_only_affects_targeted_session(self):
        # The forget map is per-session; siblings keep showing.
        self._make_session("ghost", self.now - 60, ("idle", self.now - 60))
        self._make_session("live", self.now - 30, ("idle", self.now - 30))
        self.forget.write_text(f"ghost\t{self.now - 30}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), ["live"])

    def test_orphan_forget_rows_are_gc_d(self):
        # forget rows whose JSONL no longer exists must be dropped on the
        # next collect_sessions pass — otherwise the sidecar would grow
        # forever as sessions get deleted out of band.
        self.forget.write_text(f"gone\t{self.now}\n", encoding="utf-8")
        plugin.collect_sessions(self.now)
        self.assertEqual(plugin.read_forget(), {})

    def test_unparseable_rows_are_skipped(self):
        # Same fail-open stance as read_clicks: a single corrupt row must
        # not hide the rest of the forget set.
        self._make_session("ghost", self.now - 60, ("idle", self.now - 60))
        self.forget.write_text(
            "garbage-line-no-tab\n"
            f"ghost\tnot-an-int\n"
            f"ghost\t{self.now - 30}\n",
            encoding="utf-8",
        )
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), [])


# --------------------------------------------------------------------------- #
# Config loader                                                                #
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Menubar icon resolution                                                      #
# --------------------------------------------------------------------------- #


class TestMenubarIconPieces(unittest.TestCase):
    """``_menubar_icon_pieces`` translates the ``menubar_icon`` config knob
    into the right combination of ``(swiftbar_params, inline_glyph)``.

    The tests pin each prefix in isolation and assert silent fallback on
    missing files — important because the default path points at
    Claude.app, which may not be installed on every machine.
    """

    def _set_icon(self, value):
        # ``Config`` is frozen; rebuild the singleton in place.
        from dataclasses import replace
        self._original_config = plugin.CONFIG
        plugin.core.CONFIG = replace(plugin.CONFIG, menubar_icon=value)
        self.addCleanup(self._restore)

    def _restore(self):
        plugin.core.CONFIG = self._original_config

    def test_plain_glyph_passes_through(self):
        self._set_icon("✨")
        params, glyph = plugin._menubar_icon_pieces()
        self.assertEqual(params, "")
        self.assertEqual(glyph, "✨")

    def test_sf_prefix_emits_sfimage(self):
        self._set_icon("sf:bubble.left.fill")
        params, glyph = plugin._menubar_icon_pieces()
        self.assertEqual(params, " | sfimage=bubble.left.fill")
        self.assertEqual(glyph, "")

    def _stub_resize_identity(self):
        """Patch the sips/tiffutil-based resize to a no-op for the rest of the test.

        Without this, the resize subprocess runs against our fake PNG
        bytes and fails noisily on stderr — distracting from what the
        test is actually exercising.
        """
        original = plugin._resized_menubar_image
        plugin.render._resized_menubar_image = lambda src: src
        self.addCleanup(lambda: setattr(plugin, "_resized_menubar_image", original))

    def test_template_prefix_emits_base64_template_image(self):
        import tempfile
        self._stub_resize_identity()
        png_bytes = b"\x89PNG\r\n\x1a\nfake-test-bytes"
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(png_bytes)
            tmp_path = tmp.name
        try:
            self._set_icon(f"template:{tmp_path}")
            params, glyph = plugin._menubar_icon_pieces()
            expected = base64.b64encode(png_bytes).decode("ascii")
            self.assertEqual(params, f" | templateImage={expected}")
            self.assertEqual(glyph, "")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_image_prefix_emits_image_param(self):
        import tempfile
        self._stub_resize_identity()
        png_bytes = b"\x89PNG\r\n\x1a\nfake-colour-bytes"
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(png_bytes)
            tmp_path = tmp.name
        try:
            self._set_icon(f"image:{tmp_path}")
            params, glyph = plugin._menubar_icon_pieces()
            expected = base64.b64encode(png_bytes).decode("ascii")
            self.assertEqual(params, f" | image={expected}")
            self.assertEqual(glyph, "")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_template_missing_file_falls_back_to_glyph(self):
        # Critical for the default config: Claude.app may be absent, and
        # the plugin must still render. Falls back to the configured
        # ``menubar_icon_fallback`` glyph instead of an empty icon.
        from dataclasses import replace
        original = plugin.CONFIG
        plugin.core.CONFIG = replace(
            original,
            menubar_icon="template:/no/such/file/at/all.png",
            menubar_icon_fallback="🤖",
        )
        try:
            params, glyph = plugin._menubar_icon_pieces()
            self.assertEqual(params, "")
            self.assertEqual(glyph, "🤖")
        finally:
            plugin.core.CONFIG = original


# --------------------------------------------------------------------------- #
# SwiftBar quoting                                                             #
# --------------------------------------------------------------------------- #


class TestIsValidSessionId(unittest.TestCase):
    """Allow-list for session ids. The regex is a security boundary: every
    downstream consumer (shell args, AppleScript dialogs, TSV field
    lookups, SwiftBar ``paramN=`` tokens) assumes session ids contain no
    metacharacters. New attack shapes belong here, not as defensive code
    scattered across callers."""

    def test_accepts_uuid_v4(self):
        self.assertTrue(plugin._is_valid_session_id(
            "abcd1234-ab12-4cd3-9ef0-abcdef012345"
        ))

    def test_accepts_short_test_fixture(self):
        # The unit tests across this file use short SIDs like ``sid1``,
        # ``fresh``, ``alive``. The regex must keep accepting them — they
        # contain no metacharacters and the validator is about *shape*,
        # not literal UUID-ness.
        for sid in ("sid1", "fresh", "alive", "untracked", "AB_cd-12"):
            self.assertTrue(
                plugin._is_valid_session_id(sid),
                f"safe fixture {sid!r} must pass",
            )

    def test_rejects_newline_injection(self):
        # The original vector: a JSONL file named with a literal newline
        # would let an attacker who controls ``~/.claude/projects/`` add
        # a second SwiftBar menu row with arbitrary ``shell=`` / ``param``
        # tokens. Reject before the value reaches the renderer.
        self.assertFalse(plugin._is_valid_session_id(
            "abc\nshell=/bin/sh param1=-c"
        ))

    def test_rejects_tab_injection(self):
        # A SID with an embedded tab would shift the TSV columns and let
        # attacker-controlled bytes land in the ``cwd`` / ``state_since``
        # fields. Reject at parse time.
        self.assertFalse(plugin._is_valid_session_id("abc\tworking"))

    def test_rejects_applescript_quote_injection(self):
        # The pre-patch ``delete-session.sh`` spliced ``$SID`` into an
        # ``osascript -e "... ${SID} ..."`` template. A quote here used
        # to break out of the AppleScript string literal.
        self.assertFalse(plugin._is_valid_session_id(
            'abc"; do shell script "rm -rf ~"; --'
        ))

    def test_rejects_shell_metacharacters(self):
        for hostile in (
            "abc;rm -rf /",
            "abc&touch /tmp/pwn",
            "abc|nc evil 9999",
            "abc$(id)",
            "abc`whoami`",
            "abc>/etc/passwd",
            "abc<file",
            "abc*",
            "abc?",
            "abc /",
        ):
            self.assertFalse(
                plugin._is_valid_session_id(hostile),
                f"hostile SID {hostile!r} must be rejected",
            )

    def test_rejects_regex_metacharacters(self):
        # The pre-patch ``grep -v "^${SID}\t"`` interpreted SID as a regex.
        # ``.*`` would have matched every line and wiped the sidecar.
        for hostile in (".*", "^.*$", "abc.def", "abc[ab]c", "abc(d)e"):
            self.assertFalse(
                plugin._is_valid_session_id(hostile),
                f"hostile regex SID {hostile!r} must be rejected",
            )

    def test_rejects_empty(self):
        self.assertFalse(plugin._is_valid_session_id(""))

    def test_rejects_overlong(self):
        # 64-char hard cap keeps log/menu/TSV lines bounded and prevents
        # any attacker-controlled allocation pressure.
        self.assertTrue(plugin._is_valid_session_id("a" * 64))
        self.assertFalse(plugin._is_valid_session_id("a" * 65))


class TestParseSidecarSecurity(unittest.TestCase):
    """``_parse_sidecar`` must drop rows whose SID would weaponise a later
    consumer (shell arg, AppleScript dialog, TSV column shift)."""

    def test_drops_row_with_unsafe_sid(self):
        # A real attacker can't usually get arbitrary bytes into the SID
        # column — the hook writes whatever Claude Code gave it — but a
        # corrupted TSV (half-written write, leftover from a previous
        # schema, hostile process writing to the file) must not blow up
        # the renderer either.
        raw = (
            'evil";do shell script "x";--\tworking\t1700000000\tPreToolUse\t/tmp\n'
            "sid_ok\tworking\t1700000050\tPreToolUse\t/tmp\n"
        )
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid_ok"})

    def test_drops_row_with_regex_metacharacter_sid(self):
        raw = (
            ".*\tworking\t1700000000\tPreToolUse\t/tmp\n"
            "sid_ok\tworking\t1700000050\tPreToolUse\t/tmp\n"
        )
        self.assertEqual(set(plugin._parse_sidecar(raw)), {"sid_ok"})


class TestLiveSessionIdsSecurity(unittest.TestCase):
    """``_live_session_ids`` reads filenames under ``PROJECTS_DIR``. Any
    process running under the same uid can write there, so the listing
    itself is untrusted input. The validator is what makes the rest of
    the renderer safe to use the result without quoting."""

    def setUp(self):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        self._original = plugin.PROJECTS_DIR
        plugin.core.PROJECTS_DIR = self._tmpdir

    def tearDown(self):
        import shutil
        plugin.core.PROJECTS_DIR = self._original
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_skips_unsafe_filenames(self):
        project = self._tmpdir / "-fake-proj"
        project.mkdir()
        # Filenames an attacker with write access to ~/.claude/projects/
        # could plausibly create: shell-quote injection and a regex-anchor
        # SID. The validator rejects both; the safe one survives.
        (project / 'evil";do shell script "x";--.jsonl').write_bytes(b"")
        (project / ".*.jsonl").write_bytes(b"")
        (project / "abcd1234-ab12-4cd3-9ef0-abcdef012345.jsonl").write_bytes(b"")
        live = plugin._live_session_ids()
        self.assertEqual(live, {"abcd1234-ab12-4cd3-9ef0-abcdef012345"})


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


class TestStatsHelpers(unittest.TestCase):
    """Pure helpers for the ``Tools → Stats today`` summary."""

    def test_format_token_count_under_thousand(self):
        self.assertEqual(plugin._format_token_count(0), "0")
        self.assertEqual(plugin._format_token_count(999), "999")

    def test_format_token_count_thousands(self):
        self.assertEqual(plugin._format_token_count(1_500), "1.5K")
        self.assertEqual(plugin._format_token_count(120_000), "120.0K")

    def test_format_token_count_millions(self):
        self.assertEqual(plugin._format_token_count(1_800_000), "1.8M")

    def test_local_midnight_is_today_zero_hour(self):
        now = int(time.time())
        midnight = plugin._local_midnight_ts(now)
        # The struct decomposed from the result must read 00:00:00 in
        # local time and share the calendar date with ``now``.
        m = time.localtime(midnight)
        n = time.localtime(now)
        self.assertEqual((m.tm_hour, m.tm_min, m.tm_sec), (0, 0, 0))
        self.assertEqual((m.tm_year, m.tm_mon, m.tm_mday),
                         (n.tm_year, n.tm_mon, n.tm_mday))
        self.assertLessEqual(midnight, now)

    def test_format_stats_dialog_empty_state(self):
        # Brand new install: zero sessions, no tokens, no top projects.
        # Force English so assertions don't depend on the host locale.
        with patch.object(plugin.core, "_lang", return_value="en"):
            body = plugin._format_stats_dialog({
                "sessions": 0, "turns": 0,
                "total_tokens": 0, "prompt_tokens": 0, "cache_read_tokens": 0,
                "top_projects": [],
            })
        # Tokens line should show the "no usage data yet" variant — not
        # a confusing "0 (prompt 0, cache-hit 0%)" sentence.
        self.assertIn("Tokens", body)
        self.assertNotIn("Top projects", body)

    def test_format_stats_dialog_with_projects(self):
        with patch.object(plugin.core, "_lang", return_value="en"):
            body = plugin._format_stats_dialog({
                "sessions": 5, "turns": 100,
                "total_tokens": 1_000_000, "prompt_tokens": 200_000,
                "cache_read_tokens": 500_000,
                "top_projects": [("FooRepo", 60), ("BarRepo", 40)],
            })
        self.assertIn("Sessions today: 5", body)
        self.assertIn("Turns: 100", body)
        self.assertIn("1.0M", body)
        # 500K / 1M → 50 % cache-hit
        self.assertIn("50%", body)
        self.assertIn("FooRepo", body)
        self.assertIn("BarRepo", body)


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


# --------------------------------------------------------------------------- #
# hooks/agent-state.sh — shell-level behavior                                  #
# --------------------------------------------------------------------------- #


class TestAgentStateHook(unittest.TestCase):
    """End-to-end checks for the shell hook that writes ``agent-state.tsv``.

    The hook is a Bash script, so we run it under a temporary ``$HOME``
    via subprocess and inspect the resulting TSV. The hook is now a
    plain ``{working,waiting,idle}`` switch — ``SessionStart`` is not
    registered upstream, so we don't need a ``session-start`` branch
    here either. The "unknown argument is a silent no-op" path is what
    keeps stale registrations from a previous version safe across an
    in-place upgrade.
    """

    HOOK = Path(__file__).resolve().parent.parent / "hooks" / "agent-state.sh"

    def setUp(self):
        # Fresh $HOME per test — the hook writes ~/.claude/agent-state.tsv
        # and we want each test to start from an empty index.
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / ".claude").mkdir()
        self.tsv = self.home / ".claude" / "agent-state.tsv"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, arg: str, payload: dict, check: bool = True) -> int:
        import subprocess
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        proc = subprocess.run(
            ["/bin/bash", str(self.HOOK), arg],
            input=json.dumps(payload).encode("utf-8"),
            env=env, check=check, timeout=10,
        )
        return proc.returncode

    def _row(self, sid: str) -> list[str] | None:
        if not self.tsv.exists():
            return None
        for line in self.tsv.read_text(encoding="utf-8").splitlines():
            cols = line.split("\t")
            if cols and cols[0] == sid:
                return cols
        return None

    def test_working_writes_row(self):
        # Baseline: a normal PreToolUse → working still works end-to-end.
        self._run("working", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "PreToolUse",
        })
        row = self._row("sid-1")
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "working")
        self.assertEqual(row[3], "PreToolUse")

    def test_idle_writes_row_with_stop_kind(self):
        # Stop → idle must carry kind=Stop forward so the plugin's
        # FRESH guard (last_event_kind == "Stop") can fire green.
        self._run("idle", {
            "session_id": "sid-2", "cwd": "/x",
            "hook_event_name": "Stop",
        })
        row = self._row("sid-2")
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "idle")
        self.assertEqual(row[3], "Stop")

    def test_unknown_argument_is_silent_noop(self):
        # Stale SessionStart registration on disk (left over from a
        # previous version) may still call the hook with
        # "session-start". The new hook should refuse the argument
        # without touching the TSV — not crash, not write garbage.
        self.assertEqual(
            self._run("session-start", {
                "session_id": "sid-3", "cwd": "/x",
                "hook_event_name": "SessionStart", "source": "resume",
            }, check=False),
            0,
        )
        self.assertIsNone(self._row("sid-3"))


# --------------------------------------------------------------------------- #
# bin/setup.sh — settings.json merge idempotency                               #
# --------------------------------------------------------------------------- #


class TestSetupMerge(unittest.TestCase):
    """``setup.sh`` must be able to *update* its own hook registrations.

    The original merge was purely additive (``jq +``), so re-running
    setup after the bundled command line changed appended a duplicate
    matcher alongside the stale one — both fired on every event. These
    tests pin the "purge-then-append" behavior: old ``agent-state.sh``
    matchers (including ones for events we no longer register, like
    SessionStart) are removed before our patch is appended, while
    unrelated user hooks are preserved untouched.

    The jq program is duplicated here from ``bin/setup.sh``. If you
    change the merge logic in one place, update the other.
    """

    # Mirrors the jq pipeline in bin/setup.sh, step 5. Keep in sync.
    MERGE_JQ = r"""
        def is_ours: (.command // "") | contains("agent-state.sh");
        .hooks = (.hooks // {})
        | .hooks |= with_entries(
            .value |= (
                map(.hooks |= map(select(is_ours | not)))
                | map(select(((.hooks // []) | length) > 0))
            )
        )
        | reduce ($patch.hooks | to_entries[]) as $kv (
            .;
            .hooks[$kv.key] = ((.hooks[$kv.key] // []) + $kv.value)
        )
    """

    PATCH_PATH = (
        Path(__file__).resolve().parent.parent / "hooks" / "settings-hooks.json"
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _merge(self, settings: dict) -> dict:
        """Run the merge jq program against ``settings`` and return the result."""
        import subprocess
        path = self.tmpdir / "settings.json"
        path.write_text(json.dumps(settings), encoding="utf-8")
        # Expand ${HOME} the way bin/setup.sh does before feeding the patch.
        patch_raw = self.PATCH_PATH.read_text(encoding="utf-8")
        patch_expanded = patch_raw.replace("${HOME}", os.environ["HOME"])
        result = subprocess.run(
            ["/usr/bin/jq", "--argjson", "patch", patch_expanded, self.MERGE_JQ,
             str(path)],
            check=True, capture_output=True, timeout=10,
        )
        return json.loads(result.stdout)

    def _agent_state_matchers(self, hooks_for_event: list) -> list:
        return [
            matcher for matcher in hooks_for_event
            for hook in matcher.get("hooks", [])
            if "agent-state.sh" in hook.get("command", "")
        ]

    def test_first_install_creates_exactly_one_matcher_per_event(self):
        result = self._merge({})
        for event in (
            "UserPromptSubmit", "PreToolUse", "PostToolUse",
            "Notification", "Stop",
        ):
            with self.subTest(event=event):
                ours = self._agent_state_matchers(result["hooks"][event])
                self.assertEqual(len(ours), 1)

    def test_rerun_purges_obsolete_session_start_registration(self):
        # The original bug: a previous version of claude-agents-bar
        # registered SessionStart → working. We no longer register that
        # event at all (it fires on every IDE tab switch), so the merge
        # must drop the stale matcher rather than leave it firing
        # forever alongside the rest.
        existing = {
            "hooks": {
                "SessionStart": [{
                    "hooks": [{
                        "type": "command",
                        "command": f"{os.environ['HOME']}/.claude/hooks/agent-state.sh working",
                        "async": True,
                    }],
                }],
            },
        }
        result = self._merge(existing)
        # No agent-state.sh matcher must survive on SessionStart.
        session_start_matchers = self._agent_state_matchers(
            result.get("hooks", {}).get("SessionStart", []),
        )
        self.assertEqual(session_start_matchers, [])

    def test_rerun_replaces_stale_argument_does_not_duplicate(self):
        # An older bundled version registered PreToolUse with a slightly
        # different argument (or path). The rerun must collapse to a
        # single matcher pointing at the current command line.
        old_path = f"{os.environ['HOME']}/.claude/hooks/agent-state.sh waiting"
        existing = {
            "hooks": {
                "PreToolUse": [{
                    "hooks": [{
                        "type": "command",
                        "command": old_path,
                        "async": True,
                    }],
                }],
            },
        }
        result = self._merge(existing)
        matchers = self._agent_state_matchers(result["hooks"]["PreToolUse"])
        self.assertEqual(len(matchers), 1)
        cmd = matchers[0]["hooks"][0]["command"]
        self.assertIn("agent-state.sh working", cmd)
        self.assertNotIn("agent-state.sh waiting", cmd)

    def test_user_hooks_on_same_event_are_preserved(self):
        # A hook the user has registered themselves on PreToolUse must
        # survive setup.sh — we only purge our own matchers.
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "/usr/local/bin/my-hook.sh"}]},
                    {"hooks": [{
                        "type": "command",
                        "command": f"{os.environ['HOME']}/.claude/hooks/agent-state.sh waiting",
                    }]},
                ],
            },
        }
        result = self._merge(existing)
        commands = [
            hook["command"]
            for matcher in result["hooks"]["PreToolUse"]
            for hook in matcher.get("hooks", [])
        ]
        self.assertIn("/usr/local/bin/my-hook.sh", commands)
        # Exactly one agent-state.sh registration, and it's the new one.
        ours = [c for c in commands if "agent-state.sh" in c]
        self.assertEqual(len(ours), 1)
        self.assertIn("agent-state.sh working", ours[0])

    def test_user_hook_sharing_a_matcher_with_ours_is_preserved(self):
        # Edge case: someone has packed our hook into the same matcher
        # as their own. We must scrub only the agent-state.sh entry and
        # leave their hook in place, even though the matcher object
        # itself stays.
        existing = {
            "hooks": {
                "PreToolUse": [{
                    "hooks": [
                        {"type": "command", "command": "/usr/local/bin/my-hook.sh"},
                        {"type": "command",
                         "command": f"{os.environ['HOME']}/.claude/hooks/agent-state.sh waiting"},
                    ],
                }],
            },
        }
        result = self._merge(existing)
        commands = [
            hook["command"]
            for matcher in result["hooks"]["PreToolUse"]
            for hook in matcher.get("hooks", [])
        ]
        self.assertIn("/usr/local/bin/my-hook.sh", commands)
        ours = [c for c in commands if "agent-state.sh" in c]
        self.assertEqual(len(ours), 1)
        self.assertIn("agent-state.sh working", ours[0])

    def test_unrelated_top_level_settings_are_preserved(self):
        existing = {
            "theme": "dark",
            "permissions": {"allow": ["Bash(git diff:*)"]},
        }
        result = self._merge(existing)
        self.assertEqual(result["theme"], "dark")
        self.assertEqual(result["permissions"], {"allow": ["Bash(git diff:*)"]})


if __name__ == "__main__":
    unittest.main()
