"""Subagent sidecar persistence and per-parent rollup.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin


class TestSubagentSidecar(unittest.TestCase):
    """Parser and GC for ``agent-state.subagents.tsv``."""

    def test_parses_valid_rows(self):
        raw = "p1\taaa111\tExplore\tworking\t100\t200\np2\tbbb222\tcode-reviewer\tstopped\t150\t180\n"
        parsed = plugin.sidecars._parse_subagents_sidecar(raw)
        self.assertEqual(set(parsed), {"p1", "p2"})
        self.assertEqual(parsed["p1"][0].agent_id, "aaa111")
        self.assertEqual(parsed["p1"][0].state, "working")
        self.assertEqual(parsed["p1"][0].last_event_ts, 200)

    def test_skips_invalid_state(self):
        raw = "p1\taaa111\tExplore\tlol\t100\t200\n"
        self.assertEqual(plugin.sidecars._parse_subagents_sidecar(raw), {})

    def test_skips_invalid_agent_id(self):
        # spaces / quotes etc. — same threat model as session ids.
        raw = "p1\tbad id\tExplore\tworking\t100\t200\n"
        self.assertEqual(plugin.sidecars._parse_subagents_sidecar(raw), {})

    def test_skips_short_rows(self):
        raw = "p1\taaa111\tExplore\tworking\t100\n"  # only 5 cols
        self.assertEqual(plugin.sidecars._parse_subagents_sidecar(raw), {})

    def test_parses_seven_column_rows_with_first_event_ts(self):
        # 7-column schema written by current hook — ``first_event_ts``
        # populated so the renderer can show "ran Xs" on the sub-row.
        raw = "p1\taaa111\tExplore\tstopped\t150\t180\t120\n"
        parsed = plugin.sidecars._parse_subagents_sidecar(raw)
        self.assertEqual(parsed["p1"][0].first_event_ts, 120)
        self.assertEqual(parsed["p1"][0].last_event_ts, 180)

    def test_six_column_rows_leave_first_event_ts_none(self):
        # Legacy 6-column row from older hook versions — parser must
        # treat ``first_event_ts`` as absent, not zero, so the renderer
        # skips the runtime suffix instead of showing "ran 180s ago".
        raw = "p1\taaa111\tExplore\tstopped\t150\t180\n"
        parsed = plugin.sidecars._parse_subagents_sidecar(raw)
        self.assertIsNone(parsed["p1"][0].first_event_ts)

    def test_seventh_column_garbage_falls_back_to_none(self):
        raw = "p1\taaa111\tExplore\tstopped\t150\t180\tnot-an-int\n"
        parsed = plugin.sidecars._parse_subagents_sidecar(raw)
        self.assertIsNone(parsed["p1"][0].first_event_ts)

    def test_groups_multiple_subagents_per_parent_sorted_by_since(self):
        raw = (
            "p1\taaa111\tExplore\tworking\t300\t300\n"
            "p1\tbbb222\tcode-reviewer\tworking\t100\t150\n"
            "p1\tccc333\tExplore\tstopped\t200\t220\n"
        )
        parsed = plugin.sidecars._parse_subagents_sidecar(raw)
        self.assertEqual(
            [s.agent_id for s in parsed["p1"]],
            ["bbb222", "ccc333", "aaa111"],  # by state_since asc
        )

    def test_stale_keys_orphan_parents(self):
        snapshots = {
            "p-alive": (
                plugin.core.SubagentSnapshot(
                    parent_sid="p-alive", agent_id="aaa", agent_type="x",
                    state="working", state_since=100, last_event_ts=200,
                ),
            ),
            "p-dead": (
                plugin.core.SubagentSnapshot(
                    parent_sid="p-dead", agent_id="bbb", agent_type="x",
                    state="working", state_since=100, last_event_ts=200,
                ),
            ),
        }
        with patch.object(
            plugin.sidecars, "_live_session_ids", return_value={"p-alive"},
        ):
            stale = plugin.sidecars._stale_subagent_keys(snapshots, now=300)
        self.assertEqual(stale, {("p-dead", "bbb")})

    def test_stale_keys_window_expired(self):
        import dataclasses
        snapshots = {
            "p-alive": (
                plugin.core.SubagentSnapshot(
                    parent_sid="p-alive", agent_id="fresh", agent_type="x",
                    state="working", state_since=100, last_event_ts=10_000,
                ),
                plugin.core.SubagentSnapshot(
                    parent_sid="p-alive", agent_id="ancient", agent_type="x",
                    state="stopped", state_since=100, last_event_ts=100,
                ),
            ),
        }
        # now=10_500, window_sec=1000:
        # ``fresh``  last_event=10_000 → 500 s ago → kept.
        # ``ancient`` last_event=100   → 10_400 s ago → dropped.
        new_cfg = dataclasses.replace(plugin.core.CONFIG, window_sec=1000)
        with patch.object(plugin.sidecars, "_live_session_ids",
                          return_value={"p-alive"}), \
             patch.object(plugin.core, "CONFIG", new_cfg):
            stale = plugin.sidecars._stale_subagent_keys(snapshots, now=10_500)
        self.assertEqual(stale, {("p-alive", "ancient")})



class TestSubagentRollup(unittest.TestCase):
    """``build_session`` must keep the parent ACTIVE while a child is alive."""

    def _build(
        self,
        *,
        hook_state: str,
        last_event_kind: str,
        hook_ts: int,
        jsonl_mtime: int,
        subagents: tuple,
        now: int,
        watchdog_sec: int = 90,
    ):
        import dataclasses
        from claude_agents_bar import core as core_mod
        from claude_agents_bar import sidecars as sidecars_mod
        from claude_agents_bar import render as render_mod

        sidecar = {
            "sid-1": core_mod.HookSnapshot(
                state=hook_state,
                last_event_ts=hook_ts,
                last_event_kind=last_event_kind,
                cwd="/cwd",
                state_since=hook_ts,
            ),
        }
        subagents_by_sid = {"sid-1": subagents} if subagents else {}
        new_cfg = dataclasses.replace(core_mod.CONFIG, watchdog_sec=watchdog_sec)
        # Stub out everything that hits the filesystem so the test stays pure.
        with patch.object(core_mod, "CONFIG", new_cfg), \
             patch.object(sidecars_mod, "read_transcript_meta",
                          return_value=core_mod.TranscriptMeta(ai_title="t")), \
             patch.object(sidecars_mod, "last_user_message_preview",
                          return_value=""), \
             patch.object(sidecars_mod, "current_git_branch", return_value=""), \
             patch.object(sidecars_mod, "fallback_git_branch_from_jsonl",
                          return_value=""), \
             patch.object(sidecars_mod, "last_usage_tokens", return_value=None), \
             patch.object(sidecars_mod, "last_tool_use_summary", return_value=""):
            jsonl = self._fake_jsonl(jsonl_mtime)
            return render_mod.build_session(
                jsonl, sidecar, {}, subagents_by_sid, now,
            )

    def _fake_jsonl(self, mtime: int):
        """Build a fake JSONL path whose ``stat().st_mtime`` returns ``mtime``."""
        tmp = tempfile.NamedTemporaryFile(
            suffix=".jsonl", delete=False, dir=tempfile.gettempdir(),
        )
        path = Path(tmp.name)
        tmp.close()
        # Rename to a session-id-looking stem so the validator is happy.
        renamed = path.with_name("sid-1.jsonl")
        path.rename(renamed)
        os.utime(renamed, (mtime, mtime))
        self.addCleanup(lambda: renamed.unlink(missing_ok=True))
        return renamed

    def test_live_subagent_keeps_parent_active_past_watchdog(self):
        # Parent's TSV and JSONL haven't moved in 200s (watchdog_sec=90), but a
        # subagent ticked 5s ago — the row must stay ACTIVE.
        now = 10_000
        snap = plugin.core.SubagentSnapshot(
            parent_sid="sid-1", agent_id="aaa111", agent_type="Explore",
            state="working", state_since=9_000, last_event_ts=9_995,
        )
        session = self._build(
            hook_state="working",
            last_event_kind="PreToolUse",
            hook_ts=9_500,  # 500s old (well past watchdog)
            jsonl_mtime=9_500,
            subagents=(snap,),
            now=now,
            watchdog_sec=90,
        )
        self.assertEqual(session.hook_state, "working")
        self.assertEqual(session.group, plugin.core.RenderGroup.ACTIVE)
        self.assertEqual(session.live_subagent_count, 1)

    def test_idle_parent_with_live_subagent_is_promoted_to_active(self):
        # Parent fired Stop (state=idle, last_event_kind=Stop), but a
        # subagent is still working — the row must show ACTIVE, not FRESH.
        now = 10_000
        snap = plugin.core.SubagentSnapshot(
            parent_sid="sid-1", agent_id="aaa111", agent_type="Explore",
            state="working", state_since=9_500, last_event_ts=9_995,
        )
        session = self._build(
            hook_state="idle",
            last_event_kind="Stop",
            hook_ts=9_700,
            jsonl_mtime=9_700,
            subagents=(snap,),
            now=now,
        )
        self.assertEqual(session.hook_state, "working")
        self.assertEqual(session.group, plugin.core.RenderGroup.ACTIVE)

    def test_stopped_subagent_does_not_keep_parent_active(self):
        # If every subagent already stopped, the parent's own watchdog runs.
        now = 10_000
        snap = plugin.core.SubagentSnapshot(
            parent_sid="sid-1", agent_id="aaa111", agent_type="Explore",
            state="stopped", state_since=9_500, last_event_ts=9_500,
        )
        session = self._build(
            hook_state="working",
            last_event_kind="PreToolUse",
            hook_ts=9_500,
            jsonl_mtime=9_500,
            subagents=(snap,),
            now=now,
            watchdog_sec=90,
        )
        self.assertEqual(session.hook_state, "idle")  # watchdog'd

    def test_subagent_watchdog_demotes_stale_working_rows(self):
        # A subagent whose last hook event was 200s ago and never emitted
        # SubagentStop is treated as stopped (Task crashed), so it doesn't
        # keep the parent ACTIVE forever.
        now = 10_000
        snap = plugin.core.SubagentSnapshot(
            parent_sid="sid-1", agent_id="aaa111", agent_type="Explore",
            state="working", state_since=8_000, last_event_ts=9_800,
        )
        session = self._build(
            hook_state="working",
            last_event_kind="PreToolUse",
            hook_ts=9_500,
            jsonl_mtime=9_500,
            subagents=(snap,),
            now=now,
            watchdog_sec=90,
        )
        # Snapshot was demoted to stopped, parent watchdog ran, hook_state=idle.
        self.assertEqual(session.subagents[0].state, "stopped")
        self.assertEqual(session.live_subagent_count, 0)


if __name__ == "__main__":
    unittest.main()
