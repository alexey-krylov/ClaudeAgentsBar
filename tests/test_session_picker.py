"""The ``Session ▸`` submenu — Reveal in Finder, Copy ID, Delete….

All three act on the session as an object rather than on its state, so they
live one level down instead of flat on the row (where *Reveal in Finder* and
*Delete…* used to sit). Covers the three items and their order, the nesting
under a Bookmarks entry, and the id-safety guard in ``copy-session-id.sh``.

Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import unittest

from _helpers import plugin, _make_session

_BIN_APP = plugin.core.PLUGIN_DIR / "bin" / "app"
_COPY_SCRIPT = _BIN_APP / "copy-session-id.sh"


def _picker(session, indent=""):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        plugin.render._print_session_picker(session, indent, _BIN_APP)
    return buf.getvalue().splitlines()


class TestSessionPickerRender(unittest.TestCase):

    def test_parent_plus_three_items_in_order(self):
        lines = _picker(_make_session())
        self.assertIn(plugin.render._t("menu.session"), lines[0])
        items = [l for l in lines if l.startswith("----")]
        self.assertEqual(len(items), 3)
        # Reveal → Copy ID → Delete…, destructive one last.
        self.assertIn("reveal-session.sh", items[0])
        self.assertIn("copy-session-id.sh", items[1])
        self.assertIn("delete-session.sh", items[2])

    def test_reveal_is_nested_not_flat(self):
        lines = _picker(_make_session(id="abc"))
        reveal = [l for l in lines if "reveal-session.sh" in l]
        self.assertEqual(len(reveal), 1)
        # The whole point of the change: it's one level down now.
        self.assertTrue(reveal[0].startswith("----"))
        self.assertIn('param1="abc"', reveal[0])

    def test_copy_id_runs_via_bin_bash_with_id_as_param2(self):
        lines = _picker(_make_session(id="abc"))
        copy = [l for l in lines if "copy-session-id.sh" in l]
        self.assertEqual(len(copy), 1)
        # Routed through /bin/bash so a lost executable bit can't kill the
        # item — the 1.1.1 multi-workspace failure mode.
        self.assertIn("shell=/bin/bash", copy[0])
        self.assertIn('param2="abc"', copy[0])
        self.assertIn('tooltip="abc"', copy[0])
        # Nothing on disk changed, so a refresh would be pure churn.
        self.assertIn("refresh=false", copy[0])

    def test_indent_nests_items_deeper(self):
        # Under a Bookmarks entry (row at --) the parent lands at ---- and
        # its items at ------.
        lines = _picker(_make_session(), indent="--")
        self.assertTrue(lines[0].startswith("----"))
        self.assertFalse(lines[0].startswith("------"))
        items = [l for l in lines if l.startswith("------")]
        self.assertEqual(len(items), 3)

    def test_row_no_longer_carries_flat_reveal_or_delete(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            plugin.render._print_session_row(_make_session())
        for line in buf.getvalue().splitlines():
            for script in ("reveal-session.sh", "delete-session.sh"):
                if script in line:
                    self.assertTrue(line.startswith("----"), line)

    def test_delete_still_refreshes_the_menu(self):
        # Unlike its read-only neighbours, the row it belongs to disappears.
        delete = [l for l in _picker(_make_session()) if "delete-session.sh" in l]
        self.assertIn("refresh=true", delete[0])


class TestCopySessionIdScript(unittest.TestCase):
    """The id-safety guard. We don't exercise pbcopy — it needs a live
    pasteboard, which is not something a test run should stomp on."""

    def _run(self, *args):
        return subprocess.run(
            ["/bin/bash", str(_COPY_SCRIPT), *args],
            capture_output=True, text=True, timeout=10,
        )

    def test_rejects_empty_id(self):
        self.assertEqual(self._run("").returncode, 1)

    def test_rejects_id_outside_the_safe_alphabet(self):
        for bad in ("a b", "../etc", "a;rm -rf /", "a$(id)", "a\ttab"):
            self.assertEqual(self._run(bad).returncode, 1, bad)

    def test_rejects_overlong_id(self):
        self.assertEqual(self._run("a" * 65).returncode, 1)

    def test_script_is_executable(self):
        # It's invoked via /bin/bash so this isn't load-bearing, but every
        # other bin/app/*.sh carries the bit and packaging checks assume it.
        self.assertTrue(_COPY_SCRIPT.stat().st_mode & 0o111)


if __name__ == "__main__":
    unittest.main()
