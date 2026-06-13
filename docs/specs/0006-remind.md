# Spec 0006 — Remind (re-speak a session's summary)

* Status: **Implemented in 1.1.3**
* Date: 2026-06-13
* Builds on: [0005 — Spoken summary](./0005-voice-summary.md)

## Why

Spec 0005 speaks a session's summary once, on `Stop`. But you often come
back to a session later from the menu bar and want to hear where it stands
without switching to the editor and reading. **Remind** re-speaks that
summary on demand, from the top of each row's submenu.

For a session you've left cold, the *latest* line alone often isn't enough
to reload context — you've forgotten what the thread was even about. So
Remind can optionally speak the session's **opening** summary first, then
the latest: *what it was about* → *where it is now*.

The cost model matters: the menu re-renders every 5 s, so the feature does
**zero** transcript parsing on the tick. The marker check that decides
enabled/disabled is a config read; the actual extraction happens only when
you click.

## What the user configures

| Knob | Default | Read by | Meaning |
|---|---|---|---|
| `notify_summary_marker` | `"-- "` | plugin + hook | Reused from 0005. Gates the item: a marker set → enabled; `null`/`""` → greyed-out. |
| `remind_recap_after_min` | `null` | action script | Minutes of quiet (since the last output) after which a click also speaks the **opening** summary. `null`/absent → latest only; `0` → always recap. |

No menu toggle, no sidecar — the marker already expresses on/off, and the
recap threshold is a plain config number like `notify_threshold_sec`.

## How it works

Two halves: the render decides whether the item is *clickable*; the action
script does the *work* on click.

### Render — gate only, no parsing

`claude_agents_bar/render.py` (`_print_session_row`) emits Remind as the
first submenu item. It never reads the transcript — it only checks the
marker, which is now a `Config` field:

```python
if core.CONFIG.notify_summary_marker:
    print(f"--{_t('menu.remind_session')} | "
          f"shell={_swiftbar_quote(str(remind_script))} "
          f"param1={_swiftbar_quote(session.id)} "
          f"param2={_swiftbar_quote(_t('remind.no_marker'))} "
          "terminal=false refresh=false "
          "sfimage=speaker.wave.2.fill sfcolor=systemBlue")
else:
    print(f"--{_t('menu.remind_session')} | color=#999999 sfimage=speaker.wave.2.fill")
```

- Enabled (marker set): runs `remind-session.sh <sid> <no-marker-hint>`.
  The hint (`remind.no_marker`, localised) is passed in so the *script*
  needn't know the UI locale.
- Disabled (marker `null`/`""`): a greyed-out, action-less line — the slot
  stays predictable rather than vanishing.

This is the only Python change, and it's why `notify_summary_marker`
**became** a `Config` field in 1.1.3 (spec 0005 had kept it hook-only).
`remind_recap_after_min` stays out of `Config` — only the script reads it,
like `notify_threshold_sec`.

### Click — extract, decide, speak

`bin/app/remind-session.sh` runs on click, off the render path. It
validates the id, globs the transcript
(`~/.claude/projects/*/<sid>.jsonl`), then pulls the session's first and
last summary with a shared helper in `hooks/_notify-common.sh`:

```sh
_summary_endpoints() {            # <transcript> <marker> → 0 or 2 lines
    /usr/bin/jq -rc '
        select(.type=="assistant")
        | [ .message.content[]? | select(.type=="text") | .text ]
        | join("\n") | split("\n")
        | map(select(test("\\S"))) | last // empty
    ' "$transcript" 2>/dev/null \
    | /usr/bin/awk -v m="$marker" '
        { line=$0; sub(/^[*_]+/,"",line); sub(/[*_]+$/,"",line)
          if (index(line,m)==1) { s=substr(line,length(m)+1)
            sub(/^[[:space:]]+/,"",s); sub(/[[:space:]]+$/,"",s)
            if (!fs){first=s;fs=1}; last=s } }
        END { if (fs){ print first; print last } }'
}
```

- `jq` reduces **each** assistant turn to its last non-blank line, so the
  marker test runs on every turn's *closing* line — the first match is the
  opening summary, the last is the current one. (Spec 0005's
  `_extract_summary` differs: it only ever looks at the very last reply.
  Both live in `_notify-common.sh`; 0005's is unchanged and still backs
  `notify-stop.sh`.)
- Per-turn closing-line testing is what makes a `-- ` *mid-reply* safe — it
  can't be the closing line of a turn, so it can't false-match.
- `first == last` when the session has exactly one summary; the caller
  de-dupes.

