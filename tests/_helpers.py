"""Shared bootstrap and fixtures for the ClaudeAgentsBar test suite.

Pins the repo root on ``sys.path`` and re-exports the package as ``plugin`` so
every ``test_*.py`` module can ``from _helpers import plugin``. ``_make_session``
builds a ``Session`` with sensible defaults; tests override the field they care
about. Most patches target ``plugin.core`` / ``plugin.<submodule>`` rather than
``plugin`` itself — module-level globals are read via the defining submodule.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import claude_agents_bar as plugin  # noqa: E402

# ``read_transcript_meta`` ends in ``cached_ai_title``, which *writes* a
# sidecar whenever a transcript has neither an ``ai-title`` nor a
# ``custom-title``. Plenty of tests feed it synthetic transcripts, so without
# this the suite would scribble rows like ``fresh1`` into the developer's real
# ``~/.claude/agent-state.ai-titles.tsv`` — and read them back on the next run.
# Redirect once, for the whole process; ``isolate_ai_title_cache`` narrows it
# further for tests that assert on the file itself.
_AI_TITLES_TMP = tempfile.TemporaryDirectory(prefix="cab-test-ai-titles-")
plugin.core.AI_TITLES_PATH = Path(_AI_TITLES_TMP.name) / "agent-state.ai-titles.tsv"
plugin.core._AI_TITLES_LOCK_DIR = plugin.core.AI_TITLES_PATH.with_suffix(
    plugin.core.AI_TITLES_PATH.suffix + ".lock.d"
)


def _make_session(**overrides):
    """Build a ``Session`` with sensible defaults; tests override the field they care about."""
    defaults = dict(
        id="sid",
        hook_state="idle",
        group=plugin.RenderGroup.STALE,
        last_event_ts=0,
        age_sec=0,
        title="title",
        project="project",
        git_branch="",
        cwd="",
        entrypoint="",
    )
    defaults.update(overrides)
    return plugin.Session(**defaults)


def isolate_mode_sidecars(testcase):
    """Point the live-mode sidecars at an empty temp dir for one test.

    ``core.ide_groups_mode()`` folds the ``agent-state.ide-groups.mode``
    sidecar over the config knob, so a test that sets the knob would
    otherwise be overridden by whatever mode the developer's own menu is in
    — making the suite pass or fail depending on the machine. Redirect the
    path to a file that doesn't exist and the reader falls back to config,
    which is what these tests are about.
    """
    import tempfile
    from pathlib import Path

    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    original = plugin.core.IDE_GROUPS_MODE_PATH
    plugin.core.IDE_GROUPS_MODE_PATH = Path(tmp.name) / "absent.mode"
    testcase.addCleanup(
        lambda: setattr(plugin.core, "IDE_GROUPS_MODE_PATH", original)
    )


def isolate_ai_title_cache(testcase):
    """Point the ai-title cache sidecar at a temp dir for one test.

    ``read_transcript_meta`` falls through to ``cached_ai_title`` whenever a
    transcript yields neither an ``ai-title`` nor a ``custom-title``, and that
    path *writes*. Without this redirect the suite would scribble on the
    developer's own ``~/.claude/agent-state.ai-titles.tsv`` and — worse — read
    back rows from it, so a test's outcome would depend on which sessions the
    machine happens to have.

    Also clears the ``lru_cache`` on the reader at both ends: it is keyed on
    nothing, so a value loaded under the real path would otherwise leak into
    the test and back out again.
    """
    import tempfile
    from pathlib import Path

    from claude_agents_bar import sidecars as sidecars_mod

    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    path = Path(tmp.name) / "agent-state.ai-titles.tsv"
    original_path = plugin.core.AI_TITLES_PATH
    original_lock = plugin.core._AI_TITLES_LOCK_DIR
    plugin.core.AI_TITLES_PATH = path
    plugin.core._AI_TITLES_LOCK_DIR = path.with_suffix(path.suffix + ".lock.d")
    sidecars_mod._read_ai_titles.cache_clear()

    def _restore():
        plugin.core.AI_TITLES_PATH = original_path
        plugin.core._AI_TITLES_LOCK_DIR = original_lock
        sidecars_mod._read_ai_titles.cache_clear()

    testcase.addCleanup(_restore)
    return path


def isolate_state_dir(testcase):
    """Point **every** ``~/.claude`` path in ``core`` at a fresh temp dir.

    The path constants are built from ``core.HOME`` at import time, so
    redirecting ``HOME`` afterwards achieves nothing — each one has to be
    remapped by name. This walks ``core``'s module globals and rebinds every
    ``Path`` that lives under ``~/.claude`` (plus ``~/.claude.json`` and
    ``PROJECTS_DIR``), preserving the basename so lock dirs still sit beside
    the files they guard.

    Use it in any test that reaches ``collect_sessions`` / ``ack_fresh`` or
    anything else that fans out across the sidecars: those call paths touch a
    dozen files, and redirecting only the two a test cares about leaves the
    rest pointed at the developer's real state — which at best makes the test
    machine-dependent and at worst, as with ``agent-state.subagents.tsv``,
    rewrites it.

    Repo-relative paths (``PLUGIN_DIR``, the locales dir) are deliberately
    left alone. Returns the temp directory.
    """
    import tempfile
    from pathlib import Path

    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    state_dir = plugin.core.HOME / ".claude"
    originals = {}
    for name, value in list(vars(plugin.core).items()):
        if not isinstance(value, Path):
            continue
        if value != plugin.core.CLAUDE_JSON_PATH and state_dir not in value.parents:
            continue
        originals[name] = value
        setattr(plugin.core, name, root / value.name)

    def _restore():
        for name, value in originals.items():
            setattr(plugin.core, name, value)

    testcase.addCleanup(_restore)
    (root / "projects").mkdir(exist_ok=True)
    plugin.core.PROJECTS_DIR = root / "projects"
    return root
