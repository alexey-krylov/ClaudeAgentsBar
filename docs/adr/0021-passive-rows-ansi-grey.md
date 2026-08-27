# 0021. Grey passive rows with ANSI, not `color=`

* Status: Accepted
* Date: 2026-08-27

## Context

Roughly a third of the dropdown is read-only text: the branch, model and
context-left lines under a session, the IDE group name, subagent trails, the
two usage lines under *Statistics*, the *Notifications* and *Keep awake*
status headers. All of them were rendered as `font=Menlo color=#999999` (or
`#888888`) with no `shell=` / `href=` / `refresh=true`, i.e. nothing happens
when you click them.

They highlighted under the cursor anyway, and took arrow-key focus, which
reads as "this does something" and makes the menu noisier to scan. SwiftBar
has no `disabled=` parameter, and the earlier reading — that a non-selectable
item is simply impossible (see the "Ungrouped" label decision in 1.5.0) — was
wrong.

Reading SwiftBar 2.1.1 (`SwiftBar/MenuBar/MenuBarItem.swift`,
`MenuLineParameters.swift`) shows what actually governs it:

```swift
private func configureAction(on item: NSMenuItem, for params: MenuLineParameters) {
    if params.hasAction || params.color != nil {
        item.target = self
        item.action = #selector(perfomMenutItemAction)
    } …
    } else {
        item.target = nil
        item.action = nil
    }
}
// hasAction = href != nil || bash != nil || stdin != nil || refresh
```

`NSMenu.autoenablesItems` is left at its default `true`, so an item with no
target and no action is inert: no highlight, no click, no keyboard focus. A
passive row is therefore already achievable — it just must not carry `color=`.

The `params.color != nil` clause is not a statement that colour implies
action. It exists because a custom foreground colour is unreadable against the
blue selection highlight, so `menu(_:willHighlight:)` repaints the title while
the row is selected — and `willHighlight` only fires for items that have an
action. Our grey was buying a highlight it never wanted.

Under `ansi=true` SwiftBar ignores `color=` entirely (`atributedTitle`:
`if !params.ansi { addAttributes(.foregroundColor…) }`) and takes the colour
from SGR escapes in the text instead. Same grey, no action, no highlight.

## Decision

Passive rows carry `ansi=true` (the `render.PASSIVE` fragment) and get their
grey from `render._dim()`, which wraps the label in `core._ANSI_DIM`
(`\x1b[38;5;245m`). `color=` is reserved for rows that either have an action
anyway or carry a status worth a selectable row: a cwd collision (`#cc0000`),
a worktree checkout (`#1f7a1f`), a subagent still in flight (`#cc7700`).

`_dim()` rewrites nested resets to re-open the grey rather than fall back to
the default label colour, so a self-colouring span (the usage bar, a warn
percentage) can sit inside a dimmed line without bleaching the rest of it.

Menu-bar *title* lines keep `color=` — selection doesn't apply there.

`tests/test_render.py::TestPassiveRowsAreUnselectable` enforces the invariant:
a rendered row with no action may only carry `color=` if it is one of the
three status colours.

### Leaf rows only

A row that owns a submenu is out of scope: SwiftBar expands a parent only when
it carries an action, which is why a group header ships a no-op
`shell=/usr/bin/true` "so the item is born enabled". Rendering such a row
passively makes its submenu unopenable — found the hard way on the subagent
rows, whose model and tool-trail children stopped being reachable. Those rows
keep `color=` (amber in flight, grey once stopped); their header and their
leaf children are dimmed as usual.

So: passive treatment applies to leaves. A parent is selectable by necessity,
and that selection is honest — hovering it does something.

## Consequences

* Three constraints come with the ANSI route, all load-bearing:
  * **Real `\x1b` bytes only.** SwiftBar runs `unescape()` on the title
    *before* the ANSI parser, so a literal `\e` loses its backslash and prints
    as text. (Verified in the live bar; the first probe rendered
    `e[38;5;245m…` on screen.)
  * **Stay on indices 232–255 or the 16 base codes.** SwiftBar's
    `colorForAnsi256ColorIndex` computes the 6×6×6 cube (16–231) with a bug in
    the blue channel (`i % 36` where it wants `i % 6`); the greyscale ramp is
    correct — `rgb = (index - 232) * 10 + 8`, so 245 is `#8a8a8a`.
  * **No `length=` on a dimmed row.** SwiftBar truncates the title before
    parsing escapes, so it would cut mid-sequence. Nothing uses both today.
* The grey is now a fixed sRGB value rather than a theme-aware one — same as
  the `#999999` it replaces, so no regression, but it rules out
  `.labelColor`-style adaptation unless we switch to code 39.
* `#999999` and `#888888` collapsed into one grey (245 ≈ `#8a8a8a`). The two
  were never distinguishable at menu size.
* Any new read-only **leaf** row must use `PASSIVE` + `_dim()`; the
  regression test fails the build otherwise. A row with `--` children still
  needs an action — `color=` or a no-op `shell=`.
