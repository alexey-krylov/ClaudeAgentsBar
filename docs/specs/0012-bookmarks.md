# Spec 0012 — Bookmarks (pin sessions so they survive the render window)

* Status: **Implemented** — ships in 1.4.0 (with Tags, spec 0013)
* Date: 2026-07-03

## Why

The menu is a *live* view: `collect_sessions` only builds rows for
transcripts touched within `Config.window_sec` (default 3 h) — see
`iter_active_jsonls` in `claude_agents_bar/render.py:60`. When a session
goes quiet past that window it drops out of the dropdown and there's no
way to get it back short of touching the transcript. A session you left
mid-thread and want to return to tomorrow is simply *gone*.

**Bookmarks** let the user *pin* any session. A pinned session is
guaranteed a slot under a new top-level **Bookmarks** submenu — directly
below the *Refresh* button — regardless of the render window, and it
carries the **same full submenu it has in the live list** (Remind, Mark
as read, Forget, Delete…, Reveal in Finder, project/branch, model,
context usage, subagents). Nothing about the per-session submenu changes;
Bookmarks reuse it verbatim.

The pin is a pointer, not a snapshot: the row is rebuilt from the
transcript on disk each tick (`build_session`), so a bookmarked session
that's still running shows *live* state, and one that's cold shows its
last state — exactly like the live list. See *Rebuild, don't snapshot*.

## What the user does

* **Pin / unpin** — every session's submenu gets a **Bookmark**
  checkbox item (checked ⇔ pinned). Click toggles it. Same checkbox
  pattern as *Multi-workspace* / *Usage monitor* — a native SwiftBar
  `checked=` reflecting the live sidecar state, flipped by writing the
  opposite value.
* **Find pinned sessions** — open **Bookmarks** (top-level item under
  *Refresh*). Each entry is the session's full submenu, one level deep,
  prefixed with the date it was bookmarked. Unpin from here (the same
  checked *Bookmark* item, now inside the pinned entry) or from the live
  row — both write the same sidecar.
