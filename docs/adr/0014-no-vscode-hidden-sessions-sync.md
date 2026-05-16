# 0014. Don't sync with VSCode's hiddenSessionIds

* Status: Accepted
* Date: 2026-05-17

## Context

When the user deletes a session from the Claude Code VSCode extension sidebar,
the extension does **not** delete the `.jsonl` transcript. It calls
`settings.hideSession(id)`, which appends the session ID to a `hiddenSessionIds`
array stored inside the `Anthropic.claude-code` key of the editor's global state
SQLite database:

```
~/Library/Application Support/VSCodium/User/globalStorage/state.vscdb
~/Library/Application Support/Code/User/globalStorage/state.vscdb
~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
```

Because the transcript file remains on disk, `iter_active_jsonls` still finds
it and the session keeps appearing in the menu bar, even though the user
explicitly deleted it from the IDE.

The straightforward fix is to read `hiddenSessionIds` from those SQLite files
at render time and skip any session whose ID appears there.

## Decision

Do not read the editor's SQLite global state. Instead, document that the
existing row-level **Forget** action in the session submenu is the intended
tool for this workflow.

## Reasons

**1. Private, undocumented internal API.**
The key name `Anthropic.claude-code`, the shape of its value, and the field
name `hiddenSessionIds` are extension implementation details with no stability
guarantee. Any Anthropic update can rename or restructure them silently.

**2. Multi-editor combinatorics.**
VSCodium, VSCode, and Cursor each have their own DB at a different path. A
user might delete a session in one editor while the other is not installed, or
have both open simultaneously. The "union of all editors' hidden lists" logic
is easy to get wrong and impossible to test against editors you don't have
installed.

**3. SQLite WAL locking is subtle.**
Although `sqlite3` in read-only URI mode (`file:…?mode=ro`) is safe against
the writer, WAL checkpointing can stall readers on a heavily-written DB. The
editor writes to `state.vscdb` constantly (editor state, workspace layout,
extension settings). A stall on the menu-bar render tick is user-visible.

**4. Wrong tool for a menu-bar switcher.**
ClaudeAgentsBar is a quick-glance and quick-switch tool. Sessions scroll out
of view on their own via the `max_age` window. A session the user deleted
from the IDE sidebar is almost certainly also going to stop emitting hook
events (it's closed), so it will appear `idle` and expire naturally within
`max_age_seconds` — no manual action needed in the common case.

**5. The Forget action already exists.**
Every session row's submenu has a **Forget** action backed by
`bin/app/forget-session.sh`. It writes a cutoff timestamp to
`~/.claude/agent-state.forget` (the per-session sidecar). The plugin skips
any session whose last event is at or before that timestamp. This is the
deliberate, stable, editor-agnostic path for "I don't want to see this session
anymore."

## Consequences

* Users who delete sessions from the IDE sidebar and still see them in the
  menu bar should use **Forget** from the row submenu, or simply wait for
  `max_age` to evict them.
* The troubleshooting doc should mention this — the question will come up.
* If Anthropic ever exposes a stable, filesystem-readable deletion registry
  (e.g. a plain-text sidecar analogous to `agent-state.tsv`), revisiting this
  decision would be trivial. The SQLite approach is not that.

## Related

* [ADR-0003](./0003-hook-driven-sidecar.md) — the sidecar model that Forget
  builds on.
* [ADR-0002](./0002-stateless-tick-rendering.md) — why every read in the
  render path must be cheap and fail-safe.
