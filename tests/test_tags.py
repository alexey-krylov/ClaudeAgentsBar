"""Session tags (spec 0013): a Finder-style colored flag on a session.

Covers the palette integrity, the `sid\\tcolor` sidecar reader/GC, the flag +
bookmark-glyph rendering on the row, and the ``Tags ▸`` picker.

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


class TestTagPalette(unittest.TestCase):
    """The palette is the single source of truth for colors, order, keys."""

    def test_seven_finder_colors_in_order(self):
        keys = [k for k, _, _ in plugin.core.TAG_PALETTE]
        self.assertEqual(
            keys, ["red", "orange", "yellow", "green", "blue", "purple", "white"]
        )

    def test_keys_and_glyph_derived_from_palette(self):
        self.assertEqual(plugin.core.TAG_KEYS, {k for k, _, _ in plugin.core.TAG_PALETTE})
        # ANSI-colored circled letters, not sfcolor — SwiftBar won't tint SF
        # Symbols in the dropdown, so the color lives in an ANSI escape and the
        # letter mirrors the color name.
        self.assertIn("ⓡ", plugin.core.TAG_GLYPH["red"])
        self.assertIn("ⓑ", plugin.core.TAG_GLYPH["blue"])
        self.assertIn("\x1b[38;5;", plugin.core.TAG_GLYPH["red"])  # 256-color escape
        self.assertTrue(plugin.core.TAG_GLYPH["red"].endswith(plugin.core._ANSI_RESET))
        self.assertEqual(set(plugin.core.TAG_GLYPH), plugin.core.TAG_KEYS)


class TestRowTagAndBookmarkGlyph(unittest.TestCase):
    """The main row shows the tag color (inline ANSI circled letter) and, for a
    pinned session, a leading ``bookmark.fill`` icon — the two coexist."""

    def _main(self, **overrides):
        return _render_row(_make_session(**overrides))[0]

    def test_untagged_row_has_no_tag_glyph(self):
        row = self._main(tag=None)
        self.assertNotIn("ⓑ", row)
        self.assertNotIn("ⓡ", row)

    def test_tagged_row_carries_colored_letter(self):
        red = self._main(tag="red")
        self.assertIn("ⓡ", red)
        self.assertIn("\x1b[38;5;196m", red)  # red is colored via ANSI
        self.assertIn("ⓑ", self._main(tag="blue"))

    def test_row_never_shows_bookmark_icon(self):
        # The bookmark isn't marked on the row — an sfimage would have to lead,
        # ahead of the state circle, and an inline emoji was rejected. Pinned
        # sessions are surfaced under the *Bookmarks* submenu instead.
        self.assertNotIn("sfimage=bookmark.fill", self._main(is_bookmarked=True))
        self.assertNotIn("sfimage=bookmark.fill", self._main(is_bookmarked=False))

    def test_tagged_row_shows_tag_letter(self):
        row = self._main(is_bookmarked=True, tag="blue")
        self.assertIn("ⓑ", row)  # tag color letter still shows
        self.assertNotIn("sfimage=bookmark.fill", row)

    def test_show_state_false_hides_state_circle_and_color(self):
        s = _make_session(hook_state="working", group=plugin.RenderGroup.ACTIVE)
        icon = s.group.icon
        live = _render_row(s)[0]
        self.assertIn(icon, live)          # live list shows the state circle
        self.assertIn("color=", live)      # and the state row colour
        pinned = _render_row(s, show_state=False)[0]
        self.assertNotIn(icon, pinned)     # Bookmarks: no state circle
        self.assertNotIn("color=", pinned)  # and no state colour


class TestTagsPicker(unittest.TestCase):
    """``Tags ▸`` emits a parent + seven color options; the current color is
    checked and toggles off, the others set."""

    def _picker(self, session, indent=""):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_tags_picker(
                session, indent, plugin.core.PLUGIN_DIR / "bin" / "app"
            )
        return buf.getvalue().splitlines()

    def test_parent_and_seven_options(self):
        lines = self._picker(_make_session(tag=None))
        self.assertIn(plugin.render._t("menu.tags"), lines[0])
        options = [l for l in lines if l.startswith("----")]
        self.assertEqual(len(options), 7)

    def test_untagged_parent_has_no_current_glyph(self):
        lines = self._picker(_make_session(tag=None))
        # No color square on the parent when untagged; no option checked.
        self.assertFalse(any(g in lines[0] for g in plugin.core.TAG_GLYPH.values()))
        self.assertFalse(any("checked=true" in l for l in lines))

    def test_options_carry_their_colored_letter_on_ansi_line(self):
        lines = self._picker(_make_session(tag=None))
        opts = [l for l in lines if l.startswith("----")]
        for glyph in plugin.core.TAG_GLYPH.values():
            self.assertTrue(any(glyph in l for l in opts))
        # ANSI must be enabled on every option or the colored letter is dead.
        self.assertTrue(all("ansi=true" in l for l in opts))

    def test_current_color_checked_and_clears(self):
        lines = self._picker(_make_session(tag="green"))
        # Parent is a clean header — no current-color glyph on it.
        self.assertNotIn(plugin.core.TAG_GLYPH["green"], lines[0])
        current = [l for l in lines if "checked=true" in l]
        self.assertEqual(len(current), 1)
        self.assertIn("param3=clear", current[0])  # toggle-off
        self.assertIn(plugin.core.TAG_GLYPH["green"], current[0])
        # Every other option sets its own key.
        others = [l for l in lines if l.startswith("----") and "checked=false" in l]
        self.assertEqual(len(others), 6)
        self.assertTrue(any("param3=red" in l for l in others))

    def test_indent_nests_options_deeper(self):
        # Under a Bookmarks entry (row at --) the picker parent lands at ----
        # and its options at ------.
        lines = self._picker(_make_session(tag=None), indent="--")
        self.assertTrue(lines[0].startswith("----"))
        self.assertFalse(lines[0].startswith("------"))
        opts = [l for l in lines if "param3=" in l]
        self.assertTrue(all(l.startswith("------") for l in opts))


class TestTagsSidecar(unittest.TestCase):
    """Reader / GC on ``agent-state.tags`` + the ``collect_sessions`` marking
    and orphan prune."""

    def setUp(self):
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        sidecar = self._tmpdir / "state.tsv"
        tags = self._tmpdir / "tags.tsv"
        clicks = self._tmpdir / "clicks.tsv"
        forget = self._tmpdir / "forget.tsv"
        bookmarks = self._tmpdir / "bookmarks.tsv"
        subagents = self._tmpdir / "subagents.tsv"
        dismiss = self._tmpdir / "dismiss"
        self._orig = {
            "PROJECTS_DIR": plugin.PROJECTS_DIR,
            "SIDECAR_PATH": plugin.SIDECAR_PATH,
            "TAGS_PATH": plugin.core.TAGS_PATH,
            "CLICKS_PATH": plugin.CLICKS_PATH,
            "FORGET_PATH": plugin.FORGET_PATH,
            "BOOKMARKS_PATH": plugin.core.BOOKMARKS_PATH,
            "SUBAGENTS_SIDECAR_PATH": plugin.core.SUBAGENTS_SIDECAR_PATH,
            "DISMISS_PATH": plugin.DISMISS_PATH,
            "_TAGS_LOCK_DIR": plugin.core._TAGS_LOCK_DIR,
            "_SIDECAR_LOCK_DIR": plugin._SIDECAR_LOCK_DIR,
        }
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = sidecar
        plugin.core.TAGS_PATH = tags
        plugin.core.CLICKS_PATH = clicks
        plugin.core.FORGET_PATH = forget
        plugin.core.BOOKMARKS_PATH = bookmarks
        plugin.core.SUBAGENTS_SIDECAR_PATH = subagents
        plugin.core.DISMISS_PATH = dismiss
        plugin.core._TAGS_LOCK_DIR = tags.with_suffix(tags.suffix + ".lock.d")
        plugin.core._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.tags = tags
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

    def test_missing_file_returns_empty(self):
        self.assertEqual(plugin.sidecars.read_tags(), {})

    def test_round_trip(self):
        self.tags.write_text("a\tred\nb\tblue\n", encoding="utf-8")
        self.assertEqual(plugin.sidecars.read_tags(), {"a": "red", "b": "blue"})

    def test_drops_invalid_color_and_short_rows(self):
        self.tags.write_text(
            "a\tred\nb\tmauve\nc\nd\tblue\n", encoding="utf-8"
        )
        self.assertEqual(plugin.sidecars.read_tags(), {"a": "red", "d": "blue"})

    def test_gc_drops_only_named_ids(self):
        self.tags.write_text("a\tred\nb\tblue\nc\tgreen\n", encoding="utf-8")
        plugin.sidecars.gc_tags({"b"})
        self.assertEqual(set(plugin.sidecars.read_tags()), {"a", "c"})

    def test_collect_marks_tag_and_prunes_orphan(self):
        self._make_transcript("live", self.now - 30, ("idle", self.now - 30))
        self.tags.write_text(f"live\torange\nghost\tblue\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        by_id = {s.id: s for s in sessions}
        self.assertEqual(by_id["live"].tag, "orange")
        # ghost has no transcript → pruned from the sidecar.
        self.assertEqual(plugin.sidecars.read_tags(), {"live": "orange"})


if __name__ == "__main__":
    unittest.main()
