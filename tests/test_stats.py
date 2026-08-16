"""Pure helpers for the ``Tools → Stats today`` summary.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from _helpers import plugin


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
                "model_counts": {},
                "subagents": 0, "subagent_model_counts": {},
            })
        # Tokens line should show the "no usage data yet" variant — not
        # a confusing "0 (prompt 0, cache-hit 0%)" sentence.
        self.assertIn("Tokens", body)
        self.assertNotIn("Top projects", body)
        # No model sessions ever parsed → the Models / Subagents blocks
        # stay out of the dialog rather than showing empty headers.
        self.assertNotIn("Models:", body)
        self.assertNotIn("Subagents today", body)

    def test_format_stats_dialog_with_projects(self):
        with patch.object(plugin.core, "_lang", return_value="en"):
            body = plugin._format_stats_dialog({
                "sessions": 5, "turns": 100,
                "total_tokens": 1_000_000, "prompt_tokens": 200_000,
                "cache_read_tokens": 500_000,
                "top_projects": [("FooRepo", 60), ("BarRepo", 40)],
                "model_counts": {
                    "claude-sonnet-4-6": 2,
                    "claude-opus-4-7": 3,
                },
                "subagents": 0, "subagent_model_counts": {},
            })
        self.assertIn("Sessions today: 5", body)
        self.assertIn("Turns: 100", body)
        self.assertIn("1.0M", body)
        # 500K / 1M → 50 % cache-hit
        self.assertIn("50%", body)
        self.assertIn("FooRepo", body)
        self.assertIn("BarRepo", body)
        # Full model id appears verbatim, alongside its family badge.
        self.assertIn("ⓞ claude-opus-4-7: 3", body)
        self.assertIn("ⓢ claude-sonnet-4-6: 2", body)
        # Family ordering: opus before sonnet, regardless of insertion order.
        self.assertLess(body.index("opus-4-7"), body.index("sonnet-4-6"))
        # No subagents this run → no subagents block.
        self.assertNotIn("Subagents today", body)

    def test_format_stats_dialog_with_non_claude_model(self):
        with patch.object(plugin.core, "_lang", return_value="en"):
            body = plugin._format_stats_dialog({
                "sessions": 2, "turns": 4,
                "total_tokens": 0, "prompt_tokens": 0, "cache_read_tokens": 0,
                "top_projects": [],
                "model_counts": {"openrouter/gpt-4": 2},
                "subagents": 0, "subagent_model_counts": {},
            })
        # Non-Claude provider strings keep their verbatim id and fall
        # back to the ⓜ glyph — same rule as the per-row model badge.
        self.assertIn("ⓜ openrouter/gpt-4: 2", body)

    def test_format_stats_dialog_with_subagents(self):
        with patch.object(plugin.core, "_lang", return_value="en"):
            body = plugin._format_stats_dialog({
                "sessions": 1, "turns": 5,
                "total_tokens": 0, "prompt_tokens": 0, "cache_read_tokens": 0,
                "top_projects": [],
                "model_counts": {"claude-opus-4-7": 1},
                "subagents": 4,
                "subagent_model_counts": {
                    "claude-haiku-4-5-20251001": 3,
                    "claude-opus-4-7": 1,
                },
            })
        self.assertIn("Subagents today: 4", body)
        self.assertIn("ⓗ claude-haiku-4-5-20251001: 3", body)
        # In the subagent block, opus still ranks first by family order,
        # even though haiku has more runs.
        self.assertLess(
            body.index("opus-4-7: 1"), body.index("haiku-4-5-20251001: 3"),
        )

    def test_model_sort_key_orders_claude_families(self):
        # Stable sort gives opus → sonnet → haiku → fable → non-Claude.
        models = [
            "claude-haiku-4-5",
            "openrouter/gpt-4",
            "claude-fable-5",
            "claude-opus-4-7",
            "claude-sonnet-4-6",
        ]
        self.assertEqual(
            sorted(models, key=plugin._model_sort_key),
            [
                "claude-opus-4-7",
                "claude-sonnet-4-6",
                "claude-haiku-4-5",
                "claude-fable-5",
                "openrouter/gpt-4",
            ],
        )

    def test_every_badged_family_has_a_sort_rank(self):
        # The two tables are read together in the Models block; a family with
        # a badge but no rank renders its glyph and then sorts into the
        # non-Claude tail, which is how Fable read before 1.4.2.
        badged = {prefix for prefix, _ in plugin.core._MODEL_FAMILY_BADGES}
        self.assertEqual(badged, set(plugin.actions._MODEL_FAMILY_RANK))

    def test_fable_ranks_ahead_of_non_claude_providers(self):
        with patch.object(plugin.core, "_lang", return_value="en"):
            body = plugin._format_stats_dialog({
                "sessions": 2, "turns": 9,
                "total_tokens": 0, "prompt_tokens": 0, "cache_read_tokens": 0,
                "top_projects": [],
                "model_counts": {
                    "openrouter/gpt-4": 5,
                    "claude-fable-5": 1,
                },
                "subagents": 0,
                "subagent_model_counts": {},
            })
        self.assertIn("ⓕ claude-fable-5: 1", body)
        # Fable is a Claude family, so it sorts above the ⓜ tail despite the
        # lower count.
        self.assertLess(body.index("claude-fable-5"), body.index("openrouter/gpt-4"))


if __name__ == "__main__":
    unittest.main()
