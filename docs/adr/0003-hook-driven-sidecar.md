# 0003. Hook-driven sidecar TSV for live state

* Status: Accepted
* Date: 2026-05-13

## Context

The plugin needs to distinguish three live states: `waiting` (model is
blocked on the user — permission prompt or `AskUserQuestion`), `working`
(a tool call is in flight), and `idle` (turn ended). Options:

1. **Parse JSONL transcripts post-hoc.** Tail the file, infer state
   from the last event. Tedious — JSONL events don't cleanly map to
   our three states (a permission prompt isn't a transcript event),
   and we'd be redoing work Claude Code already does internally.
2. **Poll Claude Code process state** (`pgrep`, `lsof`). Doesn't carry
   semantic info; also expensive.
3. **Claude Code hooks.** Claude Code already invokes user-defined
   shell scripts on every meaningful event (`SessionStart`,
   `PreToolUse`, `Notification`, `Stop`, …). One script writing a TSV
   row per event gives the plugin a perfect, semantically-clean index.

## Decision

A single `hooks/agent-state.sh` script is registered against every
relevant event in `~/.claude/settings.json`. It writes one row per
session into `~/.claude/agent-state.tsv` — the sidecar. The plugin
reads that sidecar on every tick.

TSV schema (tab-separated, last write wins):

```
<session_id> <state> <last_event_ts> <last_event_kind> <cwd>
```

Mapping from event to state lives in `settings-hooks.json`:

| Event              | Written state |
|--------------------|---------------|
| `UserPromptSubmit` | `working`     |
| `PreToolUse`       | `working`     |
| `PostToolUse`      | `working`     |
| `Notification`     | `waiting`     |
| `Stop`             | `idle`        |

`SessionStart` is **deliberately not registered**. It fires both on a
genuine cold start and when the user merely *opens* an existing
session in the IDE (the VSCode extension emits it on every tab
switch, with `source=resume`). Earlier versions tried to handle this
by branching on `payload.source` and writing different states for
different sources, but the simplest fix is the right one: don't
write anything on SessionStart at all. The five events above already
cover every flavour of "real agent activity"; anything else is
noise.

To make this stick, `collect_sessions` filters out any session whose
JSONL transcript has no corresponding row in `agent-state.tsv`. This
flips the earlier behavior described in *Costs* below: previously,
sessions without a sidecar row showed up as `idle` via the JSONL
mtime fallback in `build_session`. That fallback now only paths
through code that's effectively dead — the filter strips the
transcript before `build_session` runs — but the code itself is
kept defensively in case a future caller skips the filter.

The classifier (`_classify`) additionally requires
`last_event_kind == "Stop"` before granting the FRESH grace window,
so that even if a stray idle row sneaks into the TSV by some other
path (e.g. the watchdog downgrading a stuck `working`), the menu
won't paint it green.

## Consequences

**Wins:**

* Semantically clean: each state corresponds to a hook the Claude Code
  runtime emits on a real lifecycle event.
* Plugin has zero coupling to JSONL internals beyond title/cwd reads.
* Hook is dumb — three jq invocations and an `awk` rewrite. Trivial to
  port to another shell or another renderer.

**Costs:**

* Sessions started **before** the hook was installed don't appear in
  the menu at all until they emit their next hook event. The earlier
  JSONL-mtime fallback used to render them as `idle` immediately, but
  that path also let pure IDE-tab-switch noise into the menu (since
  Claude Code touches the JSONL on every `SessionStart`), so the
  filter in `collect_sessions` now keeps untracked sessions
  out entirely.
* Hook adds ~5 ms to every Claude Code event. Negligible.
* The sidecar grows monotonically without explicit cleanup; addressed
  by [ADR-0004](./0004-mkdir-lock-vs-flock.md) (lock) and a plugin-side
  garbage collector that drops rows whose transcript is gone or whose
  last event has fallen out of the dropdown window.

## Related

* [ADR-0004](./0004-mkdir-lock-vs-flock.md) — how concurrent writes are
  serialised.
* [ADR-0007](./0007-project-outside-plugins-folder.md) — why the hook
  installer can't live inside the SwiftBar plugins folder.
