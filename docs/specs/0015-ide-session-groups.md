# Spec 0015 — IDE session groups (read-only mirror of the editor sidebar)

* Status: **Implemented**
* Date: 2026-08-24

## Why

Claude Code's IDE extension **2.1.241** lets the user file sessions into
named groups in the sidebar: create a group, rename it inline, drag
sessions in, collapse it. That's the user's own taxonomy of what they're
working on — and until now the menu bar knew nothing about it, showing one
flat list sorted only by live state.

The bar already carries two *local* classifiers, bookmarks (spec 0012) and
tags (spec 0013). Groups are a third — except this one isn't ours. It's
maintained in the editor, and we mirror it.

## Product decisions (locked with the user)

1. **Read-only. No renaming from the bar.** Grouping is owned by the IDE
   sidebar; the bar reflects it. See [ADR-0019](../adr/0019-ide-groups-read-only-globalstate.md)
   for why writing is off the table rather than merely unimplemented.
2. **Three display modes**, picked with `ide_groups_mode`:
   * `submenu` — one top-level entry per group, in the
     sidebar's own order, each header carrying per-state counters
     (`🟡 🟢2 · Backend`); its sessions live inside. Ungrouped sessions
     follow below a separator, under an *Ungrouped* label — the same layout
     the extension's sidebar uses.
   * `inline` (**default**) — flat list, group name prefixing the row title
     (`group · title`, dimmed, truncated) plus a submenu line with the full
     name under *Tags*, icon `square.stack`.
   * `off` — no grouping, and the *lookup* is skipped: no database opened.
3. **Counters, not just a count.** A folded group still has to answer "does
   anything in here need me", so the header shows one counter per live state
   rather than a single total — and a count of **one is left off**, because
   the circle already says there's one. `🟡 🟢 🔵` beats `🟡1 🟢1 🔵1`.
4. **The mode is switchable live**, from *Tools → Grouping* — "As in the
   extension" / "Name only" / "Off". The pick lands in
   `~/.claude/agent-state.ide-groups.mode` and overrides the config knob,
   which stays the first-launch default (the same sidecar-beats-config
   arrangement as *Keep awake* and *Multi-workspace mode*).
5. **Autodetect the editor, with a manual override** (`ide_state_db_paths`)
   for non-standard installs.
6. **Unreadable → render as before.** Groups are an optional decoration on
   the classifier, never a precondition for the menu.

## Where the data lives

Not under `~/.claude` — in the editor's own globalState:

```
~/Library/Application Support/<Editor>/User/globalStorage/state.vscdb
```

A SQLite database. Table `ItemTable`, key **`Anthropic.claude-code`**
(publisher case matters), value a JSON object holding every globalState key
the extension owns. Groups sit under one key per workspace:

```
sessionGroups:<realpath of the first workspace folder, NFC-normalised>
```

The extension strips a trailing `/.claude/worktrees/<name>` from that path
before using it as a key, so **worktree sessions inherit the main repo's
groups**. The value:

```json
[{"id": "…", "name": "backend", "collapsed": false, "sessionIds": ["…"]}]
```

Local sessions appear as bare ids; cloud sessions carry a `remote:` prefix.

`<Editor>` is `Code`, `VSCodium`, `Cursor`, `Windsurf`, `Positron`, or an
Insiders variant — one database per install, each with its own grouping.

## How the bar reads it

`sidecars.read_ide_groups()` → `{session_id: group_name}`, gated by
`core.ide_groups_mode()` — the *effective* mode (sidecar over config), never
the raw config field.

* **Which databases.** `ide_state_db_paths` when set; otherwise the known
  editors, led by the one owning `editor_url_scheme` — that's where the
  user's row clicks land, so its grouping wins a disagreement. The first
  database to claim a session id owns it.
* **No cwd matching.** Session ids are unique, so every workspace key folds
  into one flat map. The bar never has to reconcile a session's cwd with a
  workspace path (which is also what makes the worktree case free).
