"""Menu rendering: icon pieces, model badge, worktree/collision markers.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin, _make_session


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



class TestModelBadge(unittest.TestCase):
    """Pure logic in :func:`core._model_badge`.

    Rules from `docs/specs/0004-subagent-grouping.md` § model surface:
    glyph ≠ default → family-based; = default → empty; unparseable → ⓜ.
    """

    def test_none_falls_back_to_m(self):
        self.assertEqual(
            plugin.core._model_badge(None, "claude-opus-4-7"), "ⓜ")

    def test_matches_default_returns_empty(self):
        self.assertEqual(
            plugin.core._model_badge("claude-opus-4-7", "claude-opus-4-7"),
            "",
        )

    def test_opus_renders_circled_o(self):
        self.assertEqual(
            plugin.core._model_badge("claude-opus-4-7", "claude-sonnet-4-6"),
            "ⓞ",
        )

    def test_sonnet_renders_circled_s(self):
        self.assertEqual(
            plugin.core._model_badge("claude-sonnet-4-6", "claude-opus-4-7"),
            "ⓢ",
        )

    def test_haiku_renders_circled_h(self):
        self.assertEqual(
            plugin.core._model_badge("claude-haiku-4-5", "claude-opus-4-7"),
            "ⓗ",
        )

    def test_fable_renders_circled_f(self):
        self.assertEqual(
            plugin.core._model_badge("claude-fable-5", "claude-opus-4-8"),
            "ⓕ",
        )

    def test_non_claude_provider_falls_back_to_m(self):
        # OpenRouter, custom endpoints, anything we don't recognise.
        self.assertEqual(
            plugin.core._model_badge("gpt-4-turbo", "claude-opus-4-7"),
            "ⓜ",
        )

    def test_default_none_falls_through_to_family_badge(self):
        # "Safe degradation" path — user has no default set anywhere.
        # Every row gets a badge so an absent badge never surprises.
        self.assertEqual(plugin.core._model_badge("claude-opus-4-7", None), "ⓞ")
        self.assertEqual(plugin.core._model_badge("claude-haiku-4-5", None), "ⓗ")

    def test_empty_default_treated_as_unset(self):
        # An empty-string default came from a malformed settings.json;
        # treat it as "no default" rather than as a literal match.
        self.assertEqual(plugin.core._model_badge("claude-opus-4-7", ""), "ⓞ")



class TestDefaultModelFor(unittest.TestCase):
    """:func:`core._default_model_for` reads two settings files; the
    ``<cwd>/.claude/settings.local.json`` overrides ``~/.claude/settings.json``.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.cwd = Path(self._tmp.name) / "repo"
        (self.home / ".claude").mkdir(parents=True)
        (self.cwd / ".claude").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_home_settings(self, body) -> None:
        (self.home / ".claude" / "settings.json").write_text(
            json.dumps(body), encoding="utf-8",
        )

    def _write_local_settings(self, body) -> None:
        (self.cwd / ".claude" / "settings.local.json").write_text(
            json.dumps(body), encoding="utf-8",
        )

    def _call(self, cwd=None):
        with patch.object(plugin.core, "HOME", self.home):
            return plugin.core._default_model_for(cwd or str(self.cwd))

    def test_home_settings_only(self):
        self._write_home_settings({"model": "claude-opus-4-7"})
        self.assertEqual(self._call(), "claude-opus-4-7")

    def test_cwd_local_overrides_home(self):
        self._write_home_settings({"model": "claude-sonnet-4-6"})
        self._write_local_settings({"model": "claude-opus-4-7"})
        self.assertEqual(self._call(), "claude-opus-4-7")

    def test_cwd_local_without_model_keeps_home(self):
        # ``settings.local.json`` exists but doesn't declare a model — the
        # home-level value must remain (we overlay, not replace).
        self._write_home_settings({"model": "claude-sonnet-4-6"})
        self._write_local_settings({"theme": "dark"})
        self.assertEqual(self._call(), "claude-sonnet-4-6")

    def test_neither_file_returns_none(self):
        self.assertIsNone(self._call())

    def test_bad_json_falls_back_to_none(self):
        (self.home / ".claude" / "settings.json").write_text(
            "{ not json", encoding="utf-8",
        )
        self.assertIsNone(self._call())

    def test_non_string_model_field_is_ignored(self):
        self._write_home_settings({"model": 12345})
        self.assertIsNone(self._call())

    def test_empty_cwd_only_consults_home(self):
        # When the session has no cwd we still want the home default.
        self._write_home_settings({"model": "claude-opus-4-7"})
        self.assertEqual(self._call(cwd=""), "claude-opus-4-7")