The recap decision is a pure-shell comparison against the transcript mtime
(the moment of the last output — no extra timestamp parse):

```sh
RECAP_AFTER=$(_cfg_int "remind_recap_after_min" "")
WANT_FIRST=false
if [ -n "$RECAP_AFTER" ] && [ "$RECAP_AFTER" -ge 0 ] 2>/dev/null && [ -n "$TRANSCRIPT" ]; then
    TS=$(/usr/bin/stat -f %m "$TRANSCRIPT" 2>/dev/null || echo "")
    [ -n "$TS" ] && { AGE_MIN=$(( ($(date +%s) - TS) / 60 ))
                      [ "$AGE_MIN" -ge "$RECAP_AFTER" ] && WANT_FIRST=true; }
fi
```

| `remind_recap_after_min` | time since last output | spoken |
|---|---|---|
| unset / invalid | any | latest only |
| `N` (≥0) | `< N` min ("in the flow") | latest only |
| `N` (≥0) | `≥ N` min ("cold") | opening, then latest |
| any | — (single summary) | that one summary, once |
| any | — (no summary, marker on) | the localised hint |

The phrases are then spoken fire-and-forget, with a short pause **between**
the two (no leading chime, so nothing before the first):

```sh
( __i=0; for __p in "${PHRASES[@]}"; do
      [ "$__i" -gt 0 ] && sleep 0.4
      _say "$__p"; __i=$((__i+1)); done ) >/dev/null 2>&1 &
disown
```

`_say` reuses the notification voice (`notify_voice`), so a reminder sounds
identical to the Stop notification. Being an explicit click, it speaks even
under *Banner only* / `notify_voice: "off"` (which mute only the
*automatic* speech); `"off"` falls through to the system default voice.

### Why on click, not on the tick

An earlier iteration extracted the summary in `build_session` every tick
(`sidecars.last_assistant_summary`), parsing each session's transcript tail
— the costliest per-tick reader. It was moved to click time: the text is
needed only then, opening the submenu is native AppKit, and the marker
gate is enough to render the item. A TSV-sidecar variant (write the summary
from a hook, read it on the tick) was rejected — after the move the tick
cost is already zero, so it bought nothing for the added hook surface,
escaping, gc, and "stale until next Stop" semantics.

## Config

```jsonc
{
  "notify_summary_marker": "-- ",     // null / "" → Remind item greyed-out
  "remind_recap_after_min": null      // minutes; null → latest only; 0 → always recap
}
```

## Edge cases

- **Single summary** — `first == last`; spoken once even with recap on.
- **No summary, marker on** — speaks the localised `remind.no_marker` hint
  instead of going silent.
- **Marker off (`null`/`""`)** — item renders greyed-out, no action.
- **`-- ` mid-reply** — only each turn's closing line is tested, so it's
  ignored (same guarantee as 0005, applied per turn).
- **Italic/bold wrappers** — leading/trailing `*`/`_` stripped (`*-- x*`,
  `_-- x_`, `**-- x**`, bare `-- x`).
- **`notify_voice: "off"` / Banner only** — still speaks; an explicit click
  overrides the *automatic*-speech mutes.
- **Big transcript** — `jq` reads the whole file (the opening summary is at
  the top), but only on click and in the background, so it never blocks.
- **No `jq` / unreadable transcript** — `_summary_endpoints` yields nothing
  → the hint (or quiet exit if the hint is empty).
- **Summary language** — spoken verbatim from the transcript; never
  translated (the plugin is offline). One voice for both phrases.

## Out of scope

- Per-tick freshness — the menu shows no summary text, only the gated item.
- A visual/banner surface — Remind is audio-only by design.
- A TSV/sidecar cache of summaries (see *Why on click* — rejected).
- Translating or re-voicing summaries per part.

## Verification

- `_summary_endpoints` over a multi-summary JSONL → two lines (first|last);
  single-summary → equal lines; none → empty.
- Recap threshold against a real mtime for unset / `0` / large values →
  `WANT_FIRST` false / true / false.
- Phrase-selection emulation: unset → latest only; `0` → opening then
  latest.
- `bash -n` on the edited hook + action script; `config.example.json`
  parses; `python -m unittest` regression (Python surface limited to the
  render gate + the `notify_summary_marker` `Config` field).
- Manual GUI (required before release — `say` and the SwiftBar side aren't
  scriptable): set `remind_recap_after_min: 30`, click Remind on a
  >30-min-cold session (two phrases) and a fresh one (one); empty marker →
  greyed-out item.
