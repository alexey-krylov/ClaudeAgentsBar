# 0018. Source subscription usage from a hidden background `claude` session

* Status: Accepted
* Date: 2026-06-29

## Context

We want the Claude.ai subscription's live usage — the rolling 5-hour window's
`used_percentage` / `resets_at` and the 7-day window — in the menu, both as a
line and as escalating alerts (spec 0011).

The data lives in a `rate_limits` field that Claude Code exposes in exactly one
place: **the JSON it pipes to a `statusLine` command's stdin**, and only for
Claude.ai subscription auth after the first API response. It is **not** in the
transcript JSONL (verified by grepping ~200 transcripts — `rate_limit` appears
only inside code samples), nor in any hook payload, nor any local file or API.

Worse, empirically verified: the status line only runs inside a **real
interactive TUI**. `claude -p`, piped input, and the VSCode/Cursor extension
**never invoke it** — the extension renders its own `/usage` panel from
in-memory data it doesn't persist. The project's target user works in VSCode,
so a statusLine sensor alone (the obvious approach, and what the first cut of
this ADR proposed) writes its sidecar *never* in that environment.

Options considered:

1. **statusLine sensor only** — works in a terminal session, dead in VSCode.
2. **Estimate from transcript JSONL** — the figures are dominated by
   `cache_read` tokens (96 % of volume in testing) whose rate-limit weighting
   Anthropic doesn't publish; the estimate drifts by multiples. Rejected:
   inaccurate is worse than absent for "how close am I to the limit".
3. **A usage/rate-limit API** — only exists for Console *API-key* org usage
   (Admin key), not Claude.ai subscription windows.
4. **Hold a real interactive `claude` session open ourselves**, so the status
   line genuinely fires, and read `rate_limits` off it. The only path to
   accurate live data outside a terminal.

## Decision

Run a **hidden background `claude` session** and read its status line.

- `hooks/usage-sensor.sh` is wired in as that session's `statusLine` command
  (by `setup`). It parses `rate_limits` off stdin and atomically writes
  `agent-state.usage`; it also chains to any
  pre-existing user `statusLine` (saved for teardown) so a terminal user's own
  status line still renders.
- **The sensor writes the sidecar only for the daemon's own session.** The
  `statusLine` command is registered globally, so *every* interactive session
  fires it — and an old session reopened via resume carries a stale cached
  `rate_limits` that would clobber the daemon's live snapshot and make the menu
  flap. The sensor gates on the payload's `cwd` being the trusted monitor folder
  (`~/.claude/cab-usage-monitor`); any other session just chains through.
- The session runs in a **detached `screen`** (`screen -dmS cab-usage-mon …
  claude --model <haiku>`): a genuine TTY, so the status line fires, but **no
  window**. It runs in a trusted folder (`~/.claude/cab-usage-monitor`, marked
  trusted in `~/.claude.json` by `setup`) so no folder-trust prompt blocks it.
- **First-run prompts must be silenced.** A blocking prompt at startup stops the
  session ever reaching the ready TUI, so the status line never fires (observed
  on v2.1.181: the *"Try the new fullscreen renderer?"* upsell). There is no
  supported flag/env to suppress these (`--safe-mode` doesn't; `--bare` /
  `CLAUDE_CODE_SIMPLE` disables the status line we depend on), so `setup`
  pre-seeds the undocumented `~/.claude.json` keys that gate them —
  `fullscreenUpsellSeenCount` past its show-threshold (never lowering a higher
  existing value) and `hasCompletedOnboarding`.
- `setup` sets `statusLine.refreshInterval` so the line ticks on a timer.
- `claude_agents_bar/usage_monitor.reconcile` runs on the plugin's 5-second
  tick — the same lifecycle pattern as `keep_awake` (no launchd; the project
  has none): if the master switch is on it spawns the session when absent, and
  **recycles** it (kill + fresh spawn) every `usage_ping_interval_min`. A
  long-lived session's `rate_limits` go stale — the server only refreshes
  `used_percentage` on a *new* API response, and stuff-pinging a live TUI proved
  unreliable (it eventually stops accepting input and the numbers freeze) — so
  cycling the session forces a new first response with current usage;
  `refreshInterval` alone only re-renders the same numbers. The session is
  tracked by its unique screen name, not a PID. `reconcile` lists sessions by
  their PID-qualified `<pid>.cab-usage-mon` token (so kills are unambiguous) and
  **collapses duplicates** — a spawn race can briefly produce two, each a real
  Haiku process burning quota, so seeing more than one triggers kill-all +
  single respawn.
- A single master switch `usage_monitor` (config + sidecar override + *Tools*
  toggle) gates the background session, the usage line, and the alerts together.
  **On by default** — zero-config (`setup` trusts the work folder); flip it off
  to stop the background session and its recycles.

## Consequences

* Accurate live usage in the menu in VSCode (and anywhere), because we no
  longer depend on the user happening to have an interactive terminal open —
  we hold one ourselves, invisibly.
* **Cost:** the background session is a real `claude` process (visible in `ps` /
  Activity Monitor, just window-less) and each recycle's first response spends a
  little Haiku quota. Hence a 10-minute default recycle interval (5-minute
  floor) and a single switch that kills all of it (on by default for
  zero-config, but one click off). The monitor's own requests nudge the very
  number it reports — accepted; on a
  5-hour window the effect is negligible.
* `setup`/`teardown` now touch `statusLine` and `~/.claude.json` (the trust
  entry plus the two onboarding-suppression keys) — both back up first and merge
  additively. The `~/.claude.json` format is undocumented and could change
  between Claude Code versions; we only add `projects[path].hasTrustDialogAccepted`,
  `fullscreenUpsellSeenCount`, and `hasCompletedOnboarding`, with a backup, and
  degrade to a one-time prompt if the file is absent.
* **This stays brittle.** The background session is a real interactive TUI, and
  a future Claude Code version can introduce a *new* blocking first-run prompt
  the pre-seed doesn't cover — the session would hang and the line would go
  empty. There is no "non-interactive interactive" mode to lean on; the only
  remedy is to silence each known prompt and document the diagnosis (attach with
  `screen -r cab-usage-mon`, answer, `Ctrl-A d`). See
  [troubleshooting](../troubleshooting.md).
* Dependence on `screen` (ships with macOS) and on `claude` CLI flags / the
  status line continuing to carry `rate_limits`. All failure modes are
  fail-soft: if the session can't spawn or the snapshot goes stale, the line
  simply disappears (a `record_ts` staleness gate hides frozen numbers).
* "Session" throughout this feature means the **5-hour usage window**, not a
  Claude Code chat or the context window.
