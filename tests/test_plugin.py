"""Unit tests for the pure helpers and the config loader in ``claude-agents.5s.py``.

Run with::

    /usr/bin/python3 -m unittest discover -s tests -v

Stdlib only — no pytest, no third-party deps. We load the plugin module
manually because its filename (``claude-agents.5s.py``) isn't a legal Python
identifier and SwiftBar's filename convention prevents us from renaming it.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import sys
import unittest
from pathlib import Path

_PLUGIN_PATH = Path(__file__).resolve().parent.parent / "claude-agents.5s.py"


def _load_plugin():
    """Import the plugin file under a clean module name.

    ``sys.modules[name] = module`` *before* ``exec_module`` is required so the
    plugin's ``@dataclass`` decorators can resolve their own ``__module__``
    when introspecting class annotations.
    """
    spec = importlib.util.spec_from_file_location("claude_agents_plugin", _PLUGIN_PATH)
    assert spec and spec.loader, f"could not locate {_PLUGIN_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin()


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


# --------------------------------------------------------------------------- #
# State classification                                                         #
# --------------------------------------------------------------------------- #


class TestClassify(unittest.TestCase):
    """End-to-end rules for the four render buckets.

    ``_classify(state, now, stop_ts, effective_click_ts)`` — we synthesise
    timestamps relative to ``CONFIG`` so the tests stay readable even when
    the defaults change.
    """

    def setUp(self):
        self.fresh = plugin.CONFIG.fresh_sec
        self.ack = plugin.CONFIG.ack_sec
        self.stop_ts = 1_700_000_000
        # 0 = "no click happened after the most recent Stop"
        self.no_click = 0

    def test_waiting_is_active(self):
        self.assertEqual(
            plugin._classify("waiting", self.stop_ts, self.stop_ts, self.no_click),
            plugin.RenderGroup.ACTIVE,
        )

    def test_working_is_active_regardless_of_age(self):
        # Active sessions stay active in classification — the watchdog demotes
        # them upstream in ``build_session`` before this is called.
        very_old = self.stop_ts + 10_000_000
        self.assertEqual(
            plugin._classify("working", very_old, self.stop_ts, self.no_click),
            plugin.RenderGroup.ACTIVE,
        )

    def test_idle_no_click_under_fresh_window(self):
        # First minute after Stop, no click yet → 🟢.
        now = self.stop_ts + 60
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, self.no_click),
            plugin.RenderGroup.FRESH,
        )

    def test_idle_no_click_in_ack_window(self):
        # fresh_sec has elapsed without a click → auto-promote to 🔵.
        now = self.stop_ts + self.fresh + 1
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, self.no_click),
            plugin.RenderGroup.ACKNOWLEDGED,
        )

    def test_idle_no_click_past_ack_window(self):
        # Both fresh and ack windows have elapsed without any click → ⚪.
        now = self.stop_ts + self.fresh + self.ack + 1
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, self.no_click),
            plugin.RenderGroup.STALE,
        )

    def test_idle_click_during_fresh_promotes_immediately(self):
        # Click landed at +30s, well inside fresh window. Now is +90s.
        # The click moves us straight to 🔵 — fresh ends at click_ts, not
        # at stop_ts + fresh_sec.
        click_ts = self.stop_ts + 30
        now = self.stop_ts + 90
        self.assertEqual(
            plugin._classify("idle", now, self.stop_ts, click_ts),
            plugin.RenderGroup.ACKNOWLEDGED,
        )

    def test_idle_click_resets_stale_timer(self):
        # User clicked late in the ack window. ack timer restarts from
        # click_ts, so an extra ack_sec must pass before STALE.
        click_ts = self.stop_ts + self.fresh + self.ack - 60
        now_just_after = click_ts + self.ack - 1
        now_after_window = click_ts + self.ack + 1
        self.assertEqual(
            plugin._classify("idle", now_just_after, self.stop_ts, click_ts),
            plugin.RenderGroup.ACKNOWLEDGED,
        )
        self.assertEqual(
            plugin._classify("idle", now_after_window, self.stop_ts, click_ts),
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
        raw = "sid1\tworking\t1700000000\tPreToolUse\t/tmp\n"
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid1"})
        snap = result["sid1"]
        self.assertEqual(snap.state, "working")
        self.assertEqual(snap.last_event_ts, 1700000000)
        self.assertEqual(snap.last_event_kind, "PreToolUse")
        self.assertEqual(snap.cwd, "/tmp")

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
        plugin.PROJECTS_DIR = projects
        plugin.SIDECAR_PATH = sidecar
        plugin.CLICKS_PATH = clicks
        # Redirect DISMISS_PATH too — without this the user's real cutoff
        # file (set by *Forget all sessions*) leaks into the test and
        # filters out every fake session whose synthetic ``now`` predates
        # the real cutoff.
        plugin.DISMISS_PATH = dismiss
        plugin._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        plugin._CLICKS_LOCK_DIR = clicks.with_suffix(clicks.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.clicks = clicks
        self.now = 1_700_000_000
        self.fresh = plugin.CONFIG.fresh_sec

    def tearDown(self):
        import shutil
        plugin.PROJECTS_DIR = self._orig_projects
        plugin.SIDECAR_PATH = self._orig_sidecar
        plugin.CLICKS_PATH = self._orig_clicks
        plugin.DISMISS_PATH = self._orig_dismiss
        plugin._SIDECAR_LOCK_DIR = self._orig_sidecar_lock
        plugin._CLICKS_LOCK_DIR = self._orig_clicks_lock
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

    def test_promotes_session_without_sidecar_row(self):
        # No Stop hook captured — only JSONL mtime. This is the case that
        # the sidecar-only implementation missed and motivated routing
        # ack_fresh through collect_sessions.
        self._make_session("untracked", self.now - 60)
        self.assertEqual(plugin.ack_fresh(self.now), 1)
        self.assertEqual(self._read_clicks(), {"untracked": self.now})

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
        plugin.DISMISS_PATH = Path(self._tmp.name)

    def tearDown(self):
        plugin.DISMISS_PATH = self._original
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
        plugin.CONFIG = replace(plugin.CONFIG, menubar_icon=value)
        self.addCleanup(self._restore)

    def _restore(self):
        plugin.CONFIG = self._original_config

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
        plugin._resized_menubar_image = lambda src: src
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
        plugin.CONFIG = replace(
            original,
            menubar_icon="template:/no/such/file/at/all.png",
            menubar_icon_fallback="🤖",
        )
        try:
            params, glyph = plugin._menubar_icon_pieces()
            self.assertEqual(params, "")
            self.assertEqual(glyph, "🤖")
        finally:
            plugin.CONFIG = original


# --------------------------------------------------------------------------- #
# SwiftBar quoting                                                             #
# --------------------------------------------------------------------------- #


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
