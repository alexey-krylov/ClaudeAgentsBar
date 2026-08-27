# 0019. Mirror IDE session groups read-only out of the editor's globalState

* Status: Accepted
* Date: 2026-08-24

## Context

Claude Code's IDE extension 2.1.241 added session **groups** to the sidebar:
named buckets the user drags sessions into, renames inline, and collapses.
We want them in the menu bar (spec 0015). The obvious follow-up ask — "and
let me rename a group from the bar" — turns out to be the interesting part.

Where the data actually lives, established by unpacking the 2.1.241 vsix:

* `~/Library/Application Support/<Editor>/User/globalStorage/state.vscdb`,
  a SQLite database; table `ItemTable`, key `Anthropic.claude-code`, value a
  JSON blob of the extension's whole globalState.
* Groups live under `sessionGroups:<workspace realpath>` inside that blob,
  as `[{id, name, collapsed, sessionIds}]`.
* The extension reaches them through `context.globalState.get/update` — the
  standard VS Code extension storage API.

Nothing about this is a documented interface. There is no file under
`~/.claude`, no hook payload, and no CLI surface that exposes grouping.

Options considered:

1. **Don't support groups.** Cheap, and leaves the user's own taxonomy
   invisible in the very place they glance at all day.
2. **Read *and* write the globalState database.** Full parity with the
   sidebar, including renaming from the bar.
3. **Read the database; keep every mutation in the IDE.**
4. **Ignore the IDE and build our own grouping**, a third local classifier
   next to bookmarks and tags.

## Decision

**Option 3.** The bar opens the editor's globalState database read-only
(`file:…?mode=ro`, `timeout=0.2`), folds every workspace's groups into one
`{session_id: group_name}` map, and renders it. It never writes.

Creating, renaming, moving, and collapsing stay in the IDE sidebar.

## Consequences

**Why not write (option 2).** VS Code keeps globalState **in memory** and
writes the whole blob back on its next `update()`. Any row we wrote while
the editor is running would be silently overwritten by the next unrelated
setting change — and we'd be doing concurrent writes into a SQLite file
owned by another process, against a schema nobody promised us. The failure
mode isn't "the rename doesn't stick", it's "the rename doesn't stick
*sometimes*, and in the bad case we corrupt the user's editor state". A
feature that works by luck is worse than one that's honestly read-only.

**Why not our own grouping (option 4).** The user asked for the IDE's
grouping *as is*. A parallel taxonomy that looks identical but drifts from
the sidebar is a worse outcome than mirroring, and we already have two
local classifiers for anything the bar wants to say on its own.

**What we accept:**

* **The format can change without warning.** It's someone else's internal
  storage. Mitigated by validating every field (mirroring the extension's
  own limits) and failing soft to `{}` — a format change costs the prefix,
  not the menu.
* **A rename in the IDE lands on the next tick**, up to 5 s later. Fine for
  a label.
* **Multi-editor installs need a tie-break.** The editor owning
  `editor_url_scheme` is probed first, because that's where row clicks land.
* **Reading a foreign database on every tick.** Measured at ~2.8 ms cold and
  0.2–0.6 ms warm across two real installs — cheap enough that no mtime
  cache is warranted, and gated by `show_ide_groups` for anyone who'd rather
  the bar not touch their editor's files at all.

**What it buys:** the sidebar stays the single writer, so there's exactly one
source of truth and no reconciliation logic. The bar's copy can never be
stale in a way that outlives one tick, and can never damage what it mirrors.
