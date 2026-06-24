# 0018. Capture subscription usage via a statusLine sensor that chains the user's command

* Status: Accepted
* Date: 2026-06-24

## Context

We want to surface the Claude.ai subscription's rolling usage — the 5-hour
window's `used_percentage` / `resets_at` and the 7-day window — both as a
static line in the Tools submenu and as escalating threshold alerts
(spec 0011).

The problem is the data source. Claude Code exposes these limits in the
`rate_limits` field **only on the stdin it passes to the statusLine command**.
They are not in any hook payload, and not in the transcript JSONL the plugin
already reads (verified by grepping ~200 transcripts: `rate_limit` appears only
as text inside code samples, never as a structural key). So the stateless,
hook-driven, tick-rendering plugin (ADR-0002, ADR-0003) has nowhere to read
`rate_limits` from on its own.

Three options were considered:

1. **Read it from the transcript** — impossible; the field isn't there.
2. **A new Claude Code hook** — no hook event carries `rate_limits` either.
3. **A statusLine sensor** — the statusLine command is the one place Claude
   Code hands us `rate_limits`. A bundled wrapper can read it and persist it to
   a sidecar, exactly mirroring the hook→sidecar→tick flow of ADR-0003, just
   with the statusLine as the writer instead of a hook.

Option 3 is the only one that can work. The complication: `settings.json` holds
a single `statusLine.command`, and a user may already have their own status
line script there.

## Decision

Ship `hooks/usage-sensor.sh`, a statusLine wrapper. On each invocation it:

1. Reads the JSON payload on stdin, parses `rate_limits.five_hour` /
   `seven_day`, and atomically writes a one-row snapshot to
   `~/.claude/agent-state.usage`
   (`record_ts  five_used  five_resets_at  seven_used  seven_target`). When the
   payload carries no `rate_limits` (API-key auth), it writes nothing.
2. **Chains** to the user's original statusLine command, passed as its first
   argument: `printf '%s' "$input" | eval "$ORIG"`. The original's stdout is
   proxied straight through, so the user's status line still renders unchanged.
   With no original, the status line is simply blank.

`setup.sh` reads the existing `.statusLine.command`, saves it to
`~/.claude/agent-state.statusline.orig`, and rewrites `.statusLine.command` to
`bash "<sensor>" "<original>"`. It is idempotent — a command already wrapping
`usage-sensor.sh` is left untouched. `teardown.sh` restores the saved original
(or deletes the key if there was none) and removes the sidecars.

The plugin reads `agent-state.usage` on its tick: `render` prints the Tools
line, and `usage_alerts.reconcile` fires the threshold notifications. The data
is **account-wide**, not per-session, so the sidecar is a single row and
last-writer-wins among several concurrent sessions is harmless.

The **weekly pacing target** (`used%/target%`) is not in `rate_limits`; it is a
personal office-hours model. The sensor computes it (a `WK_CUM` lookup by
weekday in `WEEK_TZ` relative to `WEEK_RESET_HOUR`, ported from the user's
statusLine) and writes the finished number, so the Python side carries no
timezone/date logic.

## Consequences

* The plugin gains subscription-usage awareness without a daemon and without
  changing its stateless tick model — the sensor is just another sidecar
  writer, like the hooks.
* `setup`/`teardown` now touch `.statusLine` in `settings.json`, a surface they
  did not touch before. Both back up the file and the teardown is a clean
  round-trip via the saved original (verified: wrap → idempotent re-run →
  restore returns the byte-identical original).
* **`eval` on the saved original.** Claude Code stores `.statusLine.command`
  as a shell string and runs it through a shell, so the sensor preserves parity
  by `eval`-ing it. A pathological original containing an unescaped `"` could
  break the wrapping; this is accepted (status line commands are simple paths
  in practice) and recoverable via the timestamped settings backup.
* **Freshness.** The statusLine only runs while Claude Code is active, so the
  snapshot can go stale after Claude Code is idle. Both the render line and the
  alerts gate on `now < five_resets_at` (the window's own expiry) and skip a
  stale snapshot rather than show or alert on it.
* **API-key auth / no statusLine** degrade silently: no `rate_limits` → no
  snapshot → no usage line and no alerts; the chain still renders the user's
  status line.
* Terminology: throughout, **"session"** in this feature means the 5-hour
  subscription usage window — not a Claude Code chat session and not the
  context window. The spec and locale strings keep that meaning.