* **No bookmark marker in the live list.** A pinned session isn't flagged on
  its live-list row — an `sfimage` bookmark icon would have to lead, ahead of
  the state circle (SwiftBar can't place an image after text), and an inline
  emoji was rejected. Pinned sessions are surfaced under the *Bookmarks*
  submenu instead. Inside that submenu each entry renders **neutral** — no
  state circle or state colour (`show_state=False`), since live status is noise
  in a pinned list.

There is **no** new config knob. The feature is always on; the Bookmarks
top-level item is **hidden while the list is empty** (like the usage
line), so it costs nothing until first used.

## How it works

Three parts: a sidecar recording *what's pinned*, a toggle action that
*writes* it, and render code that *reads* it and reuses the existing
session-row renderer under a new top-level submenu.

### The sidecar — `agent-state.bookmarks`

A two-column TAB file, the same shape and locking as `agent-state.forget`
(`~/.claude/agent-state.forget`):

```
<session_id>\t<bookmarked_at>
```

`bookmarked_at` is unix epoch seconds (when the pin was created). New
constant `BOOKMARKS_PATH` + `BOOKMARKS_LOCK_DIR` in
`claude_agents_bar/core.py` (next to `FORGET_PATH`, core.py:58).

* **Reader** — `sidecars.read_bookmarks() -> dict[str, int]` mirrors
  `read_forget` (sidecars.py:532): parse TSV, coerce col 2 to int, drop
  malformed rows, return `{}` on `OSError`. Pure parser tested in
  isolation.
* **GC (auto-prune)** — a bookmark whose transcript no longer exists on
  disk is dead and is dropped, reusing the orphan-cleanup already run in
  `collect_sessions` (render.py:262): `set(bookmarks) - _live_session_ids()`
  → `gc_bookmarks(orphans)` via the shared `_gc_two_col_sidecar`
  (sidecars.py:391). This covers both *Delete…* (transcript removed by
  `delete-session.sh`) and any external cleanup — the pin vanishes with
  the session, no tombstone.

### The toggle — `bin/app/bookmark-set.sh`

Invoked from the *Bookmark* checkbox as `/bin/bash <script> <sid> <on|off>`
(same executable-bit-independent invocation as `multi-workspace-set.sh`,
render.py:1105). `on` → awk-upsert `<sid>\t<now>` into the sidecar (add
only if absent, so re-pinning doesn't reset the date); `off` → delete the
`<sid>` row. Both take the `mkdir`-based lock (`.lock.d`) like
`forget-session.sh`. `refresh=true` so the menu redraws with the new
checkmark on the next tick.

### Render — reuse the session row, one level deeper

`_print_session_row` (render.py:575) hardcodes the `--` submenu prefix.
To render a pinned session *inside* the Bookmarks submenu it must sit one
level down (row at `--`, its items at `----`). Two small, backward-safe
changes:

1. **Thread an indent through the row renderer.** Add
   `_print_session_row(session, *, indent: str = "", bookmark_age: str | None = None)`.
   Every emitted line is prefixed with `indent`; the existing submenu
   `--` become `indent + "--"`. `indent=""` (the live list) is byte-for-byte
   unchanged. Bookmarks pass `indent="--"`. `_print_subagent_block`
   (render.py) takes the same `indent`.
2. **Add the Bookmark checkbox item** to the row's submenu (right after
   *Remind*). Its state reads off `session.is_bookmarked` — a new `Session`
   field set on the live list by `collect_sessions` and forced `True` on the
   rows the Bookmarks block rebuilds, so the one item is correct in both
   places:
   ```python
   print(f"{indent}--{_t('menu.bookmark')} | "
         f"shell=/bin/bash "
         f"param1={_swiftbar_quote(str(bookmark_set_script))} "
         f"param2={_swiftbar_quote(session.id)} "
         f"param3={'off' if session.is_bookmarked else 'on'} "
         f"checked={'true' if session.is_bookmarked else 'false'} "
         "terminal=false refresh=true sfimage=bookmark.fill sfcolor=systemYellow")
   ```
   Clicking it hands `bookmark-set.sh` the opposite value, so unpinning works
   from either the live row or the Bookmarks entry through the one action.

**The Bookmarks block** — `_print_bookmarks_block(live_sessions)` (it takes
`now` from `int(time.time())` internally, like `_print_subagent_block`),
called from the top of `_print_footer` immediately after the *Refresh*
line, before *Tools*:

```
Refresh                    ← existing
Bookmarks ▸                ← new, hidden when empty
  auth-refactor · 3m ▸     ← neutral: no state circle/colour (show_state=False)
    ★ Added 3m ago         ← passive date leaf (----), relative age
    Bookmark ✓             ← the toggle, checked (----)
    Remind
    Forget …               ← the full existing submenu, verbatim
  parse-bug · 2h ▸
    ★ Added 2h ago
    …
Tools ▸                    ← existing
```

Logic:

1. `bm = sidecars.read_bookmarks()`; if empty → emit nothing, return.
2. For each pinned `sid`:
   * **Reuse the live build if we already have it.** `live_sessions` was
     just built by `collect_sessions`; index it by id. A bookmarked
     session that's in-window is already parsed — don't parse it twice.
   * **Otherwise build on demand.** Glob `~/.claude/projects/*/<sid>.jsonl`;
     if found, `build_session(jsonl, sidecar, clicks, subagents, now)`
     (the same call `collect_sessions` makes, render.py:282) — this is
     what resurrects an out-of-window session. If **not** found, the
     transcript is gone → the orphan GC above already dropped it; skip.
3. Sort the resulting sessions by `(group.order, -last_event_ts)` — the
   same key as the live list (render.py:304), so ordering is **by session
   activity** (active/fresh first, coldest last), not by pin date.
4. Print the `Bookmarks` header, then each session via
   `_print_session_row(s, indent="--", show_state=False, bookmark_age=_humanize_bookmark_age(now - bm[s.id], lang))`
   — `show_state=False` drops the state circle, state row colour, and state-
   coloured age, so pinned entries render neutral.

The date leaf (first sub-item, `indent + "--"` → `----`) is passive grey:
`{indent}--{_t('bookmark.added', when=bookmark_age)} | font=Menlo color=#999999 sfimage=clock`.

### The bookmarked-date format

Relative age via a small `_humanize_bookmark_age(seconds, lang)` helper in
`render.py`, next to `_format_until` — no weekday/month name tables:

```python
def _humanize_bookmark_age(seconds, lang):
    if seconds >= 86400:                       # a day or more → whole days
        return _t_for("age.days", lang, n=(seconds + 43200) // 86400)
    return _humanize_age(max(0, seconds), lang)  # sub-day → "3м" / "2ч 20м"
```

Sub-day reuses the existing `_humanize_age` (`core.py`), so a fresh pin reads
finely (`5м` / `2ч 20м`) rather than the coarse `1ч` that `_format_until`
would floor it to; a day or more rolls into whole days (`8д`) so an old pin
never reads as an unwieldy `192ч`. Result:
`_t("bookmark.added", when=…)` → `"Добавлено 3м назад"`.

`bookmark.added` = `"Added {when} ago"` / `"Добавлено {when} назад"` — the
only date-related i18n key, added to every locale. English is
source-of-truth; the fallback chain (`requested → primary_subtag → en →
key`) covers gaps. (An absolute "weekday, then D month" format was
considered and dropped as too much i18n surface for the payoff — relative
age is what the rest of the menu already speaks.)

### Opening a pinned session acknowledges it

The pinned row's main line is the **same** `open-session.sh` action as in the
live list — it records a click into `agent-state.clicks` before opening the
editor. So opening a bookmark that had gone cold (out of the window, would
otherwise classify ⚪ *stale*) records a click past its last `Stop`, and on the
next tick `_classify` sees `effective_click_ts` and paints it 🔵
*acknowledged*. Nothing bookmark-specific is needed: reusing the row gives
"opening a pin marks it read" for free.

### Rebuild, don't snapshot

A bookmark stores **only** `sid + date`. The row (title, state, branch,
context, subagents) is rebuilt from the transcript every tick via
`build_session`. Rejected alternative: snapshotting the rendered row into
the sidecar at pin time. That would freeze a pinned session's state (a
still-running pin would show stale title/branch/context), duplicate the
`HookSnapshot`/transcript-meta model in a second store, and need its own
invalidation. Rebuilding is what makes a pinned *live* session show live
state for free — the whole point of pinning something you're still
working in.

