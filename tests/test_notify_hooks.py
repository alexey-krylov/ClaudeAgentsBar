"""End-to-end: the notification hooks name a session the way its menu row does.

Runs the real ``hooks/notify-*.sh`` under a throwaway ``HOME`` and config,
with a fake ``terminal-notifier`` first on ``PATH`` that records its argv, and
asserts on the banner it would have shown. Sound and voice are switched off
in the config, so nothing plays.

Regressions: ``notify-stop.sh`` built the banner title in Bash (ai-title →
first prompt) and ignored a manual rename, so the banner and the menu named
the same session differently; and the subtitle labelled a worktree by its own
branch-named directory instead of the owning repository the menu shows.

Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from _helpers import plugin

_REPO = Path(__file__).resolve().parent.parent
_PLUGIN = _REPO / "claude-agents.5s.py"
_SID = "11111111-2222-3333-4444-555555555555"

# Compact separators, as Claude Code writes them — the readers prefilter on
# the literal bytes ``"type":"ai-title"``.
_TRANSCRIPT = "".join(
    json.dumps(event, separators=(",", ":")) + "\n"
    for event in (
        {"type": "user", "cwd": "/proj", "entrypoint": "cli",
         "message": {"content": [{"type": "text", "text": "first prompt"}]}},
        {"type": "ai-title", "aiTitle": "A"},
        {"type": "custom-title", "customTitle": "B1"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Done.\n\n*-- C - summary*"}]}},
        {"type": "custom-title", "customTitle": "B2"},
    )
)

def _make_repo(root: Path, branch: str) -> Path:
    """A minimal checkout: ``.git/HEAD`` on ``branch`` — all the readers need."""
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    return root


def _make_worktree(repo: Path, wt: Path, branch: str) -> Path:
    """A linked worktree of ``repo``: ``.git`` file → ``.git/worktrees/<name>``."""
    gitdir = repo / ".git" / "worktrees" / wt.name
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    return wt


_FAKE_NOTIFIER = """#!/bin/bash
out="$(dirname "$0")/../notifier.args"
printf '%s\\0' "$@" > "$out.tmp" && mv "$out.tmp" "$out"
"""


class TestBannerNamesSessionLikeMenu(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cab-notify-")
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        project = self.home / ".claude" / "projects" / "-proj"
        project.mkdir(parents=True)
        self.transcript = project / f"{_SID}.jsonl"
        self.transcript.write_text(_TRANSCRIPT, encoding="utf-8")
        bin_dir = self.home / "bin"
        bin_dir.mkdir()
        notifier = bin_dir / "terminal-notifier"
        notifier.write_text(_FAKE_NOTIFIER, encoding="utf-8")
        notifier.chmod(0o755)
        self.args_file = self.home / "notifier.args"
        self.config = self.home / "config.json"

    def _env(self, flag: bool) -> dict:
        self.config.write_text(json.dumps({
            "use_session_titles_for_menubar": flag,
            "notify_summary_marker": "-- ",
            "notify_threshold_sec": 0,
            "notify_voice": "off",
            "notify_sound_stop": None,
            "notify_sound_wait": None,
        }), encoding="utf-8")
        env = dict(os.environ)
        env.update(
            HOME=str(self.home),
            CLAUDE_AGENTS_BAR_CONFIG=str(self.config),
            PATH=f"{self.home / 'bin'}:/usr/bin:/bin",
        )
        env.pop("XDG_CONFIG_HOME", None)
        return env

    def _banner(self, argv: list, env: dict, stdin: str = "") -> dict:
        subprocess.run(argv, input=stdin, env=env, text=True, check=True,
                       capture_output=True, timeout=30)
        # The hook backgrounds terminal-notifier; wait for its argv dump.
        deadline = time.time() + 10
        while not self.args_file.exists() and time.time() < deadline:
            time.sleep(0.05)
        parts = self.args_file.read_bytes().decode("utf-8").split("\0")
        return {parts[i]: parts[i + 1] for i in range(len(parts) - 1)
                if parts[i] in ("-title", "-subtitle", "-message")}

    def _stop(self, flag: bool, cwd: str = "") -> dict:
        payload = json.dumps({
            "transcript_path": str(self.transcript),
            "session_id": _SID,
            "cwd": cwd or str(self.home),
        })
        return self._banner(["/bin/bash", str(_REPO / "hooks" / "notify-stop.sh")],
                            self._env(flag), payload)

    def test_stop_title_is_marker_name_when_flag_on(self):
        banner = self._stop(flag=True)
        self.assertEqual(banner["-title"], "C")
        self.assertEqual(banner["-message"], "summary")

    def test_stop_title_is_latest_rename_when_flag_off(self):
        banner = self._stop(flag=False)
        self.assertEqual(banner["-title"], "B2")
        self.assertEqual(banner["-message"], "summary")

    def test_stop_subtitle_names_worktree_by_owning_repo(self):
        repo = _make_repo(self.home / "Repo", "main")
        wt = _make_worktree(repo, self.home / "fix-x", "fix/x")
        banner = self._stop(flag=False, cwd=str(wt))
        self.assertEqual(banner["-subtitle"], "Repo — ⓦ fix/x")

    def test_wait_banner_pairs_menu_title_with_summary(self):
        banner = self._banner(
            ["/bin/bash", str(_REPO / "hooks" / "notify-wait.sh"), _SID, str(self.home)],
            self._env(flag=False),
        )
        self.assertEqual(banner["-message"], "B2 — summary")

    def test_session_title_cli_prints_menu_title(self):
        out = subprocess.run(
            ["/usr/bin/python3", str(_PLUGIN), "--session-title", str(self.transcript)],
            env=self._env(flag=False), text=True, capture_output=True, timeout=30,
        )
        self.assertEqual((out.returncode, out.stdout), (0, "B2\n"))

    def test_session_title_cli_refuses_non_transcript(self):
        out = subprocess.run(
            ["/usr/bin/python3", str(_PLUGIN), "--session-title", str(self.config)],
            env=self._env(flag=False), text=True, capture_output=True, timeout=30,
        )
        self.assertEqual((out.returncode, out.stdout), (1, ""))



class TestBannerSubtitle(unittest.TestCase):
    """:func:`render.banner_subtitle` — banner line 2, labelled like the menu."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="cab-subtitle-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_plain_checkout(self):
        repo = _make_repo(self.root / "Repo", "main")
        self.assertEqual(plugin.render.banner_subtitle(str(repo)), "Repo — ⎇ main")

    def test_worktree_is_labelled_with_owning_repo(self):
        repo = _make_repo(self.root / "Repo", "main")
        wt = _make_worktree(repo, self.root / "fix-x", "fix/x")
        self.assertEqual(plugin.render.banner_subtitle(str(wt)), "Repo — ⓦ fix/x")

    def test_not_a_repo_is_project_only(self):
        plain = self.root / "notes"
        plain.mkdir()
        self.assertEqual(plugin.render.banner_subtitle(str(plain)), "notes")

    def test_unknown_cwd_is_empty(self):
        self.assertEqual(plugin.render.banner_subtitle(""), "")


if __name__ == "__main__":
    unittest.main()
