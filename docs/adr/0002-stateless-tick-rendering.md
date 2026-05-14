# 0002. Stateless rendering on every tick

* Status: Accepted
* Date: 2026-05-13

## Context

SwiftBar spawns the plugin as a fresh subprocess every refresh interval.
Two ways to handle state:

1. **Stateful daemon** — a long-lived process maintains an in-memory
   view of sessions; SwiftBar pings it; the plugin script becomes a thin
   client. Reactivity becomes sub-second (the daemon can push state on
   `agent-state.tsv` mtime changes via `kqueue`).
2. **Stateless rebuild** — every invocation walks the on-disk sources
   from scratch and emits a complete menu.

The data sources (`~/.claude/projects/*/*.jsonl` + `agent-state.tsv`)
are small enough that a full rebuild is microseconds even on a busy
machine: the per-transcript JSONL scan is capped at 256 KB, the sidecar
is a few dozen rows, git branch reads are file I/O without subprocess.

## Decision

Always rebuild from scratch. No daemon, no IPC, no shared in-memory
state. The plugin process exists for the duration of one render and
exits.

## Consequences

**Wins:**

* Trivial to reason about: every tick is deterministic given the
  filesystem state at that instant.
* Trivial to test: pure helpers + filesystem reads, no lifecycle.
* No cache invalidation, no stale-state bugs, no IPC protocol.
* Resilient to script edits: a bad change loses one tick, not the
  whole runtime.

**Costs / boxed-in by:**

* Minimum reaction time is one tick (5 s by default). For a "permission
  prompt opened" notification this is noticeable.
* The per-tick JSONL scan is O(active-sessions). Cap kept low to make
  this tolerable; see `JSONL_TITLE_SCAN_BYTES`.

A sub-second daemon could be bolted on later as an optional companion
that just pings SwiftBar's refresh URL on `agent-state.tsv` writes —
the plugin itself wouldn't change.
