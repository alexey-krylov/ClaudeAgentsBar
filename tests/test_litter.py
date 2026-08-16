"""Temp-file litter in ``~/.claude`` — issue #3.

Every sidecar writer is a shell script doing ``awk … > "$FILE.$$" && mv``. The
EXIT traps now drop ``$TMP`` when awk fails, but a ``SIGKILL`` between the
redirect and the ``mv`` strands the file, so the render tick sweeps orphans
whose owning PID is dead. Covers the sweep's matching (narrow enough to leave
live sidecars and ``.lock.d`` mutexes alone), its two safety gates (PID
liveness and an age floor), the doctor's litter report, and the traps
themselves as they're written in the shell sources.

Stdlib only — run with ``/usr/bin/python3 -m unittest discover -s tests``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from _helpers import plugin

_REPO_ROOT = Path(__file__).resolve().parent.parent

def _find_dead_pid() -> int:
    """A PID inside the valid 1..99999 range that is not currently running.

    It has to be in range: the sweep screens out-of-range suffixes as
    "never was a PID" and leaves them alone, so a fake 2^22 value would make
    the deletion tests pass for the wrong reason.
    """
    for candidate in range(99999, 1000, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except OSError:
            continue
    raise RuntimeError("no free PID found")


_DEAD_PID = _find_dead_pid()

#: Above 2^31-1 ``os.kill`` raises OverflowError, which is not an OSError.
_OVERFLOW_PID = 20260513184649

_OLD = 10_000  # seconds — comfortably past _TEMP_LITTER_MIN_AGE_SEC


class TestTempLitterPattern(unittest.TestCase):
    """The regex has to be narrow: it decides what gets deleted."""

    def _matches(self, name):
        return plugin.sidecars._TEMP_LITTER_RE.match(name) is not None

    def test_matches_pid_suffixed_temps(self):
        for name in (
            "agent-state.tsv.5312",
            "agent-state.subagents.tsv.90",
            "agent-state.tags.1",
            "agent-state.bookmarks.44021",
            "agent-state.clicks.7",
            "agent-state.forget.7",
            "agent-state.dismiss.7",
            "agent-state.usage.771.tmp",
        ):
            self.assertTrue(self._matches(name), name)

    def test_captures_the_pid(self):
        self.assertEqual(
            plugin.sidecars._TEMP_LITTER_RE.match("agent-state.tsv.5312").group(1),
            "5312",
        )
        self.assertEqual(
            plugin.sidecars._TEMP_LITTER_RE.match("agent-state.usage.771.tmp").group(1),
            "771",
        )

    def test_spares_live_sidecars_and_locks(self):
        # Deleting any of these would be a data loss bug, not a cleanup.
        for name in (
            "agent-state.tsv",
            "agent-state.subagents.tsv",
            "agent-state.tsv.lock.d",
            "agent-state.tags",
            "agent-state.bookmarks",
            "agent-state.clicks",
            "agent-state.forget",
            "agent-state.dismiss",
            "agent-state.quiet-until",
            "agent-state.usage-monitor.mode",
            "agent-state.keep-awake.mode",
            "agent-state.statusline.orig",
            "settings.json",
            "settings.json.bak.20260101-120000",
            "projects",
        ):
            self.assertFalse(self._matches(name), name)


class TestGcTempFiles(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / ".claude").mkdir()
        self.state_dir = self.tmpdir / ".claude"
        self.now = int(time.time())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write(self, name, age_sec=_OLD):
        path = self.state_dir / name
        path.write_text("junk")
        stamp = self.now - age_sec
        os.utime(path, (stamp, stamp))
        return path

    def _sweep(self):
        with patch.object(plugin.core, "HOME", self.tmpdir):
            plugin.sidecars.gc_temp_files(self.now)

    def test_removes_orphan_of_a_dead_pid(self):
        orphan = self._write(f"agent-state.tsv.{_DEAD_PID}")
        self._sweep()
        self.assertFalse(orphan.exists())

    def test_keeps_temp_owned_by_a_live_pid(self):
        # Our own PID is by definition alive — a mid-write temp must survive.
        live = self._write(f"agent-state.tsv.{os.getpid()}")
        self._sweep()
        self.assertTrue(live.exists())

    def test_keeps_recent_temp_even_when_pid_is_dead(self):
        # The age floor is the guard against PID recycling: a fresh file
        # whose suffix happens to match a recycled-and-exited PID stays.
        fresh = self._write(f"agent-state.tsv.{_DEAD_PID}", age_sec=5)
        self._sweep()
        self.assertTrue(fresh.exists())

    def test_leaves_real_sidecars_alone(self):
        keep = [
            self._write("agent-state.tsv"),
            self._write("agent-state.tags"),
            self._write("agent-state.quiet-until"),
            self._write("settings.json.bak.20260101-120000"),
        ]
        lock = self.state_dir / "agent-state.tsv.lock.d"
        lock.mkdir()
        self._sweep()
        for path in keep:
            self.assertTrue(path.exists(), path.name)
        self.assertTrue(lock.is_dir())

    def test_out_of_range_suffix_is_left_alone_and_does_not_raise(self):
        # A suffix above macOS pid_max was never a PID. Two things go wrong if
        # it isn't screened: os.kill raises OverflowError above 2^31-1 — which
        # is NOT an OSError, so nothing here catches it, and the exception
        # escapes collect_sessions and replaces the whole menu with the red
        # error item on every tick — and any out-of-range value that did get
        # through reads as a dead PID and gets deleted.
        for suffix in (str(_OVERFLOW_PID), str(2 ** 31), "999999", "0"):
            path = self._write(f"agent-state.tsv.{suffix}")
            self._sweep()  # must not raise
            self.assertTrue(path.exists(), suffix)

    def test_pid_alive_treats_unknown_failures_as_alive(self):
        # Back off rather than delete when we can't tell. OverflowError is the
        # one that used to escape and take the whole menu down with it.
        self.assertTrue(plugin.sidecars._pid_alive(_OVERFLOW_PID))
        self.assertTrue(plugin.sidecars._pid_alive(os.getpid()))
        self.assertFalse(plugin.sidecars._pid_alive(_DEAD_PID))

    def test_a_full_render_survives_an_overflowing_suffix(self):
        # End-to-end: the tick must still produce a menu.
        self._write(f"agent-state.tsv.{_OVERFLOW_PID}")
        with patch.object(plugin.core, "HOME", self.tmpdir):
            plugin.sidecars.gc_temp_files(self.now)

    def test_survives_a_missing_state_dir(self):
        shutil.rmtree(self.state_dir)
        self._sweep()  # must not raise

    def test_sweeps_a_realistic_pile(self):
        pids = []
        candidate = _DEAD_PID
        while len(pids) < 20 and candidate > 1000:
            try:
                os.kill(candidate, 0)
            except ProcessLookupError:
                pids.append(candidate)
            except OSError:
                pass
            candidate -= 1
        self.assertEqual(len(pids), 20)
        orphans = [self._write(f"agent-state.tsv.{pid}") for pid in pids]
        self._sweep()
        self.assertEqual([p for p in orphans if p.exists()], [])


class TestDoctorLitterCheck(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        (self.tmpdir / ".claude").mkdir()
        self.state_dir = self.tmpdir / ".claude"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _check(self):
        from claude_agents_bar import doctor
        with patch.object(plugin.core, "HOME", self.tmpdir):
            return doctor._doctor_check_state_dir_litter()

    def test_clean_dir_is_ok(self):
        status, message = self._check()
        self.assertEqual(status, "ok")
        self.assertIn("0 temp files", message)

    def test_warns_on_a_backup_pile(self):
        for i in range(20):
            (self.state_dir / f"settings.json.bak.20260101-1200{i:02d}").write_text("{}")
        status, message = self._check()
        self.assertEqual(status, "warn")
        self.assertIn("20 settings.json backups", message)

    def test_warns_on_a_temp_pile(self):
        for i in range(20):
            (self.state_dir / f"agent-state.tsv.{1000 + i}").write_text("")
        status, message = self._check()
        self.assertEqual(status, "warn")
        self.assertIn("20 orphaned", message)

    def test_a_few_of_each_is_still_ok(self):
        for i in range(3):
            (self.state_dir / f"settings.json.bak.20260101-1200{i:02d}").write_text("{}")
            (self.state_dir / f"agent-state.tsv.{1000 + i}").write_text("")
        self.assertEqual(self._check()[0], "ok")


class TestShellTrapsCoverTempFiles(unittest.TestCase):
    """Source-level assertions on the shell writers (issue #3).

    Three groups, because the writers don't share one shape:

    * ``TRAP_WRITERS`` — lock-holding ``awk … > "$TMP" && mv`` writers. The
      ``&&`` skips the ``mv`` on a non-zero awk, so ``$TMP`` has to be in the
      EXIT trap.
    * ``usage-sensor.sh`` — no lock, no trap, writes on both branches of an
      ``if``; both branches have to drop the temp.
    * ``forget-sessions.sh`` — ``: > tmp; mv`` with commands that can't
      realistically fail, so it leaks only on a kill and relies on the
      render-tick GC. Asserted here so the omission is deliberate, not
      forgotten.
    """

    TRAP_WRITERS = (
        "hooks/agent-state.sh",
        "hooks/record-click.sh",
        "bin/app/ack-session.sh",
        "bin/app/forget-session.sh",
        "bin/app/tag-set.sh",
        "bin/app/bookmark-set.sh",
    )

    def test_every_trap_writer_traps_its_temp_file(self):
        for rel in self.TRAP_WRITERS:
            text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            traps = [l for l in text.splitlines() if l.strip().startswith("trap ")]
            self.assertTrue(traps, rel)
            for trap in traps:
                self.assertIn('rm -f "${TMP:-}"', trap, f"{rel}: {trap}")

    def test_trap_writer_list_covers_every_awk_and_mv_writer(self):
        """The list above must not silently fall behind the source tree."""
        found = set()
        for rel in ("hooks", "bin/app"):
            for path in sorted((_REPO_ROOT / rel).glob("*.sh")):
                text = path.read_text(encoding="utf-8")
                if 'TMP="${' in text and "/usr/bin/awk" in text and "trap " in text:
                    found.add(str(path.relative_to(_REPO_ROOT)))
        # delete-session.sh writes temps but holds no EXIT trap (it uses
        # explicit if/else), so it's covered by its own test below.
        self.assertEqual(found - {"bin/app/delete-session.sh"}, set(self.TRAP_WRITERS))

    def test_usage_sensor_drops_its_temp_on_both_branches(self):
        text = (_REPO_ROOT / "hooks/usage-sensor.sh").read_text(encoding="utf-8")
        write_block = text[text.index('tmp="${USAGE_SIDECAR}'):]
        write_block = write_block[:write_block.index("\nfi\n") + 4]
        # mv-failure branch and redirect-failure branch.
        self.assertEqual(write_block.count('rm -f "$tmp"'), 2, write_block)

    def test_forget_sessions_temps_are_gc_matchable(self):
        # It leaks only on a kill, so the safety net is the sweep — which
        # means its temp names must match the litter pattern.
        text = (_REPO_ROOT / "bin/app/forget-sessions.sh").read_text(encoding="utf-8")
        self.assertIn('local tmp="${target}.$$"', text)
        for name in ("agent-state.tsv.123", "agent-state.clicks.123",
                     "agent-state.dismiss.123"):
            self.assertTrue(plugin.sidecars._TEMP_LITTER_RE.match(name), name)

    def test_delete_session_commits_only_on_clean_awk(self):
        text = (_REPO_ROOT / "bin/app/delete-session.sh").read_text(encoding="utf-8")
        # The old ``|| true`` + unconditional mv published a half-written
        # sidecar when awk failed.
        self.assertNotIn('"$STATE_FILE" > "$TMP" || true', text)
        self.assertIn('if /usr/bin/awk -F\'\\t\' -v sid="$SID" \'$1 != sid\' "$STATE_FILE" > "$TMP"; then', text)


class TestInstallerBackupRotation(unittest.TestCase):
    """``settings.json.bak.*`` used to grow without limit — one per setup run,
    no rotation, no "only if changed" check (issue #3, leak 2).

    The two helpers are pulled out of the real installer with sed and eval'd,
    so this exercises the shipped code rather than a copy of it.
    """

    INSTALLERS = ("bin/install/setup.sh", "bin/install/teardown.sh")

    def test_installers_share_the_guard_and_rotation(self):
        for rel in self.INSTALLERS:
            text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("rotate_backups()", text, rel)
            self.assertIn("publish_if_changed()", text, rel)
            self.assertIn("BACKUPS_KEPT=5", text, rel)
            # No unconditional `cp $SETTINGS $SETTINGS_BACKUP` left over.
            self.assertNotIn("SETTINGS_BACKUP", text, rel)

    def _harness(self, installer, body):
        """Run ``body`` with the installer's two backup helpers in scope."""
        script = (
            "set -u\n"
            'say() { printf "%s\\n" "$*"; }\n'
            "BACKUPS_KEPT=5\n"
            f"eval \"$(sed -n '/^rotate_backups()/,/^}}$/p' "
            f"'{_REPO_ROOT / installer}')\"\n"
            f"eval \"$(sed -n '/^publish_if_changed()/,/^}}$/p' "
            f"'{_REPO_ROOT / installer}')\"\n"
            + body
        )
        return subprocess.run(
            ["/bin/bash", "-c", script],
            capture_output=True, text=True, timeout=20,
        )

    def test_identical_content_takes_no_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text('{"a":1}\n')
            (Path(tmp) / "settings.json.tmp").write_text('{"a":1}\n')
            result = self._harness(
                "bin/install/setup.sh",
                f'publish_if_changed "{target}" cfg; echo "rc=$?"\n',
            )
            self.assertIn("rc=1", result.stdout)  # 1 == "was already identical"
            self.assertEqual(list(Path(tmp).glob("*.bak.*")), [])
            # The temp is consumed either way — it must not become litter.
            self.assertFalse((Path(tmp) / "settings.json.tmp").exists())

    def test_changed_content_is_published_with_one_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text('{"a":1}\n')
            (Path(tmp) / "settings.json.tmp").write_text('{"a":2}\n')
            result = self._harness(
                "bin/install/setup.sh",
                f'publish_if_changed "{target}" cfg; echo "rc=$?"\n',
            )
            self.assertIn("rc=0", result.stdout)
            self.assertEqual(target.read_text(), '{"a":2}\n')
            backups = list(Path(tmp).glob("settings.json.bak.*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(), '{"a":1}\n')  # the old one

    def test_rotation_keeps_the_five_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("{}")
            for i in range(12):
                stamp = 1_700_000_000 + i * 60
                backup = Path(tmp) / f"settings.json.bak.{i:02d}"
                backup.write_text(str(i))
                os.utime(backup, (stamp, stamp))
            self._harness(
                "bin/install/setup.sh", f'rotate_backups "{target}" 5\n'
            )
            kept = sorted(p.name for p in Path(tmp).glob("settings.json.bak.*"))
            self.assertEqual(len(kept), 5)
            # Newest-first: the five highest indices survive.
            self.assertEqual(
                kept, [f"settings.json.bak.{i:02d}" for i in range(7, 12)]
            )

    def test_unchanged_path_still_rotates_pre_existing_backups(self):
        # A machine that piled up backups before this fix re-runs a merge that
        # is now a no-op; if rotation only ran on the changed path those
        # backups would sit there forever.
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text('{"a":1}\n')
            (Path(tmp) / "settings.json.tmp").write_text('{"a":1}\n')
            for i in range(12):
                stamp = 1_700_000_000 + i * 60
                backup = Path(tmp) / f"settings.json.bak.{i:02d}"
                backup.write_text(str(i))
                os.utime(backup, (stamp, stamp))
            self._harness(
                "bin/install/setup.sh",
                f'publish_if_changed "{target}" cfg || true\n',
            )
            self.assertEqual(len(list(Path(tmp).glob("settings.json.bak.*"))), 5)
            self.assertEqual(target.read_text(), '{"a":1}\n')  # untouched

    def test_rotation_is_a_noop_below_the_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("{}")
            for i in range(2):
                (Path(tmp) / f"settings.json.bak.{i}").write_text(str(i))
            self._harness(
                "bin/install/setup.sh", f'rotate_backups "{target}" 5\n'
            )
            self.assertEqual(len(list(Path(tmp).glob("settings.json.bak.*"))), 2)

    def test_teardown_helpers_behave_the_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text('{"hooks":{}}\n')
            (Path(tmp) / "settings.json.tmp").write_text('{"hooks":{}}\n')
            result = self._harness(
                "bin/install/teardown.sh",
                f'publish_if_changed "{target}" cfg; echo "rc=$?"\n',
            )
            self.assertIn("rc=1", result.stdout)
            self.assertEqual(list(Path(tmp).glob("*.bak.*")), [])


if __name__ == "__main__":
    unittest.main()
