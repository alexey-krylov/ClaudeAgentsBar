# Spec 0005 — Spoken summary

* Status: **Implemented in 1.1.2**
* Date: 2026-06-10

> **Superseded marker shape (see [spec 0007](./0007-session-title.md)):** the
> marker line is now **two-field** — `*-- Name - Summary*`, the name and
> summary split on the first `" - "`. On `Stop` the hook speaks the **summary**
> field (the part after the divider), not the whole line. A single-field line
> (`*-- Summary*`, no divider) keeps the original behaviour described below.

## Why

On `Stop` the notification hook speaks a random `notify_phrases` entry
("Done", "Your turn", …). That tells you *a* session finished but not
*which one did what*. The assistant already knows the answer — its last
message is right there in the transcript. If it ends each reply with a
known marker line, the hook can read that line aloud instead of a
generic phrase, turning the chime into a one-line spoken status.

Opt-in by behaviour, not by a switch: the feature is on by default but
**inert** until the assistant actually ends a reply with a marker line,
and it degrades silently to the old random phrase whenever the last line
isn't one.

## What the user configures

One knob, read by the Stop hook:

| Knob | Default | Meaning |
|---|---|---|
| `notify_summary_marker` | `"-- "` | Literal prefix of the reply's last line. Empty / `null` disables; absent key keeps the default. |

The agreed authoring convention is an **italic last line**:

```
*-- migrated the auth module, tests are green*
```

Italics keep the line unobtrusive on screen; the hook strips the
emphasis markers before matching. No menu toggle, no sidecar, no
plugin-side state — the whole decision is made in `hooks/notify-stop.sh`
from the config value plus the transcript, which is why nothing in the
Python package changed.

## How it works

`hooks/notify-stop.sh` reads the marker with `_cfg_string_or_null`
(spec 0001's nullable reader): an **absent** key yields the default
`"-- "`; an explicit `null` or `""` yields the empty string (off →
phrase). When the marker is non-empty and `$TRANSCRIPT` exists, it
extracts the spoken line from the **last non-blank line** of the
assistant's last message:

```bash
SUMMARY=""
if [ -n "$MARKER" ] && [ -n "${TRANSCRIPT:-}" ] && [ -f "$TRANSCRIPT" ]; then
    SUMMARY=$(jq -r 'select(.type=="assistant")
                     | .message.content[]? | select(.type=="text") | .text' "$TRANSCRIPT" \
        | awk -v m="$MARKER" '
            NF { last = $0 }
            END {
                sub(/^[*_]+/, "", last)   # strip leading markdown italic/bold
                sub(/[*_]+$/, "", last)   # strip trailing markers
                if (index(last, m) == 1) print substr(last, length(m) + 1)
            }' \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
fi

# Speech keeps the phrase and appends the summary; banner shows the summary alone.
SAY_TEXT="$PHRASE";    [ -n "$SUMMARY" ] && SAY_TEXT="$PHRASE. $SUMMARY"
BANNER_MSG="$PHRASE";  [ -n "$SUMMARY" ] && BANNER_MSG="$SUMMARY"
```

- `jq` streams the transcript in chronological order (no slurp), printing
  every assistant text block. The **last** one printed is the latest
  reply.
- `awk` keeps the last non-blank line (`NF{last=$0}`), so trailing blank
  lines don't matter, then strips leading/trailing `*`/`_` (handles `*…*`,
  `_…_`, `**…**`, `***…***`).
- `index(last,m)==1` is a literal prefix test — no regex, no escaping,
  Unicode-safe. `substr` takes the text after the marker; `sed` trims
  surrounding whitespace.
- Any miss (marker off / no transcript / no `jq` / last line isn't a
  marker line) leaves `SUMMARY=""` — a silent, non-fatal fallback under
  `set -u`.

The two surfaces then diverge deliberately:

- **Speech** (`say`) reads the random phrase **followed by** the summary
  (`"$PHRASE. $SUMMARY"`), so it lands as a sentence — "Done. Migrated
  the auth module" — rather than a bare fragment.
