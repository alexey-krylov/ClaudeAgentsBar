# 0016. Multi-workspace window focus via open-document, not the deeplink alone

* Status: Accepted
* Date: 2026-06-02

## Context

A session row (dropdown) and a notification banner both resume a session
by firing `<scheme>anthropic.claude-code/open?session=<id>`. That URL
carries only the session id; the editor's `anthropic.claude-code` handler
delivers it to whichever window is **frontmost** — it does not route by
workspace. With several editor windows open the session resumes in the
wrong one, and the resume can miss entirely (the session must belong to
the focused workspace). There is no documented way to target a window
from the deeplink (no `windowId`/`cwd` param), and the extension does no
workspace matching of its own.

The only lever we have is to surface the right window *before* firing the
deeplink. Each session has a known `cwd`, so the window we want is the one
whose workspace owns that directory.

## Decision

Raise the owning window by **opening a file inside the cwd**, then fire
the deeplink. One shared helper, `hooks/raise-and-open.sh`, backs both
paths (the dropdown via `bin/app/open-session.sh`, the banner via
terminal-notifier `-execute`):

1. Pick an anchor file inside the cwd — the session's last touched file
   (newest `tool_use.file_path` from the transcript), falling back to a
   stable project file (`README`, …).
2. `open -a <editor.app> <anchor>` — an "open document" Apple Event the
   editor routes to the window whose workspace already contains the file.
3. Wait for the editor to be frontmost (`lsappinfo`), then a short
   `editor_focus_settle_sec` pause, then `open <deeplink>`.

Gated by `multi_workspace_mode` (config default `true`, live-toggleable
from *Tools → Multi-workspace mode* via an `agent-state.multi-workspace.mode`
sidecar that wins over config — same pattern as *Keep awake*). Off fires
the deeplink directly (the pre-existing single-window path).

## Reasons

**Open a file, not the folder.** `open -a <app> <cwd>` / `code <cwd>` are
folder-identity based: when the cwd is one root of an already-open
multi-root workspace they spawn a brand-new single-folder window
([VS Code #215749](https://github.com/microsoft/vscode/issues/215749)).
Opening a *file* is workspace-aware and surfaces the existing window,
multi-root included.

**`open -a`, not the editor CLI.** `code -g <file>` is also
workspace-aware but pays a ~1 s Node CLI startup per click; `open -a`
goes through LaunchServices and stays near-instant (~0.07 s), so the
focus step is cheap enough to always run — no need to detect how many
windows are open (which has no cheap, TCC-free API on macOS anyway).

**The settle pause.** `open -a <file>` returns before the anchor tab has
rendered; without a beat the deeplink fires first and the anchor tab
renders *on top of* the resumed chat. The pause lets the tab land so the
session ends up focused. It's a knob because the threshold is
machine/load dependent.

**Sidecar toggle, not config rewrite.** The menu checkbox writes a
sidecar rather than editing `config.json`, so it never reformats or
strips comments from the user's hand-edited file. Read by both Python
(dropdown) and the bash hooks (banners) so the two paths stay in step.

## Consequences

* One extra editor tab per click (the anchor). Reused across clicks, so
  tabs don't pile up; opening the last touched file is usually where the
  user wanted to be anyway.
* Adds `editor_focus_settle_sec` of latency when the mode is on.
* Relies on macOS routing an "open document" event to the owning window —
  standard behaviour, not a private editor API (consistent with ADR-0014).
* Only engages for the built-in editor schemes whose `.app` we know
  (`vscode://`, `vscodium://`, `cursor://`, `windsurf://`, `positron://`);
  a custom `editor_url_scheme` always uses the direct path.
