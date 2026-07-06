"""Bookmarks (spec 0012): pin sessions so they survive the render window.

Covers the sidecar reader/GC, the ``transcript_for`` resolver, the indent
threading + Bookmark checkbox in ``_print_session_row``, and the
``_print_bookmarks_block`` reuse/rebuild/skip logic.

Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from _helpers import plugin, _make_session


def _render_row(session, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        plugin.render._print_session_row(session, **kwargs)
    return buf.getvalue().splitlines()


def _bookmark_item(lines):
    """The single Bookmark checkbox line among a row's submenu lines."""
    hits = [l for l in lines if "sfimage=bookmark.fill" in l and "checked=" in l]
    assert len(hits) == 1, hits
    return hits[0]


class TestRowIndentAndCheckbox(unittest.TestCase):
    """``_print_session_row`` gains an ``indent`` (for nesting under Bookmarks)
    and a Bookmark checkbox whose state mirrors ``session.is_bookmarked``."""

    def test_default_indent_keeps_top_level_row(self):
        lines = _render_row(_make_session())
        # Main row carries no submenu dashes; its submenu items start at ``--``.
        self.assertFalse(lines[0].startswith("-"))
        item = _bookmark_item(lines)
        self.assertTrue(item.startswith("--"))
        self.assertFalse(item.startswith("---"))

    def test_indent_shifts_row_one_level_deeper(self):
        lines = _render_row(_make_session(), indent="--")
        # Row sits at ``--``; its submenu items shift to ``----``.
        self.assertTrue(lines[0].startswith("--"))
        self.assertFalse(lines[0].startswith("---"))
        item = _bookmark_item(lines)
        self.assertTrue(item.startswith("----"))
        self.assertFalse(item.startswith("------"))

    def test_unpinned_checkbox_offers_to_add(self):
        item = _bookmark_item(_render_row(_make_session(is_bookmarked=False)))
        self.assertIn("checked=false", item)
        self.assertIn("param3=on", item)

    def test_pinned_checkbox_offers_to_remove(self):
        item = _bookmark_item(_render_row(_make_session(is_bookmarked=True)))
        self.assertIn("checked=true", item)
        self.assertIn("param3=off", item)

    def test_bookmark_age_rides_the_row_not_a_submenu_leaf(self):
        # 1.4.1: the pin age is the row's right label, not a "clock" leaf,
        # and never carries the literal word "Added"/"Добавлено".
        plain = _render_row(_make_session())
        self.assertFalse(any("sfimage=clock" in l for l in plain))
        rows = _render_row(
            _make_session(), indent="--", show_state=False, bookmark_age="5m"
        )
        # No clock leaf anywhere in the submenu.
        self.assertFalse(any("sfimage=clock" in l for l in rows))
        # No "Added …" wording leaked anywhere.
        self.assertFalse(any("Added" in l or "Добавлено" in l for l in rows))
        # The age rides the row's right label.
        row_label = rows[0].split(" | ")[0]
        self.assertTrue(row_label.rstrip().endswith("5m"))

    def test_bookmark_row_shows_pin_age_not_wait_duration(self):
        # A waiting session: live list shows the ❓ marker + blocked duration;
        # under Bookmarks that's replaced by the bare pin age, ❓ dropped.
        waiting = _make_session(hook_state="waiting")
        live_label = _render_row(waiting)[0].split(" | ")[0]
        self.assertIn("❓", live_label)  # sanity: live row does mark waiting
        pinned_label = _render_row(
            waiting, indent="--", show_state=False, bookmark_age="5m"
        )[0].split(" | ")[0]
        self.assertIn("5m", pinned_label)
        self.assertTrue(pinned_label.rstrip().endswith("5m"))
        self.assertNotIn("❓", pinned_label)
        self.assertNotEqual(live_label, pinned_label)

    def test_bookmark_item_sits_directly_under_forget(self):
        # 1.4.1 submenu order: Remind … Forget → Bookmark → Delete.
        lines = _render_row(_make_session())

        def only_index(needle):
            hits = [i for i, l in enumerate(lines) if needle in l]
            self.assertEqual(len(hits), 1, (needle, hits))
            return hits[0]

        remind_i = only_index("sfimage=speaker.wave.2.fill")
        forget_i = only_index("sfimage=eraser.fill")
        bookmark_i = only_index("sfimage=bookmark.fill")
        delete_i = only_index("sfimage=trash.fill")
        self.assertLess(remind_i, forget_i)
        self.assertLess(forget_i, bookmark_i)  # Bookmark directly under Forget
        self.assertLess(bookmark_i, delete_i)


