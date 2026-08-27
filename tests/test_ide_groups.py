"""IDE session groups (spec 0015): the read-only mirror of the editor sidebar.

Covers the globalState reader (a foreign SQLite database we only ever open
``mode=ro``), the validator that mirrors the extension's own limits, the
``show_ide_groups`` gate, editor precedence, and the row + submenu rendering.

Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from _helpers import plugin, _make_session, isolate_mode_sidecars

_SID_A = "11111111-2222-3333-4444-555555555555"
_SID_B = "66666666-7777-8888-9999-000000000000"


def _write_db(path: Path, blob, *, key=None, table="ItemTable") -> Path:
    """Build a globalState-shaped SQLite file holding ``blob`` as JSON."""
    con = sqlite3.connect(str(path))
    try:
        con.execute(f"CREATE TABLE {table} (key TEXT PRIMARY KEY, value BLOB)")
        con.execute(
            f"INSERT INTO {table} (key, value) VALUES (?, ?)",
            (
                key if key is not None else plugin.core.IDE_GLOBALSTATE_KEY,
                blob if isinstance(blob, str) else json.dumps(blob),
            ),
        )
        con.commit()
    finally:
        con.close()
    return path


def _groups_blob(*groups) -> dict:
    """Wrap group dicts in a ``sessionGroups:<workspace>`` key."""
    return {"sessionGroups:/Users/x/Projects/Repo": list(groups)}


def _group(gid="g1", name="backend", sids=(_SID_A,), **extra) -> dict:
    entry = {"id": gid, "name": name, "collapsed": False, "sessionIds": list(sids)}
    entry.update(extra)
    return entry


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    """Strip ANSI escapes so a row can be compared as plain text."""
    return _ANSI_RE.sub("", text)


def _render_row(session, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        plugin.render._print_session_row(session, **kwargs)
    return buf.getvalue().splitlines()


class _ConfigPatch(unittest.TestCase):
    """Base class swapping ``core.CONFIG`` for the duration of one test."""

    def _config(self, **overrides):
        # The live-mode sidecar wins over the config knob, so neutralise it
        # first — otherwise these tests would follow the developer's own
        # menu state instead of the value under test.
        isolate_mode_sidecars(self)
        original = plugin.core.CONFIG
        plugin.core.CONFIG = replace(plugin.core.CONFIG, **overrides)
        self.addCleanup(lambda: setattr(plugin.core, "CONFIG", original))


class TestParseIdeGroups(unittest.TestCase):
    """The validator mirrors the extension's own (2.1.241) — and never raises."""

    def _parse(self, blob):
        return plugin.sidecars._parse_ide_groups(blob)

    def test_maps_session_id_to_group_name(self):
        out = self._parse(_groups_blob(_group(sids=(_SID_A, _SID_B))))
        self.assertEqual(out, {_SID_A: "backend", _SID_B: "backend"})

    def test_several_workspaces_fold_into_one_map(self):
        blob = {
            "sessionGroups:/a": [_group(gid="g1", name="infra", sids=(_SID_A,))],
            "sessionGroups:/b": [_group(gid="g2", name="docs", sids=(_SID_B,))],
        }
        self.assertEqual(self._parse(blob), {_SID_A: "infra", _SID_B: "docs"})

    def test_ignores_non_group_keys(self):
        blob = {"hiddenSessionIds": [_SID_A], "defaultPermissionMode": "auto"}
        self.assertEqual(self._parse(blob), {})

    def test_remote_prefixed_ids_are_dropped(self):
        # Cloud sessions have no transcript on this machine, so there's
        # nothing in the menu to attach them to.
        out = self._parse(_groups_blob(_group(sids=(f"remote:{_SID_A}", _SID_B))))
        self.assertEqual(out, {_SID_B: "backend"})

    def test_group_name_is_trimmed_and_stripped_of_row_breaking_chars(self):
        out = self._parse(_groups_blob(_group(name="  back|end\nnow  ")))
        # ``|`` starts SwiftBar's parameter list and a newline starts a new
        # menu item — both must be gone before the name reaches a row.
        self.assertEqual(out, {_SID_A: "back end now"})

    def test_empty_or_whitespace_name_drops_the_group(self):
        self.assertEqual(self._parse(_groups_blob(_group(name="   "))), {})
        self.assertEqual(self._parse(_groups_blob(_group(name=""))), {})

    def test_name_truncated_to_extension_limit(self):
        long_name = "x" * (plugin.core.IDE_GROUP_NAME_MAX + 50)
        out = self._parse(_groups_blob(_group(name=long_name)))
        self.assertEqual(len(out[_SID_A]), plugin.core.IDE_GROUP_NAME_MAX)

    def test_duplicate_group_id_is_dropped(self):
        out = self._parse(
            _groups_blob(
                _group(gid="same", name="first", sids=(_SID_A,)),
                _group(gid="same", name="second", sids=(_SID_B,)),
            )
        )
        self.assertEqual(out, {_SID_A: "first"})

    def test_first_group_claiming_a_session_wins(self):
        out = self._parse(
            _groups_blob(
                _group(gid="g1", name="first", sids=(_SID_A,)),
                _group(gid="g2", name="second", sids=(_SID_A,)),
            )
        )
        self.assertEqual(out, {_SID_A: "first"})

    def test_group_count_capped(self):
        groups = [
            _group(gid=f"g{i}", name=f"n{i}", sids=(f"sid{i}",))
            for i in range(plugin.core.IDE_GROUPS_MAX + 25)
        ]
        out = self._parse(_groups_blob(*groups))
        self.assertEqual(len(out), plugin.core.IDE_GROUPS_MAX)

    def test_session_id_total_capped(self):
        many = [f"sid{i}" for i in range(plugin.core.IDE_GROUP_SESSION_IDS_MAX + 40)]
        out = self._parse(_groups_blob(_group(sids=many)))
        self.assertEqual(len(out), plugin.core.IDE_GROUP_SESSION_IDS_MAX)

    def test_oversized_group_id_dropped(self):
        gid = "g" * (plugin.core.IDE_GROUP_ID_MAX + 1)
        self.assertEqual(self._parse(_groups_blob(_group(gid=gid))), {})

    def test_junk_in_every_field_is_survivable(self):
        blob = {
            "sessionGroups:/a": [
                None,
                "not a dict",
                42,
                {"id": 5, "name": "x", "sessionIds": [_SID_A]},
                {"id": "g", "name": None, "sessionIds": [_SID_A]},
                {"id": "g2", "name": "ok", "sessionIds": "not a list"},
                {"id": "g3", "name": "ok", "sessionIds": [None, 7, {}, _SID_B]},
            ],
            "sessionGroups:/b": "not a list",
            "sessionGroups:/c": 17,
        }
        self.assertEqual(self._parse(blob), {_SID_B: "ok"})

    def test_ids_with_shell_metacharacters_are_dropped(self):
        # Same allow-list the rest of the plugin uses for session ids.
        out = self._parse(_groups_blob(_group(sids=("../../etc/passwd", "a b", _SID_A))))
        self.assertEqual(out, {_SID_A: "backend"})


