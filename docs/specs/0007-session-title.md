# Spec 0007 — Session title from a two-field marker

* Status: **Implemented**
* Date: 2026-06-13

## Why

Claude Code names sessions with an auto-generated English `ai-title`. The
menu showed that, so a Russian-speaking user reading the dropdown got
English topics that rarely matched how they think about the work. There
is no `/rename` in the VSCode extension (only the CLI), so the title
can't be set by hand from where this user works.

Spec 0005 already established a convention the assistant follows every
reply: an italic closing line `*-- Summary*` that the `Stop` hook speaks.
We extend that one line to also carry a short **session name**, and read
that name as the menu title. One authoring habit, three payoffs: a
meaningful menu title, the spoken Stop summary (unchanged), and — new —
the session name spoken when a session is waiting on you.

## The marker line

```
*-- Name - Summary*
```

* **Prefix** — `notify_summary_marker` (default `"-- "`), the same knob
  spec 0005 introduced. Detects the marker line.
* **Divider** — the first `" - "` (a lone hyphen padded with spaces).
  Splits the remainder into **name** (before) and **summary** (after).
  Splitting on the *first* occurrence means a hyphen inside the summary
  is harmless.
* **Single-field fallback** — a line with no `" - "` after the prefix is
  the legacy spec-0005 form: empty name, whole remainder is the summary.

Emphasis wrappers (`*`/`_` runs) are stripped first; the prefix is
compared byte-for-byte (no regex, Unicode-safe); only the **last
non-blank line** of a reply is tested.

## Who consumes which field

| Surface | Field | Where |
|---|---|---|
| Menu title | **name** | `sidecars._latest_session_title_from_response` → `TranscriptMeta.session_title` → `display_title` (ahead of `ai_title`). |
| `Stop` notification | **summary** | `hooks/notify-stop.sh` via `_extract_summary` (now drops the name field). |
| `Remind` action | **summary** | `bin/app/remind-session.sh` via `_summary_endpoints` (drops the name field). |
| Awaiting (`PermissionRequest`) | **name + summary** | `hooks/notify-wait.sh` via `_marker_fields_latest`. |

The split rule lives in two places that must agree byte-for-byte:
`sidecars._parse_marker_line` (Python, render side) and the `awk` in
`hooks/_notify-common.sh` (shell, hook side). Both use the same prefix
(`notify_summary_marker`) and the same `" - "` divider.

## Render-side cost

`session_title` is needed at render time (the menu draws every tick), so
unlike Remind it can't be a click-only parse. It's kept cheap:

* **Gated on the marker.** `_latest_session_title_from_response` returns
  `""` immediately when `notify_summary_marker` is `null` / `""`, so a
  user who hasn't opted in pays nothing.
* **No extra disk read.** It scans the already-cached JSONL tail
  (`_read_jsonl_tail`), shared with the other tail signals.
* **Byte prefilter.** Lines without `"type":"assistant"` are skipped
  before `json.loads`.
* **Latest wins.** It keeps the *last* reply that carried a name, so the
  title tracks the current turn instead of a stale earlier marker.

## Awaiting: name + summary from the last completed turn

At a `PermissionRequest` the current turn is mid-flight — it hasn't
emitted its closing marker yet — so the *absolute* last line is usually
not a marker line. `_marker_fields_latest` therefore scans **per turn**
(each turn's closing non-blank line) and keeps the last turn that *was* a
marker line, yielding the most recent completed name + summary. Speech
reads `phrase → name → summary`; the banner shows `name — summary`. With
no marker turn yet (or the marker disabled) both fields are empty and the
hook falls back to the phrase alone, exactly as before.

`Stop` keeps the stricter spec-0005 rule (only *this* reply's closing
line), so a finished session never announces a stale summary as its
result; the awaiting path deliberately relaxes it because there is no
fresh marker to read mid-prompt.

## Backward compatibility

* Single-field `*-- Summary*` lines keep working: no name (menu falls
  through to `ai_title`), summary spoken on `Stop` / `Remind` as before.
* `notify_summary_marker: null` / `""` disables every surface — menu
  title, Stop/awaiting speech, and the Remind item — unchanged from 0005.

## Verification

* `_parse_marker_line` / `_session_name_from_reply` unit tests: two-field
  split, first-divider-only, single-field → empty name, emphasis
  wrappers, closing-line-only, empty marker.
* `read_transcript_meta` tests: latest marker name wins and becomes
  `display_title`; single-field falls through to `ai_title`; disabled
  marker skips the parse.
* `awk` helpers exercised over a synthetic transcript: Stop summary,
  awaiting name+summary from the previous completed turn (in-flight turn
  has no marker), Remind endpoints.
* `bash -n` on the edited hooks; full `unittest` suite green.
* Manual GUI required before release (automated checks can't exercise
  `say`, the banner, or the live menu): a real `Stop` speaks the summary;
  a real permission prompt speaks phrase + name + summary; the menu row
  shows the Russian name.

## Out of scope

* A separate config knob for the name/summary divider — it's a fixed
  `" - "` convention, documented in `docs/configuration.md`.
* Idle (`Notification`) speech — the `Notification` event still only
  updates session state; no audio is attached.
