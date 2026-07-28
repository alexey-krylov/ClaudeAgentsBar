# Spec 0014 — Worktree sessions in the IDE: open-in-own-window resume

* Status: **Draft** — proposed, not yet implemented; open questions at the end
* Date: 2026-07-08

## Why

A background auto-development pipeline (external to this repo — lives in
the user's *Second-Brain* project) spawns **headless** Claude Code
sessions with `claude -p --session-id <uuid> …`, each running in its own
**git worktree** (`<repo>/autopilot/worktrees/issue-N`). The user wants
to *work with these sessions through the IDE* (VSCodium) — see them,
open them, review and continue them **with the mouse, never touching the
console**.

Today CAB already *shows* them (their transcript records
`entrypoint = claude-vscode`, because the driver launches from a
VSCode-spawned shell and inherits `CLAUDE_CODE_ENTRYPOINT`, so they clear
the interactive whitelist — ADR-0005). But the row's **click is useless
for them**: it fires the session deeplink
(`vscodium://anthropic.claude-code/open?session=<uuid>`), which the
extension delivers to whichever window is frontmost and resumes in **that
window's** working directory — not the session's worktree. For a worktree
session that means the resumed agent operates on the **parent repo's
working tree on the wrong branch**, while its whole history references
worktree paths. Silent, dangerous, wrong.

This spec makes the click **do the right thing** for worktree sessions.

## What the research established (this shaped the design — don't relitigate)

Reverse-engineering the extension bundle
(`anthropic.claude-code-2.1.201`) and one empirical test settled the
design space:

1. **The extension's session list is worktree-blind by construction.**
   Its `listSessions()` calls the enumerator with
   `{dir: this.cwd, includeWorktrees: false, includeProgrammatic: …}`.
   `includeWorktrees:false` is **hardcoded**, not a setting. Sessions
   whose cwd is a git worktree of the opened repo never appear in the
   parent repo's list.

2. **The "programmatic" filter is not what hides them.** The predicate is
   `entrypoint ∈ {sdk-cli, sdk-ts, sdk-py}` **or**
   `sessionKind ∈ {daemon, daemon-worker}`. The autopilot sessions carry
   `entrypoint = claude-vscode`, `sessionKind = null` → **not**
   programmatic. So substituting the entrypoint (an idea we considered)
   would change nothing here. It would only matter if the driver ran
   truly detached (plain terminal / cron → `sdk-cli`), which is a
   separate concern (see *Non-goals*).

3. **Resume uses the *window's* cwd, not the session's.** The extension
   spawns the resume with `cwd: n || this.cwd`, and for a
   list-/deeplink-opened session `n` is empty, so it falls through to
   `this.cwd`. **Verified empirically:** we symlinked a finished worktree
   session's transcript into the parent repo's project dir so it appeared
   in that window's list, clicked it, and `lsof` on the resumed process
   reported

   ```
   claude … --resume cd9c8ca8-…    cwd = /Users/lexx/Projects/Second-Brain
   ```

   i.e. the **parent repo**, not `…/autopilot/worktrees/issue-5`. The
   session's recorded cwd did not travel into the resume.

**Consequence — the option space collapses:**

| Approach | Visible in IDE? | Resume lands in worktree? | Verdict |
|---|---|---|---|
| **A** — re-key transcript (symlink into parent project dir) | ✅ | ❌ (parent cwd) | surfacing only; unsafe to continue |
| **B** — patch `includeWorktrees:false→true` in the bundle | ✅ | ❌ (still `this.cwd`) | strictly worse than A (fragile *and* wrong resume) |
| **C** — open the worktree as its **own** VSCodium window | ✅ (native) | ✅ (`this.cwd` = worktree) | **chosen** |

The floor nobody can route around without patching the extension:
**a worktree session resumes correctly only when its worktree is the
window's workspace.** From any parent-repo window the cwd is wrong.

## Decision

**Path C.** When the user clicks a **worktree** session in CAB, open its
worktree **as its own VSCodium window** (so `this.cwd` = the worktree),
then hand off the deeplink so the session resumes in the correct
directory. No transcript copies or symlinks, no CAB de-duplication, no
patching the extension binary, no contamination of the original
transcript.

Mechanically this is close to a path `raise-and-open.sh` already has —
its "no anchorable file" branch does `open -a <app> <cwd>` to raise a
window on a folder. Worktree sessions are steered into (a variant of)
that branch instead of the frontmost-window deeplink.

## Smart click — behaviour matrix

Detection is done **at click time** in the row's action script (cheap,
runs once per click), never on the 5 s render tick.

| Session shape | Click does |
|---|---|
| **Worktree**, finished, worktree dir **exists** | `open -a <editor> <worktree>` → wait for window → deeplink resume. Resume lands in the worktree. ✅ |
| **Worktree**, **live** (headless still running) | **Do not resume** — resuming forks a second agent into the same worktree (file race). Open a **read-only live transcript** instead (a `tail -f | jq` view in Terminal). |
| **Worktree**, worktree dir **gone** (branch merged & pruned) | Nothing to resume into. Fall back to a read-only transcript / history view and a short notice. |
| **Not** a worktree (ordinary IDE / terminal session) | **Unchanged** — current deeplink / `raise-and-open` behaviour. |