- **Banner** (`terminal-notifier -message`) shows the **summary alone**
  when present, so the on-screen line is the useful bit, not the filler
  phrase.

Both fall back to just `$PHRASE` when `SUMMARY` is empty. `SUMMARY` is
resolved before the speech/banner blocks, so the banner shows it even
when speech is muted (`notify_voice: "off"`, quiet hours' voice channel).

### Why the last line only

Earlier drafts took the *first* line starting with the marker (`tail -r`
+ `awk … exit`). Switched to the **last line of the latest reply**: it's
exactly what the user types ("on the last line goes `--` and the text"),
and it can't be fooled by an earlier `--`-ish line (a list item, a code
snippet, a markdown rule). The convention is "the closing line is the
summary", so that's what we read.

### Setting up Claude

The feature is inert unless the assistant ends replies with the marker.
The user adds a one-time instruction to `~/.claude/CLAUDE.md` (global) or
a project `CLAUDE.md` telling Claude to end every reply with an italic
line starting `--`. See
[docs/configuration.md § Spoken summary](../configuration.md).

## Why no menu toggle / sidecar

An earlier draft added a *Tools → Voice* selector with a
`phrase`/`summary` sidecar mirroring *Keep awake* / *Multi-workspace*.
Dropped: the marker knob alone already expresses "on with a default" and
"off" (`null`/`""`), and *the last line not being a marker line* is
itself the runtime fallback — so there's nothing a menu would add beyond
surface. Keeping the whole feature in the one hook that consumes it (no
plugin render code, no sidecar, no i18n) is the smaller, simpler design.

## Config

```jsonc
{
  "notify_summary_marker": "-- "   // null / "" to disable
}
```

`notify_summary_marker` is **not** a `Config` field — the plugin never
reads it, only the hook does, so it stays out of the Python schema (like
`notify_phrases` / `notify_voice`).

> **Updated in 1.1.3:** the Remind action ([spec 0006](./0006-remind.md))
> gates its submenu item on the marker, so the plugin now *does* read it —
> `notify_summary_marker` became a `Config` field. The hook still reads its
> own copy via `_cfg_string_or_null`.

## Edge cases

- **`--` mid-reply** — only the last non-blank line is checked, so an
  earlier `--` line is ignored.
- **Italic / bold wrappers** — leading & trailing `*`/`_` runs are
  stripped, so `*-- x*`, `_-- x_`, `**-- x**`, `***-- x***` and a bare
  `-- x` all work.
- **Trailing blank lines** — `NF` skips them; the marker line is still
  found.
- **Marker language / prefix drift** — compared byte-for-byte, so a
  `notify_summary_marker` that doesn't match what the assistant emits
  silently falls back to phrases. Documented as a lockstep rule.
- **`notify_voice: "off"` / quiet hours** — still suppress speech
  entirely. The marker changes *what* is spoken, never *whether*.
- **Threshold skip** — a sub-`notify_threshold_sec` turn is skipped
  before the marker logic runs, unchanged.
- **No `jq` / unreadable transcript** — extraction yields empty → phrase.

## Out of scope

- A menu toggle / sidecar (see *Why no menu toggle* above).
- Per-event marker (PermissionRequest has no result to summarise).
- Auto-injecting the CLAUDE.md instruction during `setup` — we don't edit
  the user's instruction files; documenting the snippet is enough.

## Verification

- Extraction `awk`/`sed` over synthetic last lines: `*-- x*`, `_-- x_`,
  `***-- x***`, bare `-- x` → text; no marker / marker-not-last → empty.
- Full `jq | awk | sed` over a multi-message JSONL: picks the latest
  reply's last line, ignoring an earlier marker line.
- `_cfg_string_or_null` semantics: absent → default; `null`/`""` → off.
- `bash -n` on the edited hook.
- Manual GUI: a real `Stop` whose reply ends with an italic `*-- …*`
  speaks that text; otherwise a phrase. Required before release —
  automated checks can't exercise `say` or the SwiftBar side.