* **Read-only, short timeout.** `mode=ro` URI, `timeout=0.2` — the editor
  may hold the file, and the render tick can't block on someone else's
  writer. Measured cost across two real installs: **~2.8 ms** cold, 0.2–0.6 ms
  warm. No mtime cache: at that price it isn't worth the state.
* **Validation mirrors the extension's own** (`IDE_GROUPS_MAX` = 100 groups,
  `IDE_GROUP_SESSION_IDS_MAX` = 1000 ids, `IDE_GROUP_ID_MAX` = 200 chars,
  `IDE_GROUP_NAME_MAX` = 100 chars), plus two deviations of our own:
  * ids must match `core._SESSION_ID_RE` — this is what drops `remote:`
    sessions (no transcript on this machine) along with any junk;
  * names lose `|` and control bytes, which would otherwise corrupt the
    SwiftBar row they land in (`|` starts the parameter list, `\n` starts a
    new item).
* **Fail-soft everywhere**: missing file, locked or corrupt database, alien
  schema, non-JSON value, wrong shapes → `{}`, no exception, menu unchanged.

`render.collect_sessions` calls it once per tick and sets `Session.ide_group`.
There's no GC pass like the sidecars have: the data isn't ours to prune, and
a group naming a session we no longer render simply goes unused.

## Rendering

### `submenu` (default)

```
🟡 🟢2 · Backend                    ▸
  🟡 Release 1.4.2 · ⓦ · 3m
  🟢 Flaky test dig · 12m
🔵 · New group                       ▸
───────────────────────────────────
Ungrouped
⚪ Something else · 1h
```

`render._print_ide_group_blocks` lays this out. The header line **must** carry
a `| params` block — SwiftBar only expands an item into a submenu when it
parses one, and a bare header renders as inert text whose `--` children never
attach (verified the hard way). It carries `font=Menlo` and no `sfimage`: the
counters are the signal, and no SF Symbol read as "group" at menu size without
looking like debris. Groups come in the sidebar's
order via `Session.ide_group_order`; the header uses `sfimage=square.stack`
and `_group_header_label` builds the counters from the members' render
groups. Sessions are printed by the ordinary `_print_session_row` at
`indent="--"` — the same nesting the Bookmarks submenu uses, so every row
action works unchanged. Ungrouped sessions fall through to
`_print_flat_list`, which is the pre-existing top-level renderer.

### `inline`

```
🟡 ⓑ backend · Release 1.4.2 · ⓦ · 3m
```

The group prefix is dimmed (`core._ANSI_STALE`) and truncated to
`_IDE_GROUP_ROW_MAX` (16) characters, so a long name can't push the duration
off the right edge. The full name gets a submenu line under *Tags*:

```
--backend | font=Menlo color=#999999 sfimage=square.stack
```

Both the prefix and that line are `inline`-only: under `submenu` the
enclosing entry already names the group. The prefix survives
`show_state=False` (the Bookmarks submenu) — a group is a classification,
not a live state.

## Failure modes and their behaviour

| Situation | Behaviour |
|---|---|
| Extension older than 2.1.241 | No `sessionGroups:*` keys → no prefixes. |
| Editor never launched since the update | Same — the key appears on first group. |
| Mode `off` (config or *Tools → Grouping*) | No database opened, rows exactly as before. |
| Database locked / corrupt / gone | `{}`, rows exactly as before. |
| Anthropic changes the storage format | Type checks fail → `{}`, rows as before. |
| Session grouped in a *different* editor | Picked up too, unless another editor claimed the same id first. |

## Not in scope

* Creating, renaming, or moving groups from the bar — see ADR-0019.
* Grouping the menu *by* group (sections). Groups are a label; the menu's
  own sectioning stays keyed to live state, which is what the bar is for.
* `hiddenSessionIds` — the same globalState blob knows which sessions the
  user hid in the sidebar. Could gate the menu one day; not wired up here.
