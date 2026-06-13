# Spec 0009 — Notification banner format

* Status: **Implemented**
* Date: 2026-06-13

## Why

The three notification surfaces (spec 0005 `Stop`, spec 0007 the
two-field marker, spec 0008 idle reminders) all render a macOS banner
through the shared `_emit_notification` (`hooks/_notify-common.sh`). The
banner has three text lines — `-title`, `-subtitle`, `-message` — and
the way they were filled wasted two of them:

* **Line 1 (`-title`)** was a fixed status label for two of the three
  surfaces — `Claude awaiting input`, `Claude session unread`. Stop was
  the only one whose title carried real information (the session's
  `ai-title`).
* **Line 2 (`-subtitle`)** was the constant string `Claude Code` on
  every banner — pure chrome, no per-session information.
* **Line 3 (`-message`)** carried the random phrase *and* the
  name/summary, with the phrase as a fallback — so the phrase competed
  with the actual summary for the one informative line.

This reshuffles the three lines so each carries something useful, and so
the **type of notification** (done / awaiting / idle) reads from a
coloured emoji rather than a wasted label line.

## The three lines

| | **Stop** | **Awaiting** | **Idle** |
|---|---|---|---|
| **Line 1** `-title` | `ai-title` → first user message → `Done` *(unchanged)* | `❓ <phrase>` | `⚠️ <phrase>` |
| **Line 2** `-subtitle` | `<project> — <icon> <branch>` | `<project> — <icon> <branch>` | `<project> — <icon> <branch>` |
| **Line 3** `-message` | `<summary>`, else `<phrase>` *(unchanged)* | `<name> — <summary>` | `<name> — <summary>` |

`<phrase>` is the random pick from `notify_wait_phrases` /
`notify_idle_phrases`; `<name>` / `<summary>` are the two fields of the
latest `*-- Name - Summary*` marker turn (spec 0007).

### Line 1 — type indicator

macOS Notification Center banners are plain text — `terminal-notifier`
cannot colour an individual character or render attributed strings. The
type is therefore signalled with a leading **emoji** (intrinsically
coloured), prepended to the phrase:

* Awaiting → `❓` (red question mark)
* Idle → `⚠️` (yellow warning triangle)
* Stop → no prefix (its title is the task, not a status)

The emoji is a **banner-only** decoration. It is *not* added to the
spoken `say` text — otherwise VoiceOver/`say` would read "red question
mark". `SAY_TEXT` is built from the raw phrase, so the separation is
already there; only the `-title` argument gets the prefix. The two
emoji are **hardcoded constants** in the shims, not config knobs (KISS;
a knob can be added later if asked).

For Stop, line 1 is untouched: it stays the `ai-title` of the session
(falling back to the first user message, then `Done`).

### Line 2 — project / branch

The subtitle is computed inside `_emit_notification` from the session
`cwd` (already passed as `$6`), uniformly for all three surfaces:

* **project** = `basename "$cwd"` — matches the menu submenu's project
  line (`core._project_name`, which is `Path(cwd).name`).
* **branch** = read straight from `.git/HEAD` (worktree-aware: a `.git`
  *file* is followed through its `gitdir:` indirection; detached HEAD
  yields the 7-char SHA) — mirroring `sidecars.current_git_branch` so
  the banner's branch matches the submenu's branch line.

Format: `"<project> — <icon> <branch>"`, where `<icon>` marks the
checkout kind — `ⓦ` for a linked worktree (`.git` is a file), `⎇` for an
ordinary branch — the plain-text stand-in for the submenu's worktree
marker / branch glyph (a banner can't carry an SF Symbol). It collapses
to just `"<project>"` when no branch is resolvable, or empty (the
`-subtitle` arg omitted) when `cwd` is empty. The separator is an em dash
` — `; line 3 uses the same dash for `name — summary`, but the two lines
are never confused in context.

This is a **bash re-read of `.git/HEAD`**, deliberately *not* a new
column in `agent-state.tsv`: the notify hooks (`notify-stop.sh`,
`notify-wait.sh`) run as Claude Code hooks with no access to the Python
plugin, the read is a couple of small file reads (no `git` subprocess,
no measurable cost next to the `jq`/`tail` the hook already does), and
it stays current at banner time with no async-hook ordering race. Idle
gets it the same way — `notify-idle.sh` already receives `cwd` as a
positional arg, so `idle_reminders.py` is **untouched**.

**Accepted trade-off:** unlike the submenu, the bash path has no
JSONL `gitBranch` fallback. If the session's `cwd` was deleted after it
finished, the subtitle shows project-only (or empty), where the submenu
would still recover the branch from the transcript. For freshly-finished
sessions — the only ones that notify — `cwd` virtually always exists.

### Line 3 — name — summary

For Awaiting and Idle, the message is strictly `<name> — <summary>`
(em-dash separator), or `<name>` / `<summary>` alone when only one field
is present, or **empty** when there is no marker turn at all. The old
random-phrase fallback is gone from line 3 — the phrase now lives in
line 1. Stop's message is unchanged (summary, else phrase).

## What stays the same

* Stop's title and message logic.
* The `say` text on all three surfaces (raw phrase → name → summary,
  separated by `_SAY_SEP`).
* Chimes (`Hero` / `Funk` / `Submarine`), quiet hours, the notify-audio
  master switch, the deeplink click target.
* All config knobs; no new keys.

## Acceptance

1. `bash -n hooks/_notify-common.sh hooks/notify-stop.sh
   hooks/notify-wait.sh hooks/notify-idle.sh` exits 0.
2. `_git_branch_from_cwd "<repo>"` echoes the branch checked out in
   `<repo>` (verified against `git -C <repo> rev-parse --abbrev-ref
   HEAD`); a linked worktree echoes the worktree's branch; a non-repo
   path echoes empty.
3. `_banner_subtitle "<repo-on-branch-X>"` echoes `"<basename> — ⎇ X"`
   (or `"… — ⓦ X"` for a linked worktree); `_banner_subtitle ""` echoes
   empty.
4. `notify-wait.sh` / `notify-idle.sh` pass a `-title` beginning with
   `❓ ` / `⚠️ ` respectively, and their `say` text contains no emoji
   (grep of the rendered args).
5. With no marker turn in the transcript, the Awaiting/Idle banner
   `-message` is empty (no phrase leaks into line 3).
6. Existing `unittest` suite stays green (Python untouched).
7. Manual GUI required before release: a real Stop / awaiting-prompt /
   idle reminder each shows `<project> / <branch>` on line 2, the right
   emoji on line 1 for awaiting/idle, and `say` speaks no emoji.

## Out of scope

* A config knob for the emoji or for the subtitle format — hardcoded.
* A JSONL `gitBranch` fallback in bash — see the accepted trade-off.
* Caching branch in `agent-state.tsv` — rejected (changes the TSV
  schema + Python parser for no measurable gain).