## Cost

`build_session` parses a transcript tail (title, usage tokens, git
branch) — the per-session tick cost the live list already pays. Bookmarks
add that cost **only for pinned sessions that are out-of-window** (in-window
ones are reused from `live_sessions`, parsed once). Bounded by how many
sessions the user has pinned, which is inherently few. No per-tick cost
when the list is empty (single `read_bookmarks` returning `{}`). This
keeps the tick within the project's "no expensive parsing on the 5 s
tick" rule — the extra parse is proportional to a small, user-controlled
set, and re-uses the existing per-session parser rather than adding a new
one.

## i18n

Three new keys, all 8 locales: `menu.bookmarks` (header), `menu.bookmark`
(checkbox), `bookmark.added` (`"Added {when} ago"` / `"Добавлено {when}
назад"`, `{when}` filled by `_humanize_age`).

## Icons (SF Symbols)

All from the system SF Symbols set (rendered by SwiftBar on macOS 11+):

* **Bookmarks** header + **Bookmark** checkbox — `bookmark.fill` (the
  filled bookmark; ties the section and the toggle together visually,
  the way *Tools* = `wrench.adjustable.fill` and *Stats* =
  `chart.bar.fill` do).
* **Added … ago** date leaf — `clock` (it's a time annotation).

Alternatives if `bookmark.fill` reads too heavy next to the neighbouring
icons: `bookmark` (outline) or `bookmark.circle.fill`. Final pick is a
one-line GUI check at implementation time (icons can't be judged from
tests).

## Config

None. Always on; hidden when empty. (Contrast
`use_session_titles_for_menubar`, spec 0007, which *is* gated — that one
changes existing behaviour; Bookmarks only *adds* an inert-until-used
surface.)

## Acceptance

1. `read_bookmarks` round-trips a valid two-column file; drops rows with
   `<2` columns / non-numeric date; returns `{}` on a missing/unreadable
   file. `_gc_two_col_sidecar`-backed `gc_bookmarks(ids)` removes exactly
   those ids and leaves the rest byte-identical.
2. `bash -n bin/app/bookmark-set.sh` exits 0. `on` upserts `<sid>\t<ts>`
   (and a second `on` does **not** overwrite the original date); `off`
   removes the row; concurrent writers serialize on `.lock.d`.
3. Auto-prune: a bookmarked `sid` with no `~/.claude/projects/*/<sid>.jsonl`
   is dropped by the orphan pass (`set(bookmarks) - _live_session_ids()`)
   and never rendered.
4. `_print_session_row(s, indent="")` output is unchanged from today
   (existing `test_render` assertions pass verbatim); `indent="--"` shifts
   the row to `--` and every submenu line to `----`. With
   `s.is_bookmarked=True` the *Bookmark* item emits `checked=true`.
5. `_print_bookmarks_block`: empty sidecar → **no** output; N bookmarks →
   a `Bookmarks` header + N entries sorted by `(group.order,
   -last_event_ts)`, each led by the localized date leaf; an in-window
   pinned session is **not** re-parsed (reused from `live_sessions`); an
   out-of-window pinned session **is** built via `build_session`; an
   orphan sid is skipped.
6. The date leaf renders `bookmark.added` with `{when}` from
   `_humanize_bookmark_age(now - bookmarked_at, lang)` — a pin 3 min old
   shows `Добавлено 3м назад` (RU) / `Added 3m ago` (EN); sub-day reuses
   `_humanize_age`, a day or more rolls to whole days (`8d`, half up) so an
   old pin never reads as `192h`.
7. The *Bookmark* checkbox in the **live** row reflects real state:
   `checked=false` + `param3=on` when unpinned, `checked=true` +
   `param3=off` when pinned.
8. Opening a pinned row records a click (reused `open-session.sh`), so an
   out-of-window pin classifies 🔵 *acknowledged* on the next tick — no
   bookmark-specific ack path.
9. Every new i18n key resolves in all 8 locales (no key falls through to
   the literal).
10. Full `unittest` suite green (including the above).
11. **Manual GUI required before release** (SwiftBar clicks, checkmarks,
    and submenu nesting aren't scriptable): pin a live session → it
    appears under *Refresh* with its full working submenu; let it fall
    out of `window_sec` (or shorten it) → it **persists** in Bookmarks
    with Remind/Forget/Reveal/context all functional; the date leaf shows
    the relative pin age (`Added 3m ago`); unpin from either the live row
    or the Bookmarks entry → it disappears from Bookmarks and the checkbox
    clears; *Delete…* a pinned session → the bookmark auto-prunes.

## Out of scope

* **Inline search / a filter field in the menu.** SwiftBar's plugin API
  is line-based static rendering — there is no interactive text-input
  widget in an NSMenu dropdown, so an inline search box isn't possible.
  Dropped per that constraint. (macOS already gives a partial substitute
  for free: with the menu open, type-to-select jumps to the first item
  whose title matches the typed letters.) If pinned lists ever grow
  unwieldy, revisit with a *grouping* or *cap*, not an input field.
* **Snapshotting session state at pin time** — rejected; see *Rebuild,
  don't snapshot*.
* **Ordering by pin date** — the list is ordered by session activity per
  the product decision; the pin date is shown as an annotation, not a
  sort key.
* **Folders / tags / notes on a bookmark** — a bookmark is `sid + date`,
  nothing more.
* **Syncing bookmarks across machines** — the sidecar is local, like
  every other `agent-state.*` file.
* **A config knob** — always on, hidden when empty (see *Config*).