class TestLastSessionModel(unittest.TestCase):
    """JSONL tail parser for the ``"model":"..."`` field."""

    def _write(self, lines: list[str]) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, dir=tempfile.gettempdir(), mode="w",
            encoding="utf-8",
        )
        for line in lines:
            tmp.write(line)
            tmp.write("\n")
        tmp.close()
        p = Path(tmp.name)
        self.addCleanup(lambda: p.unlink(missing_ok=True))
        return p

    # Real-world JSONL from Claude Code is written with no whitespace
    # between keys and values (the spike's dump shows this). The regex
    # in :data:`core._MODEL_RE` matches the compact form only, so tests
    # use the same ``separators=(",",":")`` form here.
    _DUMP_COMPACT_KWARGS = {"separators": (",", ":")}

    def test_returns_last_match_when_multiple(self):
        # Mixed-model session (user switched mid-stream via /model).
        # Latest model wins — the badge answers "what am I jumping into".
        p = self._write([
            json.dumps({"type": "assistant", "message": {
                "model": "claude-sonnet-4-6", "content": []}},
                **self._DUMP_COMPACT_KWARGS),
            json.dumps({"type": "assistant", "message": {
                "model": "claude-opus-4-7", "content": []}},
                **self._DUMP_COMPACT_KWARGS),
        ])
        self.assertEqual(plugin.sidecars.last_session_model(p), "claude-opus-4-7")

    def test_returns_none_when_no_model_field(self):
        p = self._write([
            json.dumps({"type": "user", "message": {"content": "hi"}},
                       **self._DUMP_COMPACT_KWARGS),
        ])
        self.assertIsNone(plugin.sidecars.last_session_model(p))

    def test_returns_none_for_empty_file(self):
        p = self._write([])
        self.assertIsNone(plugin.sidecars.last_session_model(p))

    def test_returns_none_for_missing_file(self):
        p = Path(tempfile.gettempdir()) / "does-not-exist-9999.jsonl"
        self.assertIsNone(plugin.sidecars.last_session_model(p))

    def test_captures_non_claude_provider(self):
        # OpenRouter / custom strings come back too — the regex is
        # deliberately loose so the submenu can surface them; the badge
        # function maps them to ⓜ.
        p = self._write([
            json.dumps({"type": "assistant", "message": {
                "model": "openrouter/anthropic/claude-3.5-sonnet",
                "content": []}},
                **self._DUMP_COMPACT_KWARGS),
        ])
        self.assertEqual(
            plugin.sidecars.last_session_model(p),
            "openrouter/anthropic/claude-3.5-sonnet",
        )



class TestIsWorktreeCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_plain_repo_dot_git_dir_is_not_worktree(self):
        # A normal checkout stores ``.git`` as a directory.
        os.mkdir(os.path.join(self.tmp, ".git"))
        self.assertFalse(plugin.sidecars.is_worktree_checkout(self.tmp))

    def test_worktree_dot_git_file_is_worktree(self):
        # A linked worktree stores ``.git`` as a file pointing at the gitdir.
        with open(os.path.join(self.tmp, ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: /some/repo/.git/worktrees/wt1\n")
        self.assertTrue(plugin.sidecars.is_worktree_checkout(self.tmp))

    def test_non_repo_is_not_worktree(self):
        self.assertFalse(plugin.sidecars.is_worktree_checkout(self.tmp))

    def test_empty_cwd_is_not_worktree(self):
        self.assertFalse(plugin.sidecars.is_worktree_checkout(""))

    def test_dot_git_file_without_gitdir_marker_is_not_worktree(self):
        with open(os.path.join(self.tmp, ".git"), "w", encoding="utf-8") as fh:
            fh.write("garbage\n")
        self.assertFalse(plugin.sidecars.is_worktree_checkout(self.tmp))



class TestCwdCollision(unittest.TestCase):
    def test_two_active_sessions_same_cwd_collide(self):
        from claude_agents_bar import render
        a = _make_session(id="a", hook_state="working", cwd="/work/proj")
        b = _make_session(id="b", hook_state="waiting", cwd="/work/proj")
        c = _make_session(id="c", hook_state="working", cwd="/work/other")
        render._mark_cwd_collisions([a, b, c])
        self.assertTrue(a.cwd_collision)
        self.assertTrue(b.cwd_collision)
        self.assertFalse(c.cwd_collision)

    def test_path_normalization_groups_equivalent_cwds(self):
        from claude_agents_bar import render
        a = _make_session(id="a", hook_state="working", cwd="/work/proj")
        b = _make_session(id="b", hook_state="working", cwd="/work/proj/")
        render._mark_cwd_collisions([a, b])
        self.assertTrue(a.cwd_collision)
        self.assertTrue(b.cwd_collision)

    def test_idle_sessions_do_not_collide(self):
        from claude_agents_bar import render
        a = _make_session(id="a", hook_state="idle", cwd="/work/proj")
        b = _make_session(id="b", hook_state="idle", cwd="/work/proj")
        render._mark_cwd_collisions([a, b])
        self.assertFalse(a.cwd_collision)
        self.assertFalse(b.cwd_collision)

    def test_empty_cwd_never_collides(self):
        from claude_agents_bar import render
        a = _make_session(id="a", hook_state="working", cwd="")
        b = _make_session(id="b", hook_state="working", cwd="")
        render._mark_cwd_collisions([a, b])
        self.assertFalse(a.cwd_collision)
        self.assertFalse(b.cwd_collision)

    def test_single_active_session_does_not_collide(self):
        from claude_agents_bar import render
        a = _make_session(id="a", hook_state="working", cwd="/work/proj")
        render._mark_cwd_collisions([a])
        self.assertFalse(a.cwd_collision)



class TestWorktreeRowMarker(unittest.TestCase):
    """The inline ``ⓦ`` marker on the main row mirrors the submenu's green
    branch line: present (green) for a worktree, red when that worktree also
    collides, absent otherwise.
    """

    def _render_row(self, **overrides):
        import contextlib
        import io
        from claude_agents_bar import render
        session = _make_session(**overrides)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            render._print_session_row(session)
        # Only the first line is the main row; the rest is the submenu.
        return buf.getvalue().splitlines()[0]

    def test_non_worktree_has_no_marker(self):
        self.assertNotIn("ⓦ", self._render_row(is_worktree=False))

    def test_worktree_shows_green_marker(self):
        row = self._render_row(is_worktree=True)
        self.assertIn(f"{plugin.core._ANSI_FRESH_BAR}ⓦ{plugin.core._ANSI_RESET}", row)

    def test_colliding_worktree_shows_red_marker_only(self):
        row = self._render_row(is_worktree=True, cwd_collision=True)
        self.assertIn(f"{plugin.core._ANSI_WAITING}ⓦ{plugin.core._ANSI_RESET}", row)
        # The red ⓦ absorbs the collision signal — the branch glyph is suppressed.
        self.assertNotIn("⎇", row)

    def test_non_worktree_collision_shows_branch_glyph(self):
        row = self._render_row(is_worktree=False, cwd_collision=True)
        self.assertIn(f"{plugin.core._ANSI_WAITING}⎇{plugin.core._ANSI_RESET}", row)
        self.assertNotIn("ⓦ", row)


if __name__ == "__main__":
    unittest.main()


class TestWorktreeMainRepo(unittest.TestCase):
    """A worktree row is labelled with its owning repo, not its own folder.

    The worktree directory is named after the branch, which the submenu's
    branch line already shows — labelling the row with it says the same thing
    twice and hides which project the session actually belongs to.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def _write_git(self, content):
        with open(os.path.join(self.tmp, ".git"), "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_resolves_the_owning_repository(self):
        self._write_git("gitdir: /Users/me/Projects/Repo/.git/worktrees/my-branch\n")
        self.assertEqual(
            plugin.sidecars.worktree_main_repo(self.tmp), "/Users/me/Projects/Repo"
        )

    def test_project_name_becomes_the_repo_name(self):
        self._write_git("gitdir: /Users/me/Projects/Repo/.git/worktrees/my-branch\n")
        main = plugin.sidecars.worktree_main_repo(self.tmp)
        self.assertEqual(plugin.core._project_name(main or self.tmp, ""), "Repo")

    def test_plain_checkout_yields_empty(self):
        os.mkdir(os.path.join(self.tmp, ".git"))
        self.assertEqual(plugin.sidecars.worktree_main_repo(self.tmp), "")

    def test_unexpected_marker_shape_yields_empty(self):
        self._write_git("gitdir: /some/place/without/the/needle\n")
        self.assertEqual(plugin.sidecars.worktree_main_repo(self.tmp), "")

    def test_missing_repo_and_empty_cwd_yield_empty(self):
        self.assertEqual(plugin.sidecars.worktree_main_repo(self.tmp), "")
        self.assertEqual(plugin.sidecars.worktree_main_repo(""), "")

    def test_gitdir_at_filesystem_root_yields_empty(self):
        # idx == 0 would leave an empty repo path; guard against it.
        self._write_git("gitdir: /.git/worktrees/wt\n")
        self.assertEqual(plugin.sidecars.worktree_main_repo(self.tmp), "")


class TestFolderLineOpensFinder(unittest.TestCase):
    """The submenu's folder line is a click target: it reveals cwd in Finder."""

    def _rows(self, **overrides):
        session = _make_session(**overrides)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_session_row(session)
        return buf.getvalue().splitlines()

    def _folder_line(self, **overrides):
        return next(r for r in self._rows(**overrides) if "sfimage=folder" in r)

    def test_project_line_opens_cwd(self):
        line = self._folder_line(
            project="Repo", cwd="/tmp/repo", git_branch="main"
        )
        self.assertIn("shell=/usr/bin/open", line)
        self.assertIn('param1="/tmp/repo"', line)
        # Opening a Finder window changes nothing the menu shows.
        self.assertIn("refresh=false", line)

    def test_project_line_always_carries_the_path_as_tooltip(self):
        # It's clickable now, so it has to say where the click lands — even
        # for a worktree, whose branch line carries a status tooltip.
        line = self._folder_line(
            project="Repo", cwd="/tmp/repo", git_branch="main", is_worktree=True
        )
        self.assertIn('tooltip="/tmp/repo"', line)

    def test_bare_cwd_line_opens_too(self):
        # No git branch: the line carries the full path instead of a name.
        line = self._folder_line(project="", cwd="/tmp/plain", git_branch="")
        self.assertIn("sfimage=folder.fill", line)
        self.assertIn('param1="/tmp/plain"', line)

    def test_no_cwd_means_no_click(self):
        rows = self._rows(project="Repo", cwd="", git_branch="main")
        folder_lines = [r for r in rows if "sfimage=folder" in r]
        for line in folder_lines:
            self.assertNotIn("shell=", line)

    def test_path_with_spaces_is_quoted(self):
        line = self._folder_line(
            project="Repo", cwd="/tmp/my repo", git_branch="main"
        )
        self.assertIn('param1="/tmp/my repo"', line)


class TestWorktreeClickTargets(unittest.TestCase):
    """Project line opens the repo; the branch line opens the worktree."""

    def _lines(self, **overrides):
        session = _make_session(**overrides)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_session_row(session)
        rows = buf.getvalue().splitlines()
        project = next(r for r in rows if "sfimage=folder" in r)
        branch = next(r for r in rows if "arrow.triangle.branch" in r)
        return project, branch

    def _worktree_session(self, **extra):
        return dict(
            project="ClaudeAgentsBar",
            project_dir="/Users/me/Projects/ClaudeAgentsBar",
            cwd="/Users/me/Projects/ClaudeAgentsBar/.claude/worktrees/feature",
            git_branch="worktree-feature",
            is_worktree=True,
            **extra,
        )

    def test_project_line_opens_the_repository_not_the_worktree(self):
        project, _ = self._lines(**self._worktree_session())
        self.assertIn('param1="/Users/me/Projects/ClaudeAgentsBar"', project)
        self.assertNotIn("worktrees/feature", project.split("tooltip=")[0])

    def test_project_tooltip_states_the_repository_path(self):
        project, _ = self._lines(**self._worktree_session())
        self.assertIn('tooltip="/Users/me/Projects/ClaudeAgentsBar"', project)

    def test_branch_line_opens_the_worktree(self):
        _, branch = self._lines(**self._worktree_session())
        self.assertIn("shell=/usr/bin/open", branch)
        self.assertIn(
            'param1="/Users/me/Projects/ClaudeAgentsBar/.claude/worktrees/feature"',
            branch,
        )

    def test_branch_tooltip_keeps_the_status_note_and_adds_the_path(self):
        _, branch = self._lines(**self._worktree_session())
        tooltip = branch.split("tooltip=")[1]
        self.assertIn("worktrees/feature", tooltip)
        self.assertIn(plugin.core._t("tooltip.worktree"), tooltip)

    def test_plain_checkout_branch_line_is_not_clickable(self):
        project, branch = self._lines(
            project="Repo", project_dir="/tmp/repo", cwd="/tmp/repo",
            git_branch="main", is_worktree=False,
        )
        self.assertIn('param1="/tmp/repo"', project)
        self.assertNotIn("shell=", branch)

    def test_project_dir_falls_back_to_cwd(self):
        # Sessions built before the field existed, or a worktree we couldn't
        # resolve: the project line still opens something sensible.
        project, _ = self._lines(
            project="Repo", project_dir="", cwd="/tmp/repo", git_branch="main"
        )
        self.assertIn('param1="/tmp/repo"', project)


class TestPassiveRowsAreUnselectable(unittest.TestCase):
    """Read-only rows must not carry ``color=``.

    SwiftBar hangs a click action on every row with a ``color=`` parameter so
    it can repaint the custom colour against the selection highlight
    (``MenuBarItem.configureAction``: ``params.hasAction || params.color !=
    nil``). A row with an action highlights under the cursor and takes
    keyboard focus — misleading on a line that does nothing when clicked. Grey
    therefore travels as ANSI (:func:`render._dim`), which SwiftBar renders
    without attaching an action.
    """

    #: Status colours worth the selectable row they cost — a cwd collision, a
    #: worktree checkout, a subagent still in flight.
    STATUS_COLOURS = ("#cc0000", "#1f7a1f", "#cc7700")

    def _rows(self, **overrides):
        session = _make_session(**overrides)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_session_row(session)
        return buf.getvalue().splitlines()

    def _assert_passive_rows_are_colourless(self, rows):
        for row in rows:
            params = row.split(" | ", 1)[1] if " | " in row else ""
            has_action = any(
                p in params
                for p in ("shell=", "bash=", "href=", "stdin=", "refresh=true")
            )
            colour = params.replace("sfcolor=", "")
            if has_action or "color=" not in colour:
                continue
            self.assertTrue(
                any(c in params for c in self.STATUS_COLOURS),
                f"passive row carries color= and would be selectable: {row!r}",
            )

    def test_plain_session_submenu_has_no_passive_colour(self):
        self._assert_passive_rows_are_colourless(self._rows(
            project="Repo", project_dir="/tmp/repo", cwd="/tmp/repo",
            git_branch="main", model="claude-opus-5", context_used=42_000,
            ide_group="backend",
        ))

    def test_branch_line_is_grey_via_ansi(self):
        rows = self._rows(
            project="Repo", project_dir="/tmp/repo", cwd="/tmp/repo",
            git_branch="main",
        )
        branch = next(r for r in rows if "arrow.triangle.branch" in r)
        self.assertIn("ansi=true", branch)
        self.assertIn(plugin.core._ANSI_DIM, branch)
        self.assertNotIn("color=", branch)


class TestDimHelper(unittest.TestCase):
    """:func:`render._dim` wraps a label in grey without bleaching its spans."""

    def test_wraps_plain_text(self):
        out = plugin.render._dim("main")
        self.assertEqual(
            out, plugin.core._ANSI_DIM + "main" + plugin.core._ANSI_RESET
        )

    def test_nested_reset_reopens_the_grey(self):
        # A coloured span inside a dimmed line (the usage bar, a warn
        # percentage) closes with a reset. Left alone it would drop the rest
        # of the line to the default label colour instead of back to grey.
        inner = f"{plugin.core._ANSI_WORKING}63{plugin.core._ANSI_RESET}%"
        out = plugin.render._dim(f"Session {inner}")
        self.assertTrue(out.endswith(plugin.core._ANSI_RESET))
        self.assertEqual(out.count(plugin.core._ANSI_RESET), 1)
        self.assertEqual(out.count(plugin.core._ANSI_DIM), 2)