Notes:

* **"Worktree"** = `sidecars.is_worktree_checkout(cwd)` — already
  computed every tick and exposed as `Session.is_worktree`
  (`sidecars.py:1219`, set in `render.py:197`). Git-truth (`.git` is a
  file starting `gitdir:`), not a path-name heuristic — so it generalises
  beyond this one pipeline.
* **Liveness** = `pgrep -f <session-id>` at click time. A headless
  `claude -p … --session-id <uuid>` always carries the id in argv, so
  the probe is reliable for exactly the sessions we must protect from
  forking. Not run per tick (respects the render-tick-cost rule).
* **The live-session read-only transcript** is a generalisation of the
  reference `autopilot/watch.sh` (`tail -f <sid>.jsonl | jq` → agent
  replies + tool names). Found by globbing `<sid>.jsonl` across
  `~/.claude/projects/*/`.

## The marker

CAB **already** marks worktree sessions: a colored circled **`ⓦ`**
immediately before the right-hand duration (`render.py:660`), green
normally / orange when the worktree is also a cwd collision. It is
visible on the autopilot rows today. So the *set* of "sessions that take
the path-C click" is already flagged; we are not inventing a marker for
nothing.

What `ⓦ` does **not** say is "this is an autonomous background agent,
not a session you're driving". That distinction is what the user asked to
surface. The honest constraint: **CAB cannot cheaply tell a headless
pipeline session from a human's manual worktree session** — both record
`entrypoint = claude-vscode`, and the only positive signal (`-p` in the
live process argv) exists only while the session is alive and would cost
a per-tick `pgrep`. So an auto-detected "robot" badge would lie on the
tail of finished sessions.

**Proposed marker — `ⓐ` (autopilot), driven by a session tag the
pipeline sets.** CAB already has a **tags** sidecar (spec 0013) whose
glyph vocabulary is exactly this family (`ⓡ ⓞ ⓨ ⓖ ⓑ ⓟ ⓦ`; model badges
`ⓞ ⓢ ⓗ ⓕ`), so `ⓐ` slots in with zero new rendering machinery. The
auto-dev driver tags each session it spawns (one line: write
`agent-state.tags`), and CAB renders `ⓐ` for tagged sessions. This is
truthful (the producer declares intent) rather than guessed, and reuses a
shipped feature.

Fallback if the pipeline can't/won't tag: keep `ⓦ` alone as the marker
(option A below) — it already flags the correct set, just without the
human-vs-robot nuance.

## Non-goals

* **No re-keying / symlinking** transcripts into foreign project dirs —
  the experiment proved it surfaces the session but resumes in the wrong
  cwd, and it duplicates the row in CAB (which globs every project dir and
  does not de-dupe by session id) and risks write-through into the
  original transcript.
* **No patching `extension.js`.** Fragile across extension updates and it
  doesn't even fix the resume cwd.
* **No change to ADR-0005.** Truly-detached headless runs
  (`entrypoint = sdk-cli`) stay hidden; surfacing those is a separate
  decision with its own config surface.
* **No attaching to a live headless session.** Impossible by design
  (`claude -p` is non-interactive; there is no attach). Live sessions get
  a read-only transcript only.
* **No aggregating worktree sessions into a single parent window's list.**
  That is exactly what forces the wrong-cwd resume; the whole point of C
  is one window per worktree.

## Cost, accepted

* **One VSCodium window per worktree.** Unavoidable: correct resume
  requires `this.cwd` = the worktree. This is the price of "work with it
  natively in the right place".
* **Only while the worktree exists.** The pipeline prunes worktrees after
  merge (confirmed: 3 of 4 finished autopilot sessions had `wt-GONE`), so
  a merged session offers history view only — but a merged session has
  nothing to continue anyway.

## Files this will touch (rough, for scoping only)

* `claude_agents_bar/render.py` — steer worktree rows to the new action
  script; render the `ⓐ` marker for tagged sessions.
* `bin/app/` — a new action (open worktree as its own window → deeplink)
  and a live-transcript viewer action; possibly a thin reuse of
  `hooks/raise-and-open.sh`.
* `locales/*.json` — menu strings for any new submenu items.
* `tests/` — click-routing / marker rendering; worktree + liveness
  branches.
* `docs/adr/` — an ADR recording the detection scheme (why click-time
  `pgrep`, why `this.cwd`=worktree is mandatory, why not A/B).
* `README.md` / `CHANGELOG.md` — user-visible behaviour.

## Open questions (lock before implementation)

1. **Marker.** `ⓐ` driven by a pipeline-set tag (recommended, truthful)
   vs. just reuse the existing `ⓦ` vs. another glyph.
2. **Window trigger.** CAB row click opens the worktree window
   (recommended — matches "work from the bar with the mouse") vs. the
   pipeline opens the window itself on spawn.
3. **New window per worktree** — confirmed acceptable? (No alternative
   that preserves correct resume.)
4. **Live-session click.** Read-only live transcript (recommended) vs.
   open the worktree window **without** firing the deeplink (visible, but
   the user must not type into it).
