# Spec 0004 — Subagent activity & model surface

* Status: **Implemented in 1.1.0** &nbsp;·&nbsp; spike completed 2026-05-26
* Date: 2026-05-26
* Spike notes: see [§ Spike outcome](#spike-outcome) below.

## Why

Two disambiguation problems for users running multiple agents in
parallel.

**Subagent activity.** Today a run that fans out through Claude
Code's `Task` tool keeps emitting `PreToolUse` / `PostToolUse`
events from inside each subagent — and those events arrive **with
the parent's `session_id`**. The current hook treats them as
regular parent activity, so:

- the TSV row's `last_event_kind` / `cwd` get clobbered on every
  subagent tool call,
- the menu has no way to tell *that* a subagent is running, let
  alone how many,
- if we naively filter subagent events out (clean fix for the
  clobber) the parent's TSV stops getting refreshed for the entire
  Task duration; the watchdog then demotes the row to `idle` and
  it drifts 🟡 → 🟢 → 🔵 even though the Task is still in flight.

Two related user-visible symptoms come out of this: parent rows that
go green/blue mid-Task ([issues/no-green.md](../../issues/no-green.md)),
and a menu that gives no signal that subagents are doing anything.

**Model surface.** When several agents are live at once, the title +
project + branch axes often collide (three Sonnet sessions on the
same repo, one of them on Opus for a deep refactor). Knowing which
row is which model — at a glance, without opening a submenu — adds
the disambiguation axis the user actually reaches for. Originally
scoped as part of a broader statistics spec, which was
[rejected](0005-statistics.md); only this piece survived.

The two ship together because both touch the same render path
(per-session row + submenu), but they are technically independent.

## Spike outcome

The spike (throwaway `hooks/spike-dump.sh` recording every payload
plus env vars; run against a parent that invoked two `Task`
subagents in parallel) answered the two open questions from the
original draft:

1. **Do subagents get their own `session_id`?** **No.** Every
   hook event — parent's and every subagent's — arrives with the
   parent's `session_id`. Risk #1 from the original draft fired.
2. **What carries the parent ↔ child link?** Field **`agent_id`**
   (16-char hex) in the payload, plus **`agent_type`** (e.g.
   `Explore`, `code-reviewer`). Both are present on every
   subagent-side event and absent on every parent-side event.
   Parent-side events carry an `effort` block instead.

Two more facts from the dump that shape the design:

- `SubagentStop` is a real Claude Code hook event. It fires once
  per subagent termination, with `agent_id`, `agent_type`,
  `agent_transcript_path`, and `last_assistant_message`.
- Subagent transcripts live under
  `~/.claude/projects/<project-slug>/<parent_sid>/subagents/agent-<agent_id>.jsonl`
  (plus a sibling `.meta.json`).
- Hook payloads come only on **stdin** — `CLAUDE_HOOK_EVENT` /
  `CLAUDE_SESSION_ID` / `CLAUDE_AGENT_TYPE` environment variables
  are **not** set despite the docs implying they might be.

What collapses because of (1):

- The “grouping” idea — one row per parent with indented child rows
  in the submenu — has nothing to group. There are no per-subagent
  TSV rows to hide; there's already exactly one row per parent
  session, and subagent activity is hidden inside it.
- “Subagent of a subagent” is a non-issue: nested subagents are
  still attributed to the same `session_id`, distinguishable only
  by their own `agent_id`. Render flat or hierarchical — same data.
- “Orphaned subagent” (parent pruned, child alive) is also a
  non-issue: there is no separate child row to orphan.

What survives — and grows in importance:

- **Parent state rollup**: the entire point of the subagent
  sidecar is to give the watchdog a reason not to demote the
  parent while a subagent is live.
- **Visibility**: even if subagents don't get their own rows, the
  user wants to see *that* they're running. Counter on the row +
  one info-line per live subagent in the submenu.
- **Model surface** is independent of all of this and ships as-is.

## What the user sees

The parent row gets a suffix `🤖×N`, where `N` is the live subagent
count. Each row picks up an inline model badge right of the title
**when its model differs from the user's default**. The parent's
submenu adds a non-clickable info block for live and recently-done
subagents, sitting between the existing branch / model / context
rows and the per-row actions:

```
🟡 Refactor authentication middleware ⓞ · working · 🤖×2
├── main                        ← branch (existing)
├── claude-opus-4-7             ← model row (this spec)
├── 162k / 1M (16%)             ← context (existing)
├── 🤖 Subagents
│   ├── [research] · working · 12s · Bash: grep -rn "authenticate"
│   └── [code-reviewer] · done · 4m ago
├── ────────
├── Mark as read
├── Forget
└── …
```

Rules:

- Subagent info rows are **not clickable**. The
  `vscode://anthropic.claude-code/open?session=<sid>` deep-link
  doesn't reach a subagent transcript — there is no separate
  session_id to address.
- A subagent counts as **live** while its sidecar `state` is
  `working` *or* it's within the per-row fresh window after
  `SubagentStop`. After that it drops out of `🤖×N` and falls off
  the submenu list.
- The `[type]` label comes from `agent_type` in the hook payload.
  Falls back to `[Task]` when absent (older Claude Code versions
  the spike didn't cover).
- The summary on each subagent row mirrors the existing parent
  tooltip format: `tool_name: tool_input_excerpt`, read from the
  subagent's own JSONL transcript.

### Parent state rollup

While any subagent is `working`, the parent **stays `working` /
ACTIVE**, regardless of whether the parent's own JSONL / TSV row
has gone stale. Watchdog short-circuits in this case — that's the
whole point of the subagent sidecar.

When all subagents have stopped, the parent's own state takes
over: typically `working` for a few more seconds (the parent
generating the post-Task response) then `idle` on its `Stop`.

This rule applies **only** to the visual state badge. The
menu-bar counters (🟡 N 🟢 M 🔵 K) keep counting parents — a job
with five subagents is still one yellow dot, not six.

### Model badge & submenu row

Every parent row picks up an inline badge right of the title
**only when its model differs from the user's default**:

| Marker | Model family |
|---|---|
| ⓞ | `claude-opus-*` |
| ⓢ | `claude-sonnet-*` |
| ⓗ | `claude-haiku-*` |
| ⓜ | anything else (OpenRouter, custom endpoint, …) |

The submenu picks up a non-clickable info row carrying the full
model string, sitting between the existing branch line and the
existing context line — same `font=Menlo color=#999999` style as
the other read-only rows.

Subagent info rows do **not** carry their own model badge:
subagents inherit the parent's model in current Claude Code
releases. If that ever changes (per-subagent model selection),
the row can grow a badge then — until then it would be noise.

Rules:

- **Default model** is read from `~/.claude/settings.json`'s
  `model` field. If `<cwd>/.claude/settings.local.json` carries a
  `model` field, it overrides for sessions in that cwd. Missing /
  unset everywhere → every row gets a badge (safe degradation; the
  user is never surprised by an *absent* badge).
- **Session model** is the `model` field from the **latest**
  assistant event in the session's JSONL. For mixed-model sessions
  (user switched mid-stream via `/model`), the latest model wins —
  the badge answers "what am I jumping into", not "where did the
  work happen".
- The full model row is always shown in the submenu, regardless of
  whether the badge appears on the main row. If the JSONL has no
  parseable `model` field (older sessions), the row is omitted and
  the badge falls back to ⓜ.

## Data sources

1. **Hook split in `agent-state.sh`** — if the payload carries
   `agent_id`, the event is a subagent-side event. It updates a
   new sidecar `~/.claude/agent-state.subagents.tsv` instead of
   the main `agent-state.tsv`. Parent-side events keep going to
   the main sidecar exactly as today.
2. **`SubagentStop` hook** — newly registered. Writes
   `state=stopped, last_event_ts=now` into the subagent sidecar
   for the row keyed on `(parent_sid, agent_id)`.
3. **Subagent sidecar format:**

   ```
   <parent_sid>\t<agent_id>\t<agent_type>\t<state>\t<state_since>\t<last_event_ts>
   ```

   - `state ∈ {working, stopped}`. (No `waiting` — subagents don't
     surface `Notification`/`PermissionRequest`; those always go
     through the parent.)
   - Locking: same mkdir-mutex pattern as the main sidecar.
   - GC: rows whose `last_event_ts` is older than `window_sec`
     get pruned in the same tick that GCs the main sidecar.
     Rows whose `parent_sid` no longer has a transcript on disk
     also get pruned (the whole parent went away).
4. **Subagent JSONL** for the per-subagent submenu line —
   `~/.claude/projects/<project>/<parent_sid>/subagents/agent-<agent_id>.jsonl`.
   Same tail-read window as the parent JSONL (`_USAGE_BLOCK_RE`
   reuse); we only need the latest `tool_use` chunk.
5. **Default model from settings** — read `model` from
   `~/.claude/settings.json`, then `<cwd>/.claude/settings.local.json`
   if present (overrides). Both files are tiny; reading them once
   per tick is negligible.
6. **Session model from JSONL** — extend the existing
   `_USAGE_BLOCK_RE` tail window in
   [core.py](../../claude_agents_bar/core.py) to also pick up
   `"model":"…"` from the latest assistant event.

## Config

```jsonc
{
  "show_subagents": true,
  "subagent_badge_format": "🤖×{n}",
  "model_badge": true
}
```

`show_subagents: false` makes the row badge and the submenu
subagent block disappear entirely — the parent rollup *still*
happens (otherwise watchdog regresses), it just isn't surfaced.
This is the safe-mode toggle when the surface misbehaves.

`model_badge: false` suppresses the badge on rows and the model
row in submenus entirely.

No `group_subagents` knob (the original draft had one): there is
nothing to group when there's only one row, so the knob would have
no semantics to flip between.

## Edge cases

- **Many subagents in parallel.** Counter shows them all
  (`🤖×8`). Submenu lists them all — no truncation. If the user
  has so many at once that the submenu doesn't fit, that's a
  signal worth seeing, not noise to hide.
- **Subagent of a subagent.** Still one parent `session_id`;
  nested subagent's events carry the deepest-level `agent_id`.
  Render flat in the submenu, one row per distinct `agent_id`,
  in order of `state_since`.
- **Subagent never emits a `SubagentStop`** (Task crashed
  mid-execution). Sidecar's `last_event_ts` stops advancing.
  Watchdog demotes that subagent's row to `state=stopped` after
  `watchdog_sec` of no activity, same as the parent watchdog.
- **`model` absent from older JSONL.** Badge falls back to ⓜ;
  submenu row is omitted entirely (better than showing
  `claude-unknown`).
- **Default model unset everywhere.** Every row gets a badge —
  documented as safe degradation. User can flip
  `model_badge: false`.
- **Subagent transcript missing on disk.** Submenu row still
  renders from sidecar data, but the `tool_name: excerpt` summary
  is omitted. Sidecar is authoritative; transcript is decoration.

## Out of scope (v1)

- Per-subagent click-to-open. Deep-link can't reach a subagent
  transcript.
- Per-model cost / token aggregation. Tried in spec 0005, dropped.
- Per-subagent model badge (subagents inherit parent's model in
  current Claude Code releases).
- Surfacing subagent `PermissionRequest` / `Notification`. The
  spike showed those don't reach subagent-side events anyway —
  they go through the parent.

## Technical feasibility

**Confidence:** high (post-spike) &nbsp;·&nbsp; **Estimated effort:** 1.5–2 days

Hook split is trivial (one `jq` field read for `agent_id` early
in `agent-state.sh`). The new sidecar reuses the existing
mkdir-mutex / `awk`-rewrite pattern verbatim. Watchdog rollup is
a single check in `render.py:build_session`. Submenu rendering
extends the existing `_submenu_*` helpers; no new SwiftBar
features needed (indented submenu rows are just string-prefixed
items).

Risks remaining after the spike:

- **`SubagentStop` semantics under failure.** The spike only
  exercised the happy path. If a subagent is *killed* (Claude
  Code crashes mid-stream), `SubagentStop` may or may not fire.
  The watchdog-on-the-sidecar covers this either way, but the
  user-facing label `done` vs `crashed` can't be distinguished
  without extra inference. v1: always show `done`. v2 could
  cross-check transcript tail.
- **`agent_id` collisions across runs.** The spike's two
  parallel subagents had distinct ids; whether ids are globally
  unique or only unique-per-parent isn't established. Sidecar
  is keyed on `(parent_sid, agent_id)` so we're robust either way.
