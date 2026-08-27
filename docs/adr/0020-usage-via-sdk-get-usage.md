# 0020. Source subscription usage from the SDK `get_usage` control request

* Status: Accepted
* Date: 2026-08-27
* Supersedes: [ADR-0018](./0018-usage-sensor-statusline-chain.md)

## Context

[ADR-0018](./0018-usage-sensor-statusline-chain.md) had to work around a hard
constraint: as of mid-2026, Claude Code exposed the account's `rate_limits`
(the rolling 5-hour and 7-day subscription windows) in exactly one place — the
JSON piped to a `statusLine` command's stdin — and the status line only fires
inside a real interactive TUI. So the plugin held a hidden background `claude`
session in a detached `screen`, wrapped the user's `statusLine` with a sensor
script, and recycled the session every 10 minutes to force fresh numbers.

That worked, and it cost:

* a real `claude` process running permanently, recycled 144×/day, each recycle
  producing a first response and spending Haiku quota;
* a wrapper installed into `~/.claude/settings.json` (`statusLine.command` plus
  `refreshInterval`), with save/restore logic in both `setup` and `teardown`;
* a trusted work folder and pre-seeded onboarding keys written into
  `~/.claude.json`, because a first-run prompt would silently wedge the TUI —
  the single most common failure mode, with its own `doctor` check;
* a whole class of "the numbers froze" bugs (duplicate screen sessions, a TUI
  that stops accepting input, a blocking upsell after an upgrade).

Re-examining the current CLI (2.1.231) and IDE extension (2.1.247) shows the
constraint no longer holds. The extension's usage panel gets its data two ways:
a direct `GET https://api.anthropic.com/api/oauth/usage` from the extension
host, and — for the panel itself — a **`get_usage` control request** to the
`claude` process it already runs over the SDK control protocol. The CLI
implements the same call (`fetchUtilization`) and caches the response in
`~/.claude.json` under `cachedUsageUtilization` (write-throttled to 5 min, read
TTL 1 h).

Measured, on this machine:

```
printf '{"type":"control_request","request_id":"r","request":{"subtype":"get_usage"}}' \
 | claude -p --verbose --input-format stream-json --output-format stream-json
```

answers in ~1.7 s (0.7 s CPU) with `session`, `subscription_type`,
`rate_limits_available`, `rate_limits` (`five_hour`, `seven_day`,
`seven_day_opus/sonnet`, `model_scoped[]`, `extra_usage`, `limits[]`, `spend`)
and `behaviors`. It reports `total_cost_usd: 0` with an empty `model_usage` —
**no inference happens** — creates no transcript under `~/.claude/projects`, no
`session-env` entry, no hook events, and needs no folder trust (verified from a
fresh `mktemp -d`, which also gained no `~/.claude.json` projects entry).

## Decision

Drop the background session and the statusLine wrapper. Fetch usage by running
the `get_usage` control request directly, detached, every
`usage_fetch_interval_min` (default 3), writing the same five-column snapshot
sidecar the sensor used to write.

Cheapest source first: if Claude Code's own `cachedUsageUtilization` is fresher
than the fetch interval, read that and spawn nothing (any native `claude` the
user runs pays for the refresh). If a fetch fails, fall back to the same cache
under the CLI's own one-hour bound. If everything fails, write nothing and let
the previous snapshot age out — the menu lines vanish rather than lying.

Consequences for the surface: the *Statistics → Usage monitor* checkbox is
removed. It existed to let the user stop a process that burned quota; there is
no such process now. `usage_monitor` stays in the config as an off switch for
anyone who wants the feature gone entirely.

`setup` becomes the migration: it unwires the old `statusLine` (restoring
whatever the user had), deletes the sensor symlink, kills any leftover
`cab-usage-mon` screen session, and removes the (empty) work folder.

## Consequences

**Good.**

* No permanent background process, no quota spent, no recycling.
* `settings.json` is left alone — one less thing `setup`/`teardown` must
  save, patch and restore, and no interaction with the user's own status line.
* The onboarding-hang failure mode disappears entirely, along with its
  pre-seeding of undocumented `~/.claude.json` keys.
* Richer payload available for later use (Opus/Sonnet weekly windows,
  per-model `model_scoped` buckets, `extra_usage` credits, and `behaviors` —
  request/session counts the CLI already computes from local transcripts).
* Freshness is now bounded by our own interval rather than by whether a TUI
  happened to get a response.

**Bad / risky.**

* `get_usage` is exposed by the SDK as
  `usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET` — the name is a
  promise that it can change. Mitigated by failing soft (no snapshot → no
  lines, never a broken menu) and by the `~/.claude.json` cache fallback, which
  is a second, independent source of the same data.
* Each fetch is a ~1.7 s process. It runs detached and at most once per
  interval (the marker is written *before* the spawn, so a hung fetch can't
  stack), but it is heavier per call than reading a file.
* The plugin now needs to find the `claude` binary under SwiftBar's stripped
  PATH. `doctor` reports this explicitly, and a small candidate list covers the
  Homebrew / native / `~/.local/bin` installs.

## Alternatives considered

1. **Call `GET /api/oauth/usage` ourselves**, with the OAuth token from the
   `Claude Code-credentials` keychain item. Fewest moving parts and no
   subprocess — but it makes the plugin an OAuth client: a keychain-access
   prompt for SwiftBar/python, responsibility for refreshing an expiring token
   (racing Claude Code's own refresh, which rotates the refresh token), and a
   private endpoint with no versioning promise. Rejected: more coupling to
   undocumented internals than the CLI call, for no functional gain.
2. **Read `~/.claude.json → cachedUsageUtilization` only.** Zero cost, no
   subprocess. Rejected as the sole source: it's refreshed only when some
   native `claude` process happens to run, and was observed a week stale on a
   machine in daily use through the IDE extension. Kept as an accelerator and
   as the failure fallback.
3. **Keep ADR-0018.** Rejected — it spends quota to obtain a number that is now
   available for free, and carries the settings-patching and onboarding
   failure modes described above.