class TestReadGlobalState(_ConfigPatch):
    """Reading the editor's database: fail-soft on every foreign-data path."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_reads_groups_from_a_real_database(self):
        db = _write_db(self.tmp / "state.vscdb", _groups_blob(_group()))
        self._config(ide_groups_mode="submenu", ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {_SID_A: "backend"})

    def test_missing_file_yields_empty(self):
        self._config(ide_state_db_paths=(str(self.tmp / "nope.vscdb"),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})

    def test_corrupt_database_yields_empty(self):
        db = self.tmp / "broken.vscdb"
        db.write_bytes(b"this is not a sqlite file at all")
        self._config(ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})

    def test_alien_schema_yields_empty(self):
        db = _write_db(self.tmp / "alien.vscdb", _groups_blob(_group()), table="Other")
        self._config(ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})

    def test_missing_extension_key_yields_empty(self):
        db = _write_db(self.tmp / "s.vscdb", _groups_blob(_group()), key="some.other")
        self._config(ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})

    def test_non_json_value_yields_empty(self):
        db = _write_db(self.tmp / "s.vscdb", "{not json at all")
        self._config(ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})

    def test_json_array_instead_of_object_yields_empty(self):
        db = _write_db(self.tmp / "s.vscdb", json.dumps([1, 2, 3]))
        self._config(ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})

    def test_path_with_spaces_and_question_mark(self):
        # The real path contains "Application Support"; percent-encoding also
        # has to survive a "?" that would otherwise read as URI syntax.
        weird = self.tmp / "App Support ? dir"
        weird.mkdir()
        db = _write_db(weird / "state.vscdb", _groups_blob(_group()))
        self._config(ide_state_db_paths=(str(db),))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {_SID_A: "backend"})

    def test_off_mode_opens_nothing(self):
        db = _write_db(self.tmp / "state.vscdb", _groups_blob(_group()))
        self._config(ide_groups_mode="off", ide_state_db_paths=(str(db),))
        opened = []
        original = sqlite3.connect

        def _spy(*args, **kwargs):
            opened.append(args[0] if args else None)
            return original(*args, **kwargs)

        plugin.sidecars.sqlite3.connect = _spy
        self.addCleanup(lambda: setattr(plugin.sidecars.sqlite3, "connect", original))
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})
        self.assertEqual(opened, [])

    def test_first_database_claiming_a_session_wins(self):
        first = _write_db(
            self.tmp / "first.vscdb",
            _groups_blob(_group(gid="g1", name="from-first", sids=(_SID_A,))),
        )
        second = _write_db(
            self.tmp / "second.vscdb",
            _groups_blob(
                _group(gid="g2", name="from-second", sids=(_SID_A, _SID_B)),
            ),
        )
        self._config(ide_state_db_paths=(str(first), str(second)))
        self.assertEqual(
            plugin.sidecars.read_ide_groups(),
            {_SID_A: "from-first", _SID_B: "from-second"},
        )


class TestDatabasePathOrder(_ConfigPatch):
    """Autodetection order, and the manual override that replaces it."""

    def test_editor_of_the_click_is_probed_first(self):
        self._config(editor_url_scheme="cursor://", ide_state_db_paths=())
        paths = plugin.sidecars._ide_state_db_paths()
        self.assertIn("Cursor", str(paths[0]))
        self.assertTrue(str(paths[0]).endswith("User/globalStorage/state.vscdb"))

    def test_every_known_editor_is_probed_once(self):
        self._config(editor_url_scheme="vscodium://", ide_state_db_paths=())
        paths = [str(p) for p in plugin.sidecars._ide_state_db_paths()]
        self.assertEqual(len(paths), len(set(paths)))
        expected = set(plugin.core.IDE_SUPPORT_DIR_BY_SCHEME.values()) | set(
            plugin.core.IDE_EXTRA_SUPPORT_DIRS
        )
        for name in expected:
            self.assertTrue(
                any(f"/{name}/User/globalStorage/" in p for p in paths), name
            )

    def test_explicit_paths_replace_autodetection(self):
        self._config(ide_state_db_paths=("~/custom/state.vscdb",))
        paths = plugin.sidecars._ide_state_db_paths()
        self.assertEqual(len(paths), 1)
        self.assertTrue(str(paths[0]).endswith("custom/state.vscdb"))
        self.assertNotIn("~", str(paths[0]))  # expanded


class TestConfigKnobs(unittest.TestCase):
    def test_defaults(self):
        config = plugin.Config()
        self.assertEqual(config.ide_groups_mode, "inline")
        self.assertEqual(config.ide_state_db_paths, ())

    def test_every_mode_is_accepted(self):
        for mode in ("submenu", "inline", "off"):
            self.assertEqual(
                plugin.Config._from_mapping({"ide_groups_mode": mode}).ide_groups_mode,
                mode,
            )

    def test_mode_set_matches_the_accepted_values(self):
        self.assertEqual(plugin.core.IDE_GROUPS_MODES, {"submenu", "inline", "off"})

    def test_unknown_mode_falls_back_to_default(self):
        # The value picks a rendering shape, so an unknown one is refused
        # rather than guessed at.
        self.assertEqual(
            plugin.Config._from_mapping({"ide_groups_mode": "sections"}).ide_groups_mode,
            "inline",
        )

    def test_non_string_mode_falls_back_to_default(self):
        self.assertEqual(
            plugin.Config._from_mapping({"ide_groups_mode": True}).ide_groups_mode,
            "inline",
        )

    def test_paths_list_is_coerced_to_tuple(self):
        config = plugin.Config._from_mapping({"ide_state_db_paths": ["/a", "/b"]})
        self.assertEqual(config.ide_state_db_paths, ("/a", "/b"))

    def test_paths_drop_junk_entries_but_keep_the_rest(self):
        config = plugin.Config._from_mapping(
            {"ide_state_db_paths": ["/a", 7, None, "  ", "/b"]}
        )
        self.assertEqual(config.ide_state_db_paths, ("/a", "/b"))

    def test_paths_non_list_falls_back_to_autodetect(self):
        config = plugin.Config._from_mapping({"ide_state_db_paths": "/a"})
        self.assertEqual(config.ide_state_db_paths, ())


class TestRowRendering(_ConfigPatch):
    """``inline`` mode: ``group · title`` plus the full name in the submenu."""

    def setUp(self):
        self._config(ide_groups_mode="inline")

    def test_ungrouped_row_is_unchanged(self):
        rows = _render_row(_make_session(ide_group=None))
        # An ungrouped row has no group prefix; the middle dots that
        # remain are the ordinary segment separators.
        self.assertTrue(_plain(rows[0]).startswith("⚪ title ·"), rows[0])
        self.assertFalse(any("tray.full" in r for r in rows))

    def test_group_prefixes_the_title(self):
        row = _render_row(_make_session(ide_group="backend"))[0]
        # The dim/reset escape sits between the separator and the title, so
        # compare the row with the escapes stripped.
        self.assertIn("backend · title", _plain(row))

    def test_group_prefix_is_dimmed(self):
        row = _render_row(_make_session(ide_group="backend"))[0]
        self.assertIn(f"{plugin.core._ANSI_STALE}backend", row)
        self.assertIn(plugin.core._ANSI_RESET, row)

    def test_long_group_name_truncated_on_the_row(self):
        name = "a-very-long-group-name-indeed"
        row = _render_row(_make_session(ide_group=name))[0]
        self.assertIn("…", row)
        self.assertNotIn(name, row)

    def test_submenu_carries_the_full_name_with_its_icon(self):
        name = "a-very-long-group-name-indeed"
        rows = _render_row(_make_session(ide_group=name))
        line = next(r for r in rows if "tray.full" in r)
        self.assertIn(name, line)
        self.assertIn("color=#999999", line)

    def test_group_line_sits_directly_under_tags(self):
        rows = _render_row(_make_session(ide_group="backend"))
        tags_idx = next(i for i, r in enumerate(rows) if "Tags" in r or "Тег" in r)
        group_idx = next(i for i, r in enumerate(rows) if "tray.full" in r)
        self.assertGreater(group_idx, tags_idx)
        # Only the tag picker's own colour rows may sit between them.
        between = rows[tags_idx + 1 : group_idx]
        self.assertTrue(all(r.startswith("----") for r in between), between)

    def test_group_survives_the_bookmarks_submenu(self):
        # ``show_state=False`` strips live-state signals; a group is a
        # classification, not a state, so it stays.
        row = _render_row(
            _make_session(ide_group="backend"), show_state=False, bookmark_age="3m"
        )[0]
        self.assertIn("backend · title", _plain(row))


class TestShortenGroup(unittest.TestCase):
    def test_short_name_untouched(self):
        self.assertEqual(plugin.render._shorten_group("infra"), "infra")

    def test_exact_limit_untouched(self):
        name = "x" * plugin.render._IDE_GROUP_ROW_MAX
        self.assertEqual(plugin.render._shorten_group(name), name)

    def test_over_limit_gets_an_ellipsis(self):
        name = "x" * (plugin.render._IDE_GROUP_ROW_MAX + 5)
        out = plugin.render._shorten_group(name)
        self.assertEqual(len(out), plugin.render._IDE_GROUP_ROW_MAX)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()


class TestSubmenuMode(_ConfigPatch):
    """``submenu`` mode: one entry per group, sidebar order, state counters."""

    def setUp(self):
        self._config(ide_groups_mode="submenu")

    def _render(self, sessions):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render.render(sessions)
        return buf.getvalue().splitlines()

    def _session(self, sid, name=None, order=None, **kw):
        # ``name`` is the IDE group; Session.group is the live-state bucket,
        # which callers pass through **kw when a test cares about it.
        return _make_session(id=sid, ide_group=name, ide_group_order=order, **kw)

    def test_groups_become_top_level_entries_with_sessions_inside(self):
        rows = self._render([
            self._session("a", "Backend", 0, title="first"),
            self._session("b", "Backend", 0, title="second"),
        ])
        header = next(r for r in rows if r.split(" | ")[0].endswith("Backend"))
        # A params block is mandatory: SwiftBar only expands an item into a
        # submenu when it parses one.
        self.assertIn(" | ", header)
        # Both sessions are nested one level under the header.
        nested = [r for r in rows if r.startswith("--") and "open-session.sh" in r]
        self.assertEqual(len(nested), 2)

    def test_header_carries_an_action_so_it_survives_a_live_rebuild(self):
        # The header's label changes whenever a member switches state, so
        # SwiftBar rebuilds it inside the already-open menu. An item with no
        # action lands there unvalidated by AppKit and goes dead — no
        # highlight, no submenu. The no-op action is what keeps it enabled;
        # it never runs, since a click on a parent goes to its submenu.
        rows = self._render([self._session("a", "Backend", 0)])
        header = next(r for r in rows if r.split(" | ")[0].endswith("Backend"))
        self.assertIn("shell=", header)
        self.assertIn("refresh=false", header)

    def test_header_carries_per_state_counters(self):
        rows = self._render([
            self._session("a", "Backend", 0, group=plugin.RenderGroup.ACTIVE),
            self._session("b", "Backend", 0, group=plugin.RenderGroup.ACTIVE),
            self._session("c", "Backend", 0, group=plugin.RenderGroup.FRESH),
        ])
        header = next(r for r in rows if r.split(" | ")[0].endswith("Backend"))
        self.assertIn(f"{plugin.RenderGroup.ACTIVE.icon}2", header)
        # A count of one is left off — the circle alone says "one of these".
        self.assertIn(plugin.RenderGroup.FRESH.icon, header)
        self.assertNotIn(f"{plugin.RenderGroup.FRESH.icon}1", header)
        # Counters lead, name last — the state is what the eye needs first.
        self.assertTrue(header.startswith(plugin.RenderGroup.ACTIVE.icon), header)

    def test_all_singles_render_as_bare_circles(self):
        rows = self._render([
            self._session("a", "Backend", 0, group=plugin.RenderGroup.ACTIVE),
            self._session("b", "Backend", 0, group=plugin.RenderGroup.FRESH),
            self._session("c", "Backend", 0, group=plugin.RenderGroup.ACKNOWLEDGED),
        ])
        header = next(r for r in rows if r.split(" | ")[0].endswith("Backend"))
        label = header.split(" | ")[0]
        expected = " ".join(
            g.icon
            for g in (
                plugin.RenderGroup.ACTIVE,
                plugin.RenderGroup.FRESH,
                plugin.RenderGroup.ACKNOWLEDGED,
            )
        )
        self.assertEqual(label, f"{expected} · Backend")
        self.assertNotIn("1", label)

    def test_groups_follow_the_sidebar_order_not_the_alphabet(self):
        rows = self._render([
            self._session("a", "Zulu", 0),
            self._session("b", "Alpha", 1),
        ])
        headers = [
            r.split(" | ")[0].rsplit(" · ", 1)[-1]
            for r in rows
            if r.split(" | ")[0].endswith(("Zulu", "Alpha"))
        ]
        self.assertEqual(headers, ["Zulu", "Alpha"])

    def test_ungrouped_sessions_follow_after_a_separator(self):
        # No label: SwiftBar can't render a non-selectable item, and a
        # clickable-looking "Ungrouped" row is worse than a plain gap.
        rows = self._render([
            self._session("a", "Backend", 0),
            self._session("b", None, None, title="loose"),
        ])
        header_idx = next(
            i for i, r in enumerate(rows) if r.split(" | ")[0].endswith("Backend")
        )
        loose_idx = next(i for i, r in enumerate(rows) if "loose" in r)
        self.assertGreater(loose_idx, header_idx)
        self.assertIn("---", rows[header_idx + 1 : loose_idx])
        # The loose session stays a top-level row, not nested in a group.
        self.assertFalse(rows[loose_idx].startswith("--"))

    def test_no_separator_when_nothing_is_grouped(self):
        rows = self._render([self._session("a", None, None)])
        # Falls straight through to the flat list — no leading separator.
        body = rows[rows.index("---") + 1 :]
        self.assertFalse(body[0].startswith("---"), body[:2])

    def test_row_does_not_repeat_the_group_name(self):
        rows = self._render([self._session("a", "Backend", 0, title="only")])
        row = next(r for r in rows if "only" in r and "open-session.sh" in r)
        self.assertNotIn("Backend ·", row)

    def test_off_mode_renders_the_flat_list(self):
        self._config(ide_groups_mode="off")
        rows = self._render([self._session("a", "Backend", 0, title="only")])
        self.assertFalse(any(r.split(" | ")[0].endswith("Backend") for r in rows))
        row = next(r for r in rows if "only" in r)
        self.assertNotIn("Backend", row)


class TestGroupingToggle(unittest.TestCase):
    """Tools → Grouping: the sidecar that overrides the config knob."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "agent-state.ide-groups.mode"
        original_path = plugin.core.IDE_GROUPS_MODE_PATH
        plugin.core.IDE_GROUPS_MODE_PATH = self.path
        self.addCleanup(
            lambda: setattr(plugin.core, "IDE_GROUPS_MODE_PATH", original_path)
        )
        original_config = plugin.core.CONFIG
        plugin.core.CONFIG = replace(plugin.core.CONFIG, ide_groups_mode="inline")
        self.addCleanup(lambda: setattr(plugin.core, "CONFIG", original_config))

    def test_absent_sidecar_falls_back_to_config(self):
        self.assertEqual(plugin.core.ide_groups_mode(), "inline")
        plugin.core.CONFIG = replace(plugin.core.CONFIG, ide_groups_mode="submenu")
        self.assertEqual(plugin.core.ide_groups_mode(), "submenu")

    def test_sidecar_overrides_config(self):
        self.assertEqual(plugin.core.write_ide_groups_mode("off"), 0)
        self.assertEqual(plugin.core.ide_groups_mode(), "off")

    def test_every_mode_round_trips(self):
        for mode in ("submenu", "inline", "off"):
            self.assertEqual(plugin.core.write_ide_groups_mode(mode), 0)
            self.assertEqual(plugin.core.ide_groups_mode(), mode)

    def test_unknown_mode_is_refused_not_written(self):
        # A garbage sidecar would silently fall back to config on every tick,
        # which reads as "the menu item does nothing".
        self.assertEqual(plugin.core.write_ide_groups_mode("sections"), 1)
        self.assertFalse(self.path.exists())

    def test_garbage_sidecar_falls_back_to_config(self):
        self.path.write_text("nonsense\n", encoding="utf-8")
        self.assertEqual(plugin.core.ide_groups_mode(), "inline")

    def test_sidecar_gates_the_database_lookup(self):
        self.assertEqual(plugin.core.write_ide_groups_mode("off"), 0)
        db = _write_db(Path(self._tmp.name) / "state.vscdb", _groups_blob(_group()))
        plugin.core.CONFIG = replace(
            plugin.core.CONFIG, ide_state_db_paths=(str(db),)
        )
        self.assertEqual(plugin.sidecars.read_ide_groups(), {})


