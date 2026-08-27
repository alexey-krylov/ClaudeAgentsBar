"""Shared bootstrap and fixtures for the ClaudeAgentsBar test suite.

Pins the repo root on ``sys.path`` and re-exports the package as ``plugin`` so
every ``test_*.py`` module can ``from _helpers import plugin``. ``_make_session``
builds a ``Session`` with sensible defaults; tests override the field they care
about. Most patches target ``plugin.core`` / ``plugin.<submodule>`` rather than
``plugin`` itself — module-level globals are read via the defining submodule.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import claude_agents_bar as plugin  # noqa: E402


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
