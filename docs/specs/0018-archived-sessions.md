# Spec 0018 — Archived sessions (read-only mirror of the sidebar's archive)

* Status: **Implemented**
* Date: 2026-09-20

## Why

Claude Code's IDE extension lets you archive a session: it disappears from
the sidebar list. The extension calls the action *Archive*, stores the ids
under `hiddenSessionIds`, and reads them back through
`getArchivedSessionIds()`.

The bar kept showing them. Archiving is the user saying "I am done looking
at this"; a menu that keeps it on screen is not mirroring the editor, it is
arguing with it. Same reasoning that put `custom-title` above `ai-title`
([spec 0007](./0007-session-title.md)) and brought the sidebar's groups into
the menu ([spec 0015](./0015-ide-session-groups.md)).

## What it does

A session in the editor's archive is dropped from the menu — **unless** it
carries a tag ([spec 0013](./0013-tags.md)) or a bookmark
([spec 0012](./0012-bookmarks.md)).

That exception is the whole design. Tags and bookmarks are the bar's own
markers; the extension has no idea they exist. Setting one is an explicit
"keep this one in front of me", made *here*, and it outranks a decision made
in the sidebar. It also means the escape hatch is a click away and needs no
config edit: if a session vanishes from the menu and you want it back, tag
it.

The rule is applied unconditionally to the rest, including a session that is
🟡 working or 🔴 waiting. Archiving something still running is an odd thing
to do, and second-guessing it would leave the menu neither mirroring the
sidebar nor obeying a rule anyone could state. Notifications are unaffected
— they travel a different path, so a banner still arrives and its click
still lands in the session.

## Where the data lives

The same globalState database the groups come from:

```
~/Library/Application Support/<Editor>/User/globalStorage/state.vscdb
  → ItemTable, key "Anthropic.claude-code"
    → "hiddenSessionIds": ["<sid>", …]
```

Two differences from the groups blob:

* the list is **global**, not per-workspace, so every probed editor's
  archive folds into one set;
* the lookup is **not** gated on `ide_groups_mode`. Grouping and archiving
  are two features that happen to share a file; switching the group display
  off must not silently un-hide the archive. The gate here is
  `hide_archived_sessions`.

`sidecars._read_ide_globalstate` is now cached on the file's
`(path, size, mtime_ns)`, so the two readers cost one SQLite open per tick
between them rather than one each. **The cached dict must not be mutated by
either caller.**

## Validation

Mirrors what `_parse_ide_groups` already does with a blob we do not own:

* a value that is not a list → empty set, no error;
* every id checked against `core._SESSION_ID_RE`, which drops junk *and* the
  `remote:`-prefixed cloud sessions (no transcript on this machine, so
  nothing in the menu to hide anyway);
* total capped at `IDE_ARCHIVED_MAX` (1000);
* a missing, locked or corrupt database reads as an empty set, and the menu
  renders exactly as it did before the feature existed.

## The knob

| Key | Default | Meaning |
|---|---|---|
| `hide_archived_sessions` | `true` | Mirror the archive. `false` shows everything **and skips the lookup**. |

There is a knob at all — unlike `custom-title`, which just landed — because
this one *hides rows*. Somebody who archives aggressively and never tags
anything could lose sight of live work, and "my session disappeared" is a
bad thing to have no off switch for.

No menu toggle: the setting is a preference about mirroring, not something
you flip several times a day, and the per-session escape hatch (tag it) is
already in the row's submenu.

## Out of scope

* Writing to the archive. Archiving and unarchiving stay in the editor —
  the globalState database is rewritten wholesale by VS Code, so we only
  ever open it `mode=ro`. See
  [ADR-0019](../adr/0019-ide-groups-read-only-globalstate.md).
* A separate *Archived* submenu. If you want a session in the menu, tag or
  bookmark it; that is the same gesture with a visible result.
* Mirroring `sessionUnread:<workspace>`, the other new sidebar key. The bar
  has its own 🟢 / 🔵 read model and two competing notions of "unread" would
  only confuse.
