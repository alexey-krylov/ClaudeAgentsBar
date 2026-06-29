"""Transcript parsing: titles, user/tool-use previews, the response marker.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from _helpers import plugin


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



class TestReadTranscriptMetaTailFallback(unittest.TestCase):
    """Recover ``ai-title`` from the tail when bloated early events push the
    first one past the head-scan window.

    Regression: a first message with pasted images (huge base64) drove the
    first ``ai-title`` past :data:`JSONL_TITLE_SCAN_BYTES`, so the head scan
    returned an empty ``ai_title`` and the row title flapped between the
    sliding ``last_user_message`` tail and ``raw_title`` as tool output
    pushed the latest prompt out of the tail window.
    """

    def _write(self, body: str) -> Path:
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        path = Path(path)
        path.write_text(body, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    def test_ai_title_recovered_from_tail_when_past_head_window(self):
        from claude_agents_bar import core as core_mod
        # A small first user event (sets cwd/raw_title/entrypoint), then one
        # filler line big enough to drive ``consumed`` past the head window
        # so the scan breaks before reaching the ai-title — which then sits
        # at the very end, inside the tail buffer.
        filler = "x" * (core_mod.JSONL_TITLE_SCAN_BYTES + 4096)
        body = (
            '{"type":"user","cwd":"/proj","entrypoint":"cli",'
            '"message":{"content":[{"type":"text","text":"hi"}]}}\n'
            '{"type":"assistant","message":{"content":"' + filler + '"}}\n'
            '{"type":"ai-title","aiTitle":"Recovered Topic"}\n'
        )
        meta = plugin.read_transcript_meta(self._write(body))
        self.assertEqual(meta.ai_title, "Recovered Topic")
        self.assertEqual(meta.display_title, "Recovered Topic")

    def test_head_ai_title_still_preferred_when_in_window(self):
        # Normal case stays untouched: the head scan finds the first
        # ai-title and the tail is never consulted for it.
        body = (
            '{"type":"user","cwd":"/proj","entrypoint":"cli",'
            '"message":{"content":[{"type":"text","text":"hi"}]}}\n'
            '{"type":"ai-title","aiTitle":"Head Topic"}\n'
            '{"type":"ai-title","aiTitle":"Later Topic"}\n'
        )
        meta = plugin.read_transcript_meta(self._write(body))
        self.assertEqual(meta.ai_title, "Head Topic")

    def test_no_ai_title_anywhere_leaves_empty(self):
        body = (
            '{"type":"user","cwd":"/proj","entrypoint":"cli",'
            '"message":{"content":[{"type":"text","text":"hi"}]}}\n'
        )
        meta = plugin.read_transcript_meta(self._write(body))
        self.assertEqual(meta.ai_title, "")



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

    def test_session_title_wins_over_ai_title(self):
        # The Russian session name parsed from the response marker takes
        # precedence over Claude Code's English ai-title.
        meta = plugin.TranscriptMeta(
            session_title="Чиню баг",
            ai_title="Fixing the bug",
            last_user_message="latest",
            raw_title="first",
        )
        self.assertEqual(meta.display_title, "Чиню баг")



class TestSessionTitleMarker(unittest.TestCase):
    """Parse the two-field summary marker ``*-- Name - Summary*`` for the menu
    title. The name is the text before the first ``" - "``; a single-field line
    yields an empty name, so the title falls through to ``ai_title``."""

    def setUp(self):
        from claude_agents_bar import sidecars
        self.s = sidecars

    def test_parse_two_fields(self):
        self.assertEqual(
            self.s._parse_marker_line("*-- Чиню баг - нашёл причину*", "-- "),
            ("Чиню баг", "нашёл причину"),
        )

    def test_split_on_first_divider_only(self):
        # The summary may itself contain ` - `; only the first divider splits
        # off the name.
        self.assertEqual(
            self.s._parse_marker_line("-- Имя - a - b - c", "-- "),
            ("Имя", "a - b - c"),
        )

    def test_single_field_yields_empty_name(self):
        self.assertEqual(
            self.s._parse_marker_line("*-- just a summary*", "-- "),
            ("", "just a summary"),
        )

    def test_non_marker_line_is_none(self):
        self.assertIsNone(self.s._parse_marker_line("plain text", "-- "))

    def test_emphasis_wrappers_stripped(self):
        for line in ("*-- N - S*", "_-- N - S_", "**-- N - S**", "-- N - S"):
            self.assertEqual(self.s._parse_marker_line(line, "-- "), ("N", "S"), line)

    def test_empty_marker_disables(self):
        self.assertIsNone(self.s._parse_marker_line("-- N - S", ""))

    def test_name_from_reply_uses_closing_line(self):
        # Only the last non-blank line is the marker; an earlier ``- ``-ish
        # list item can't false-match.
        text = "разбираюсь\n- пункт списка\n\n*-- Реальное имя - summary*"
        self.assertEqual(
            self.s._session_name_from_reply(text, "-- "), "Реальное имя"
        )

    def test_name_from_reply_without_marker_is_empty(self):
        self.assertEqual(self.s._session_name_from_reply("no marker", "-- "), "")



class TestReadTranscriptMetaSessionTitle(unittest.TestCase):
    """``read_transcript_meta`` lifts the latest reply's session name into
    ``session_title`` — but only when ``use_session_titles_for_menubar`` is
    on; otherwise the menu shows ``ai-title``. The parse is further gated on
    ``notify_summary_marker``."""

    def setUp(self):
        # The marker→menu-title feature is opt-in; enable it for the positive
        # tests below (the default-off path has its own test).
        from dataclasses import replace
        self._orig_config = plugin.core.CONFIG
        plugin.core.CONFIG = replace(
            plugin.core.CONFIG, use_session_titles_for_menubar=True
        )

    def tearDown(self):
        plugin.core.CONFIG = self._orig_config

    def _write(self, body: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        path = Path(path)
        path.write_text(body, encoding="utf-8")
        self.addCleanup(path.unlink)
        return path

    _PREAMBLE = (
        '{"type":"user","cwd":"/p","entrypoint":"cli",'
        '"message":{"content":[{"type":"text","text":"hi"}]}}\n'
        '{"type":"ai-title","aiTitle":"English Topic"}\n'
    )

    def test_latest_marker_name_wins(self):
        body = self._PREAMBLE + (
            '{"type":"assistant","message":{"content":'
            '[{"type":"text","text":"a\\n\\n*-- Старое имя - old*"}]}}\n'
            '{"type":"assistant","message":{"content":'
            '[{"type":"text","text":"b\\n\\n*-- Новое имя - new*"}]}}\n'
        )
        meta = plugin.read_transcript_meta(self._write(body))
        self.assertEqual(meta.session_title, "Новое имя")
        self.assertEqual(meta.display_title, "Новое имя")

    def test_single_field_falls_through_to_ai_title(self):
        body = self._PREAMBLE + (
            '{"type":"assistant","message":{"content":'
            '[{"type":"text","text":"*-- summary only*"}]}}\n'
        )
        meta = plugin.read_transcript_meta(self._write(body))
        self.assertEqual(meta.session_title, "")
        self.assertEqual(meta.display_title, "English Topic")

    def test_marker_disabled_skips_parse(self):
        from dataclasses import replace
        body = self._PREAMBLE + (
            '{"type":"assistant","message":{"content":'
            '[{"type":"text","text":"*-- Имя - s*"}]}}\n'
        )
        path = self._write(body)
        # Inherit the setUp's use_session_titles_for_menubar=True, then also
        # disable the marker — the parse must still be skipped.
        plugin.core.CONFIG = replace(
            plugin.core.CONFIG, notify_summary_marker=""
        )
        meta = plugin.read_transcript_meta(path)
        self.assertEqual(meta.session_title, "")
        self.assertEqual(meta.display_title, "English Topic")

    def test_option_off_uses_ai_title(self):
        # Default (use_session_titles_for_menubar=False): even a valid marker
        # is ignored for the menu title, which stays the VSCode-consistent
        # ai-title.
        from dataclasses import replace
        body = self._PREAMBLE + (
            '{"type":"assistant","message":{"content":'
            '[{"type":"text","text":"b\\n\\n*-- Русское имя - s*"}]}}\n'
        )
        path = self._write(body)
        plugin.core.CONFIG = replace(
            plugin.core.CONFIG, use_session_titles_for_menubar=False
        )
        meta = plugin.read_transcript_meta(path)
        self.assertEqual(meta.session_title, "")
        self.assertEqual(meta.display_title, "English Topic")


if __name__ == "__main__":
    unittest.main()
