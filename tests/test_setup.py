"""Additive jq-style merge of hook registrations into settings.json.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin


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
            "Notification", "Stop", "SubagentStop",
        ):
            with self.subTest(event=event):
                ours = self._agent_state_matchers(result["hooks"][event])
                self.assertEqual(len(ours), 1)

    def test_subagent_stop_registers_stopped_argument(self):
        # New in v1.1: SubagentStop is the only event that calls the hook
        # with the ``stopped`` argument. Pin it so a future refactor
        # doesn't silently drop the matcher.
        result = self._merge({})
        ours = self._agent_state_matchers(result["hooks"]["SubagentStop"])
        self.assertEqual(len(ours), 1)
        cmd = ours[0]["hooks"][0]["command"]
        self.assertIn("agent-state.sh stopped", cmd)

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
