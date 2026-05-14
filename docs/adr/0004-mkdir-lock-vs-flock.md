# 0004. `mkdir`-based mutex, not `flock`

* Status: Accepted
* Date: 2026-05-13

## Context

`agent-state.tsv` is written by the Claude Code hook on every event,
and read+rewritten by the plugin during garbage collection. Concurrent
hooks (a subagent and its parent racing) plus the plugin's rewrite
constitute three writers needing serialisation.

Standard POSIX choices:

1. **`flock(1)`** — the obvious one, but **not installed on stock
   macOS**. `command -v flock` fails on a clean Sonoma/Sequoia install
   unless the user has `brew install util-linux` or similar.
2. **`shlock` / `lockfile`** — from `procmail`. Not preinstalled either.
3. **Atomic `mkdir`** — `mkdir <path>` is guaranteed atomic on every
   POSIX filesystem and works with nothing but `bash` and `coreutils`.

Earlier iterations used `flock` with `command -v flock` as a guard,
silently skipping the lock when it wasn't available. That meant *every*
stock macOS user was running unlocked — a race waiting to happen.

## Decision

Use a directory as the mutex token:

* `agent-state.tsv.lock.d` is the lock primitive.
* `mkdir lock.d` succeeds for exactly one holder; failures spin with a
  50 ms sleep.
* On exit, `rmdir lock.d` releases.
* Both the hook (`hooks/agent-state.sh`) and the plugin
  (`_sidecar_lock` context manager in Python) use this same path —
  one protocol, two implementations.

A stuck lock is recovered by stealing after a timeout (~2 s for both
implementations). Holders only need the lock for microseconds, so the
timeout is generous.

## Consequences

**Wins:**

* Works on every Mac out of the box. No dependency on `util-linux`.
* Same protocol shared between Bash and Python — no cross-language
  primitive headaches.
* Auto-recovery via timeout-and-steal prevents deadlocks from crashed
  holders.

**Costs:**

* Spin-wait isn't free; contention pays a few ms. Acceptable given
  fewer than a couple of writers per second in steady state.
* Stealing after a timeout assumes the prior holder is dead. A
  slow-but-alive holder loses its critical section. In practice the
  holders only need the lock for tens of microseconds, so this is
  vanishingly rare.

## Related

* [ADR-0003](./0003-hook-driven-sidecar.md) — what the lock protects.
