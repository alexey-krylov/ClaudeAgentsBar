"""Terminal sessions (spec 0016): the row marker and the terminal-aware click.

A session started in a terminal is already running somewhere, so its row
click must go *to that terminal* rather than fire the editor deeplink (which
would resume the same transcript in a second, parallel session).

Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path

from _helpers import plugin, _make_session, isolate_mode_sidecars

_SCRIPT = plugin.core.PLUGIN_DIR / "bin" / "app" / "open-terminal-session.sh"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _render_row(session, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        plugin.render._print_session_row(session, **kwargs)
    return buf.getvalue().splitlines()


def _plain(text: str) -> str:
    return _ANSI_RE.sub("", text)


class TestIsTerminal(unittest.TestCase):
    def test_cli_entrypoint_is_a_terminal_session(self):
        self.assertTrue(_make_session(entrypoint="cli").is_terminal)

    def test_editor_entrypoint_is_not(self):
        self.assertFalse(_make_session(entrypoint="claude-vscode").is_terminal)

    def test_unset_entrypoint_is_not(self):
        # Older transcripts may not carry the field. They stay on the editor
        # deeplink — the safer default of the two.
        self.assertFalse(_make_session(entrypoint="").is_terminal)

    def test_terminal_entrypoints_are_a_subset_of_interactive(self):
        # Anything we route to a terminal must survive the interactive filter,
        # or the row would never be rendered in the first place.
        self.assertTrue(
            plugin.core.TERMINAL_ENTRYPOINTS <= plugin.core.INTERACTIVE_ENTRYPOINTS
        )


class TestRowMarker(unittest.TestCase):
    def test_terminal_row_carries_the_marker(self):
        row = _render_row(_make_session(entrypoint="cli"))[0]
        self.assertIn("sfimage=greaterthan.square", row)

    def test_editor_row_has_no_marker(self):
        row = _render_row(_make_session(entrypoint="claude-vscode"))[0]
        self.assertNotIn("greaterthan.square", row)

    def test_marker_is_dimmed(self):
        # Provenance, not status — it must not compete with the state circle.
        row = _render_row(_make_session(entrypoint="cli"))[0]
        self.assertIn("sfcolor=systemGray", row)

    def test_marker_leaves_the_label_alone(self):
        # SwiftBar draws sfimage at the head of the row, so the label keeps
        # the order it has on every other row — group prefix, then title.
        isolate_mode_sidecars(self)
        original = plugin.core.CONFIG
        plugin.core.CONFIG = replace(plugin.core.CONFIG, ide_groups_mode="inline")
        self.addCleanup(lambda: setattr(plugin.core, "CONFIG", original))
        row = _plain(
            _render_row(_make_session(entrypoint="cli", ide_group="infra"))[0]
        )
        self.assertIn("infra · title", row)


class TestClickTarget(unittest.TestCase):
    def _main_row(self, **overrides):
        return _render_row(_make_session(**overrides))[0]

    def test_terminal_row_calls_the_terminal_action(self):
        row = self._main_row(entrypoint="cli", id="sid-1", cwd="/tmp/x")
        self.assertIn("open-terminal-session.sh", row)
        self.assertNotIn("open-session.sh", row)
        # Routed through /bin/bash so a lost executable bit can't kill it.
        self.assertIn("shell=/bin/bash", row)

    def test_terminal_row_passes_id_cwd_and_app(self):
        original = plugin.core.CONFIG
        plugin.core.CONFIG = replace(plugin.core.CONFIG, terminal_app="iTerm")
        self.addCleanup(lambda: setattr(plugin.core, "CONFIG", original))
        row = self._main_row(entrypoint="cli", id="sid-1", cwd="/tmp/x")
        self.assertIn('param2="sid-1"', row)
        self.assertIn('param3="/tmp/x"', row)
        self.assertIn('param4="iTerm"', row)

    def test_terminal_row_does_not_carry_the_editor_deeplink(self):
        row = self._main_row(entrypoint="cli", id="sid-1")
        self.assertNotIn("anthropic.claude-code/open", row)

    def test_editor_row_still_uses_the_deeplink(self):
        row = self._main_row(entrypoint="claude-vscode", id="sid-1")
        self.assertIn("open-session.sh", row)
        self.assertIn("anthropic.claude-code/open?session=sid-1", row)


class TestTerminalAppKnob(unittest.TestCase):
    def test_default_is_auto(self):
        self.assertEqual(plugin.Config().terminal_app, "auto")

    def test_known_apps_accepted(self):
        for value in ("auto", "Terminal", "iTerm"):
            self.assertEqual(
                plugin.Config._from_mapping({"terminal_app": value}).terminal_app,
                value,
            )

    def test_unknown_app_falls_back_to_default(self):
        # The value picks an AppleScript branch; an unknown one would make
        # clicks do nothing, so it's refused at load.
        self.assertEqual(
            plugin.Config._from_mapping({"terminal_app": "Warp"}).terminal_app,
            "auto",
        )


class TestActionScript(unittest.TestCase):
    """The script itself — shape and guard rails, without driving AppleScript."""

    def test_script_exists_and_is_executable(self):
        self.assertTrue(_SCRIPT.is_file())
        self.assertTrue(_SCRIPT.stat().st_mode & 0o111)

    def test_script_is_valid_bash(self):
        proc = subprocess.run(
            ["/bin/bash", "-n", str(_SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_missing_session_id_exits_nonzero_without_output(self):
        proc = subprocess.run(
            ["/bin/bash", str(_SCRIPT)], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")

    def test_tmux_name_is_validated_before_interpolation(self):
        # The tmux session name reaches a shell command, so the script only
        # accepts a plain name — the guard has to stay in place.
        body = _SCRIPT.read_text()
        self.assertIn("^[A-Za-z0-9_.-]+$", body)

    def test_fallback_uses_claude_resume(self):
        self.assertIn("claude --resume", _SCRIPT.read_text())

    def test_click_is_recorded_like_the_editor_path(self):
        self.assertIn("record-click.sh", _SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