class TestGroupingMenuItem(unittest.TestCase):
    """The Tools entry itself: three options, the live one checked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "mode"
        original = plugin.core.IDE_GROUPS_MODE_PATH
        plugin.core.IDE_GROUPS_MODE_PATH = path
        self.addCleanup(lambda: setattr(plugin.core, "IDE_GROUPS_MODE_PATH", original))
        self.path = path

    def _rows(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_ide_groups_block(
                plugin.core.PLUGIN_DIR / "bin" / "app"
            )
        return buf.getvalue().splitlines()

    def test_lists_all_three_modes(self):
        rows = self._rows()
        self.assertEqual(len(rows), 4)  # header + three options
        for mode in ("submenu", "inline", "off"):
            self.assertTrue(
                any(f"param2={mode} " in r for r in rows), mode
            )

    def test_live_mode_is_checked_and_others_are_not(self):
        plugin.core.write_ide_groups_mode("inline")
        rows = self._rows()
        checked = [r for r in rows if "checked=true" in r]
        self.assertEqual(len(checked), 1)
        self.assertIn("param2=inline ", checked[0])

    def test_options_run_the_action_script_via_bash(self):
        # /bin/bash, so a lost executable bit can't kill the item (1.1.1).
        for row in self._rows()[1:]:
            self.assertIn("shell=/bin/bash", row)
            self.assertIn("ide-groups-set.sh", row)
            self.assertIn("refresh=true", row)

    def test_action_script_exists_and_is_executable(self):
        script = plugin.core.PLUGIN_DIR / "bin" / "app" / "ide-groups-set.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & 0o111)


class TestGroupingMenuNesting(unittest.TestCase):
    """The Tools entry is a real submenu, not an indented flat list."""

    def _rows(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_ide_groups_block(
                plugin.core.PLUGIN_DIR / "bin" / "app"
            )
        return buf.getvalue().splitlines()

    def test_header_sits_one_level_under_tools(self):
        header = self._rows()[0]
        self.assertTrue(header.startswith("--"))
        self.assertFalse(header.startswith("---"))

    def test_options_are_children_of_the_header(self):
        for row in self._rows()[1:]:
            self.assertTrue(row.startswith("----"), row)
            # No leading spaces left over from the old flat layout.
            self.assertFalse(row.startswith("----  "), row)
