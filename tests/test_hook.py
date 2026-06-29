"""The ``agent-state.sh`` Bash hook: state writes and subagent routing.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from _helpers import plugin


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



class TestAgentStateHookSubagentRouting(unittest.TestCase):
    """The hook splits events by ``agent_id``: subagent-side ones must land
    in ``agent-state.subagents.tsv``, parent-side ones in
    ``agent-state.tsv``. See ``docs/specs/0004-subagent-grouping.md``.
    """

    HOOK = Path(__file__).resolve().parent.parent / "hooks" / "agent-state.sh"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / ".claude").mkdir()
        self.parent_tsv = self.home / ".claude" / "agent-state.tsv"
        self.subagent_tsv = self.home / ".claude" / "agent-state.subagents.tsv"

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

    def test_parent_payload_writes_parent_tsv_only(self):
        self._run("working", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "PreToolUse",
        })
        self.assertTrue(self.parent_tsv.exists())
        # Subagent sidecar shouldn't be touched at all for parent-side events.
        self.assertFalse(self.subagent_tsv.exists())

    def test_subagent_payload_writes_subagent_tsv_only(self):
        self._run("working", {
            "session_id": "sid-1", "cwd": "/y",
            "hook_event_name": "PreToolUse",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        self.assertFalse(self.parent_tsv.exists())
        self.assertTrue(self.subagent_tsv.exists())
        cols = self.subagent_tsv.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(cols[0], "sid-1")
        self.assertEqual(cols[1], "aaa111")
        self.assertEqual(cols[2], "Explore")
        self.assertEqual(cols[3], "working")

    def test_subagent_events_do_not_clobber_parent_row(self):
        # Original bug from issues/no-green.md: subagent events were
        # overwriting the parent row's last_event_kind / cwd because they
        # shared the parent's session_id.
        self._run("working", {
            "session_id": "sid-1", "cwd": "/parent-cwd",
            "hook_event_name": "UserPromptSubmit",
        })
        self._run("working", {
            "session_id": "sid-1", "cwd": "/subagent-cwd",
            "hook_event_name": "PreToolUse",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        parent_row = self.parent_tsv.read_text(encoding="utf-8").strip().split("\t")
        # cwd and kind stay pinned to the parent's UserPromptSubmit.
        self.assertEqual(parent_row[3], "UserPromptSubmit")
        self.assertEqual(parent_row[4], "/parent-cwd")

    def test_subagent_stop_writes_state_stopped(self):
        # SubagentStop registers ``stopped`` arg.
        self._run("working", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "PreToolUse",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        self._run("stopped", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "SubagentStop",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        cols = self.subagent_tsv.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(cols[3], "stopped")

    def test_two_subagents_get_separate_rows(self):
        for agent_id, agent_type in [("aaa111", "Explore"), ("bbb222", "code-reviewer")]:
            self._run("working", {
                "session_id": "sid-1", "cwd": "/x",
                "hook_event_name": "PreToolUse",
                "agent_id": agent_id, "agent_type": agent_type,
            })
        rows = self.subagent_tsv.read_text(encoding="utf-8").splitlines()
        agent_ids = {r.split("\t")[1] for r in rows}
        self.assertEqual(agent_ids, {"aaa111", "bbb222"})

    def test_subagent_first_event_ts_is_pinned_across_writes(self):
        # ``first_event_ts`` (col 7) is set on the row's first sighting
        # and must never advance on subsequent events — that's how the
        # plugin computes total runtime for stopped subagents.
        self._run("working", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "PreToolUse",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        first_cols = self.subagent_tsv.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(len(first_cols), 7)
        first_ts = first_cols[6]
        self.assertTrue(first_ts.isdigit())

        # Force a one-second gap so the second event's ``ts`` is strictly
        # later than the first; otherwise the test can't tell whether
        # ``first_event_ts`` was reused or coincidentally re-stamped.
        import time as _time
        _time.sleep(1.1)
        self._run("stopped", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "SubagentStop",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        second_cols = self.subagent_tsv.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(len(second_cols), 7)
        self.assertEqual(second_cols[6], first_ts)  # pinned
        self.assertGreater(int(second_cols[5]), int(first_ts))  # last_event_ts advanced

    def test_subagent_first_event_ts_backfilled_from_legacy_six_col_row(self):
        # Pre-existing 6-column row (written by an older hook) must keep
        # producing valid 7-column output once a new event lands —
        # ``first_event_ts`` backfilled from ``state_since`` so we get a
        # plausible runtime instead of dropping the column.
        legacy = "sid-1\taaa111\tExplore\tworking\t100\t100\n"
        self.subagent_tsv.write_text(legacy, encoding="utf-8")
        self._run("stopped", {
            "session_id": "sid-1", "cwd": "/x",
            "hook_event_name": "SubagentStop",
            "agent_id": "aaa111", "agent_type": "Explore",
        })
        cols = self.subagent_tsv.read_text(encoding="utf-8").strip().split("\t")
        self.assertEqual(len(cols), 7)
        self.assertEqual(cols[6], "100")  # backfilled from legacy state_since

    def test_stopped_arg_without_agent_id_is_dropped(self):
        # ``stopped`` only makes sense for subagent-side events; a payload
        # without agent_id must be a no-op so the parent TSV doesn't grow
        # a state outside :data:`core.HOOK_STATES`.
        self.assertEqual(
            self._run("stopped", {
                "session_id": "sid-1", "cwd": "/x",
                "hook_event_name": "Stop",
            }, check=False),
            0,
        )
        self.assertFalse(self.parent_tsv.exists())
        self.assertFalse(self.subagent_tsv.exists())

    def test_idle_arg_with_agent_id_is_dropped(self):
        # Symmetric: ``idle`` is parent-side only. A misrouted Stop with
        # agent_id must not pollute the subagent TSV with an unparseable state.
        self.assertEqual(
            self._run("idle", {
                "session_id": "sid-1", "cwd": "/x",
                "hook_event_name": "Stop",
                "agent_id": "aaa111", "agent_type": "Explore",
            }, check=False),
            0,
        )
        self.assertFalse(self.parent_tsv.exists())
        self.assertFalse(self.subagent_tsv.exists())


if __name__ == "__main__":
    unittest.main()
