# Spec 0003 — Keep Awake

* Status: **Implemented in 1.1.0**
* Date: 2026-05-26

## Why

A 20-minute agentic loop with the laptop lid open will hit display
sleep mid-tool unless something explicitly inhibits it.
`caffeinate -i` (idle-sleep inhibit) is the macOS primitive — the
plugin can own its lifecycle and tie it to actual session activity.

## What the user sees

Tools submenu gains a new section:

```
─────────────────────────────
Keep awake: Auto · holding while 2 sessions working
  Off
  Auto (keep awake while sessions are running)
  Always (keep awake until disabled)
─────────────────────────────
```

- Status line shows current mode + live effect (`holding` vs
  `idle`) + reason (`while 2 sessions working`,
  `until disabled`, `—`).
- Clicking a mode writes the sidecar; takes effect on the next
  tick (≤ 5 s).

## How it works

The plugin owns one detached `caffeinate -i` process. On every
5 s tick:

1. Read mode from `~/.claude/agent-state.keep-awake.mode` (default
   `off`).
2. Read PID from `~/.claude/agent-state.caffeinate`. Liveness
   check: `os.kill(pid, 0)`. Bad / stale PID — unlink the file.
3. Decide *should be running*:
   - `off` → false
   - `always` → true
   - `auto` → true iff any session is `working` (counting
     parent rollup from [spec 0004](0004-subagent-grouping.md)).
4. Reconcile: spawn or kill as needed.

### Spawn

```python
proc = subprocess.Popen(
    ["/usr/bin/caffeinate", "-i"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
sidecar_path.write_text(str(proc.pid))
```

`start_new_session=True` detaches the child from the plugin's
process group, so it survives the plugin process exiting after
each tick. On the next tick we adopt the PID back via the sidecar.

### Kill

`os.kill(pid, signal.SIGTERM)`, then unlink the sidecar. Before
signalling, verify the process is still `caffeinate` via
`/bin/ps -p <pid> -o comm=` — defensive against PID reuse.

## Config

```jsonc
{
  "keep_awake": "off"
}
```

Values: `"off"` (default), `"auto"`, `"always"`. The config is the
source of truth on first launch; once the user clicks a mode in
the menu, the sidecar takes precedence.

## Edge cases

- **Mac sleeps anyway** (lid closed, no external display) →
  `caffeinate -i` doesn't override the clamshell sleep policy;
  documented limitation.
- **Plugin uninstall** → `bin/install/teardown.sh` kills any
  `caffeinate` we own (read the sidecar before unlinking sidecars).
- **Hung `caffeinate`** that ignores SIGTERM — followed by
  `SIGKILL` after a 1 s grace. Almost never happens for
  `caffeinate` but defensive.
- **Multiple plugin instances** (user re-symlinked into two
  SwiftBar plugin dirs) — both would try to manage one PID file.
  Last writer wins; one orphaned `caffeinate` may linger until the
  user signs out. Not worth the locking complexity.
- **Battery** — in `auto` we still hold awake on battery power. If
  this becomes a complaint, add `keep_awake_on_battery: false`
  later; for now, prefer simple.

## Out of scope (v1)

- `caffeinate -d` (display sleep inhibit). Most users *want* the
  display to dim; only the system sleep is the problem. Could add
  `keep_awake_display: true` later.
- Per-session keep-awake (only hold while *this specific* session
  is working). Submenu cost not justified.
- Showing a status icon (☕) in the menu bar itself. Possible via
  prefix on the title; not worth real estate.

## Technical feasibility

**Confidence:** medium-high &nbsp;·&nbsp; **Estimated effort:** ~1 day

**Confirmed:**
- `/usr/bin/caffeinate` ships on every macOS we support
  (10.9+). `man caffeinate` lists `-i` as stable since 10.8.
- `subprocess.Popen(..., start_new_session=True)` is the standard
  POSIX double-fork-less detach idiom and works on macOS — the
  spawned `caffeinate` runs in its own session and is not reaped
  when the SwiftBar tick exits.
- `os.kill(pid, 0)` liveness check is POSIX, works on macOS.
- PID sidecar pattern is what `bin/app/forget-sessions.sh` and
  friends already do.

**Needs verification:**
- Confirm `caffeinate` started by SwiftBar's child process doesn't
  inherit any SwiftBar-side signal mask that breaks SIGTERM
  delivery later. Quick test: spawn from a SwiftBar plugin, exit,
  send SIGTERM from another shell. Expect: dies cleanly.
- Confirm that a `caffeinate -i` started detached doesn't keep
  the menu-bar icon visible if SwiftBar quits — i.e. its parent
  process going away doesn't reparent us to launchd in a way that
  surprises the user with a stuck process. Should be fine; verify
  on Sonoma + Sequoia.

**Risks:**
- **PID reuse** after weeks of uptime. macOS PIDs are 32-bit but
  recycled. If the sidecar holds a stale PID that now belongs to
  Slack, we must not `kill -TERM` it. `ps -p <pid> -o comm=`
  comparison against `caffeinate` is the gate.
- **Plugin disabled mid-run.** SwiftBar can be paused; the
  reconcile loop stops. Our `caffeinate` keeps holding awake
  forever. Mitigation: `bin/install/teardown.sh` reads the
  sidecar and kills.
- **macOS deep sleep** events may not run our reconcile for hours.
  On wake the next tick reconciles. No harm done — we either
  spawn fresh or kill stale.

**Mitigations:**
- Process-name check before SIGTERM.
- Teardown script kills owned PID on uninstall.
- Sidecar contains only the PID (one int); easy to inspect
  manually if something goes sideways.