class TestBookmarkAge(unittest.TestCase):
    """The "Added … ago" age stays fine-grained under a day and rolls into
    whole days beyond, so an old pin never reads as ``192h``."""

    def _age(self, seconds):
        return plugin.render._humanize_bookmark_age(seconds, "en")

    def test_sub_hour_is_minutes(self):
        self.assertEqual(self._age(5 * 60), "5m")

    def test_sub_day_is_hours(self):
        self.assertEqual(self._age(3 * 3600), "3h")

    def test_a_day_or_more_rolls_to_days(self):
        self.assertEqual(self._age(8 * 86400), "8d")

    def test_days_round_half_up(self):
        # 1 day 12h → 2d (half up), 1 day 11h → 1d.
        self.assertEqual(self._age(86400 + 12 * 3600), "2d")
        self.assertEqual(self._age(86400 + 11 * 3600), "1d")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(self._age(-10), self._age(0))


class TestBookmarksSidecar(unittest.TestCase):
    """Reader / GC / resolver on ``agent-state.bookmarks`` + the orphan prune
    and ``is_bookmarked`` marking that ``collect_sessions`` performs."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        sidecar = self._tmpdir / "state.tsv"
        clicks = self._tmpdir / "clicks.tsv"
        forget = self._tmpdir / "forget.tsv"
        bookmarks = self._tmpdir / "bookmarks.tsv"
        subagents = self._tmpdir / "subagents.tsv"
        dismiss = self._tmpdir / "dismiss"
        self._orig = {
            "PROJECTS_DIR": plugin.PROJECTS_DIR,
            "SIDECAR_PATH": plugin.SIDECAR_PATH,
            "CLICKS_PATH": plugin.CLICKS_PATH,
            "FORGET_PATH": plugin.FORGET_PATH,
            "BOOKMARKS_PATH": plugin.core.BOOKMARKS_PATH,
            "SUBAGENTS_SIDECAR_PATH": plugin.core.SUBAGENTS_SIDECAR_PATH,
            "DISMISS_PATH": plugin.DISMISS_PATH,
            "_SIDECAR_LOCK_DIR": plugin._SIDECAR_LOCK_DIR,
            "_CLICKS_LOCK_DIR": plugin._CLICKS_LOCK_DIR,
            "_FORGET_LOCK_DIR": plugin._FORGET_LOCK_DIR,
            "_BOOKMARKS_LOCK_DIR": plugin.core._BOOKMARKS_LOCK_DIR,
        }
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = sidecar
        plugin.core.CLICKS_PATH = clicks
        plugin.core.FORGET_PATH = forget
        plugin.core.BOOKMARKS_PATH = bookmarks
        plugin.core.SUBAGENTS_SIDECAR_PATH = subagents
        plugin.core.DISMISS_PATH = dismiss
        plugin.core._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        plugin.core._CLICKS_LOCK_DIR = clicks.with_suffix(clicks.suffix + ".lock.d")
        plugin.core._FORGET_LOCK_DIR = forget.with_suffix(forget.suffix + ".lock.d")
        plugin.core._BOOKMARKS_LOCK_DIR = bookmarks.with_suffix(bookmarks.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.bookmarks = bookmarks
        self.now = 1_700_000_000

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(plugin.core, name, val)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_transcript(self, sid, mtime, sidecar_row=None):
        project_dir = self.projects / f"-fake-{sid}"
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        jsonl.write_bytes(b"")
        os.utime(jsonl, (mtime, mtime))
        if sidecar_row is not None:
            state, ts = sidecar_row
            existing = self.sidecar.read_text() if self.sidecar.exists() else ""
            self.sidecar.write_text(
                existing + f"{sid}\t{state}\t{ts}\tStop\t/tmp\n", encoding="utf-8"
            )
        return jsonl

    # --- reader -----------------------------------------------------------

    def test_missing_file_returns_empty(self):
        self.assertEqual(plugin.sidecars.read_bookmarks(), {})

    def test_round_trip(self):
        self.bookmarks.write_text(f"a\t{self.now}\nb\t{self.now - 5}\n", encoding="utf-8")
        self.assertEqual(
            plugin.sidecars.read_bookmarks(), {"a": self.now, "b": self.now - 5}
        )

    def test_unparseable_rows_skipped(self):
        self.bookmarks.write_text(
            f"no-tab-line\na\tnot-an-int\nb\t{self.now}\n", encoding="utf-8"
        )
        self.assertEqual(plugin.sidecars.read_bookmarks(), {"b": self.now})

    # --- gc ---------------------------------------------------------------

    def test_gc_drops_only_named_ids(self):
        self.bookmarks.write_text(
            f"a\t{self.now}\nb\t{self.now}\nc\t{self.now}\n", encoding="utf-8"
        )
        plugin.sidecars.gc_bookmarks({"b"})
        self.assertEqual(set(plugin.sidecars.read_bookmarks()), {"a", "c"})

    # --- transcript resolver ---------------------------------------------

    def test_transcript_for_finds_existing(self):
        jsonl = self._make_transcript("live", self.now)
        self.assertEqual(plugin.sidecars.transcript_for("live"), jsonl)

    def test_transcript_for_missing_is_none(self):
        self.assertIsNone(plugin.sidecars.transcript_for("ghost"))

    def test_transcript_for_rejects_invalid_id(self):
        self.assertIsNone(plugin.sidecars.transcript_for("../etc/passwd"))

    # --- collect_sessions integration ------------------------------------

    def test_orphan_bookmark_is_gc_d(self):
        # A pin whose transcript is gone must be pruned on the next pass.
        self.bookmarks.write_text(f"gone\t{self.now}\n", encoding="utf-8")
        plugin.collect_sessions(self.now)
        self.assertEqual(plugin.sidecars.read_bookmarks(), {})

    def test_live_bookmark_survives_and_is_marked(self):
        self._make_transcript("live", self.now - 30, ("idle", self.now - 30))
        self.bookmarks.write_text(f"live\t{self.now - 100}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        marked = {s.id: s.is_bookmarked for s in sessions}
        self.assertTrue(marked.get("live"))
        # And the bookmark row was NOT pruned (its transcript exists).
        self.assertIn("live", plugin.sidecars.read_bookmarks())

    def test_unbookmarked_session_not_marked(self):
        self._make_transcript("plain", self.now - 30, ("idle", self.now - 30))
        sessions = plugin.collect_sessions(self.now)
        self.assertFalse(any(s.is_bookmarked for s in sessions))


class TestBookmarksBlock(unittest.TestCase):
    """``_print_bookmarks_block`` — hidden when empty; reuses live sessions;
    rebuilds out-of-window pins from the transcript; skips orphans."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        self._orig = {
            "PROJECTS_DIR": plugin.PROJECTS_DIR,
            "SIDECAR_PATH": plugin.SIDECAR_PATH,
            "CLICKS_PATH": plugin.CLICKS_PATH,
            "BOOKMARKS_PATH": plugin.core.BOOKMARKS_PATH,
            "SUBAGENTS_SIDECAR_PATH": plugin.core.SUBAGENTS_SIDECAR_PATH,
        }
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = self._tmpdir / "state.tsv"
        plugin.core.CLICKS_PATH = self._tmpdir / "clicks.tsv"
        plugin.core.BOOKMARKS_PATH = self._tmpdir / "bookmarks.tsv"
        plugin.core.SUBAGENTS_SIDECAR_PATH = self._tmpdir / "subagents.tsv"
        self.projects = projects
        self.bookmarks = plugin.core.BOOKMARKS_PATH
        self.now = 1_700_000_000

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(plugin.core, name, val)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_transcript(self, sid, mtime):
        project_dir = self.projects / f"-fake-{sid}"
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        jsonl.write_bytes(b"")
        os.utime(jsonl, (mtime, mtime))
        return jsonl

    def _render(self, live_sessions):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_bookmarks_block(live_sessions)
        return buf.getvalue()

    def test_empty_bookmarks_render_nothing(self):
        self.assertEqual(self._render([]), "")

    def test_header_and_reused_live_session(self):
        # A bookmarked session already in the live list is reused (no transcript
        # needed) and rendered under the Bookmarks header, one level deep.
        self.bookmarks.write_text(f"live\t{self.now - 60}\n", encoding="utf-8")
        s = _make_session(id="live", title="Live One", is_bookmarked=True)
        out = self._render([s])
        self.assertIn(plugin.render._t("menu.bookmarks"), out)
        self.assertIn("Live One", out)
        # Reused → row nested at ``--`` (its submenu at ``----``).
        self.assertTrue(any(l.startswith("--") and "Live One" in l
                            for l in out.splitlines()))

    def test_out_of_window_bookmark_is_rebuilt(self):
        # Not in live_sessions, but its transcript exists → rebuilt via
        # build_session and shown.
        self._make_transcript("cold", self.now - 10 * 3600)
        self.bookmarks.write_text(f"cold\t{self.now - 100}\n", encoding="utf-8")
        out = self._render([])
        self.assertIn(plugin.render._t("menu.bookmarks"), out)
        self.assertTrue(any("cold" in l for l in out.splitlines()))

    def test_orphan_bookmark_is_skipped(self):
        # No transcript and not live → nothing to render, header suppressed.
        self.bookmarks.write_text(f"ghost\t{self.now}\n", encoding="utf-8")
        self.assertEqual(self._render([]), "")


if __name__ == "__main__":
    unittest.main()
