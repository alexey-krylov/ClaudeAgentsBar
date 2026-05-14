# 0010. Compact menu-bar mode: ANSI-coloured `●` bullets

* Status: Accepted
* Date: 2026-05-14

## Context

The default menu-bar title is `[icon] 🟡N 🟢M 🔵K`. Each emoji counter
is rendered via Apple Color Emoji at roughly 16 px advance width, and
the leading icon adds another 14–18 px. On a roomy external display
that's fine — the title reads at a glance.

On a notched MacBook (14"/16" Pro, redesigned Air) the bar is split by
the camera housing and only the strip to the *right* of the notch
holds status items, laid out right-to-left. Once a user installs a
handful of third-party menu-bar apps (cloud sync, screenshot tool,
update agent, IDE indicators…) ClaudeAgentsBar starts getting clipped
behind the notch — the plugin is still running, just not drawn. The
existing *MacBook notch* section in the README already lists the
"prune your menu bar" mitigations; this ADR addresses the orthogonal
question of how to make *our* footprint as small as it can reasonably
be without hiding the plugin's value.

The full default title for a moderately busy session list is something
like `◐ 🟡2 🟢1 🔵3`, which is ~80 px. The counters carry the most
information; the icon is branding. So the goal: drop the icon, then
shrink the counter glyphs.

## Alternatives considered

| # | Approach | Verdict |
|---|----------|---------|
| 1 | **SF Symbols via `sfimage=`** (e.g. `circle.fill`) | Rejected. SwiftBar's `sfimage=` parameter applies one image to the entire title line — there's no way to inline three SF Symbols of different colours next to three different numbers. We'd be back to one glyph + counters, and we'd lose the per-bucket colour signal. |
| 2 | **Narrower emoji circles** | Rejected. Unicode has no compact cyan/yellow/green circle pair. `🟡🟢🔵` are already the smallest of their family; `⚫⚪🟤` are narrower but monochrome, defeating the colour signal. |
| 3 | **Plain numbers, no glyphs** (`2 1 3`) | Rejected. Without a colour or symbol prefix, the three buckets become positional-only — readers have to remember "first number is active, second is fresh, third is acknowledged". Worse, omitting empty buckets (which we already do) makes the position unstable. |
| 4 | **ASCII `*` / `+` / `o` without colour** | Rejected. Narrow but monochrome — same failure mode as #3. |
| 5 | **Dim the icon and keep the emoji** | Half-measure. Saves ~14 px from the icon but the emoji counters stay wide; total saving is ~18 px instead of ~30. Not enough to clear notch contention on a busy bar. |
| 6 | **Custom template PNG for each bucket** | Three template images stitched into one row would technically render at any pixel width, but SwiftBar's title only takes one `templateImage=`. We'd need ImageMagick/Pillow to pre-compose, which violates [ADR-0006](./0006-json-config-stdlib-only.md). |
| 7 | **ANSI-coloured `●` via `ansi=true`** | Accepted. Single Unicode glyph (`●`, ~9 px advance), recoloured per bucket via `\x1b[…m` escape codes, SwiftBar parses them when `ansi=true` is set. Three counters now fit in ~50 px and keep their colour semantics. |

## Decision

Add a boolean `compact` config knob, default `false`. When enabled,
`_print_menubar` takes a separate branch:

* The menu-bar icon is suppressed entirely (no `image=`, no inline
  glyph). Branding loss is the explicit trade.
* Each non-zero counter is rendered as
  `<ansi-colour>●<reset><count>` — a single `●` (U+25CF) coloured via
  a **menu-bar-specific palette** (`_ANSI_ACTIVE_BAR` /
  `_ANSI_FRESH_BAR` / `_ANSI_ACK_BAR`, the bold-bright `9{2,3,4}m`
  variants), separate from the toned-down palette the dropdown rows
  use (`_ANSI_WORKING` / `_ANSI_FRESH` / `_ANSI_ACK`). The two
  contexts have different legibility budgets: dropdown rows sit on
  the menu's solid background and benefit from softer colours, while
  the menu-bar `●` is a 9 px glyph competing with the wallpaper and
  needs the brighter ANSI variants to stay readable. `_COMPACT_ANSI`
  is the map that wires `RenderGroup` → bar palette; SwiftBar's
  `ansi=true` flag on the title line enables the parser.
* Empty buckets are still omitted (same as the default branch). If
  every bucket is zero, the title falls back to a single dim
  `●` (`color=#888888`) so the plugin keeps a visible foothold on the
  bar — disappearing entirely would be worse than just dim.
* `_COMPACT_ANSI` maps `RenderGroup → ANSI colour string`. Adding a
  new render group requires extending this map iff the group should
  appear in the compact title; the existing
  `_MENUBAR_COUNTER_ORDER` already gates participation.

The JSON loader does *not* go through the generic `take()` helper for
this knob: `bool("false") == True` in Python, so the default int/str/
float coercion path would silently accept the wrong type. Instead the
loader requires the raw value to already be a JSON boolean (which
`json.loads` produces natively for `true`/`false`).

## Consequences

**Wins:**

* ~30 px reclaimed on the menu bar — enough to unblock notch
  clipping in most real-world bar configurations.
* Colour semantics preserved across contexts (yellow = active,
  green = fresh, blue = acknowledged) even though the exact ANSI
  codes differ between the menu bar and dropdown rows — the
  at-a-glance read is unchanged.
* No new dependencies; ANSI rendering uses SwiftBar's existing
  `ansi=true` path that the dropdown rows already rely on, so we're
  not stress-testing a new code path.
* Default is `false`, so users on a roomy display see no change. This
  is opt-in.

**Costs:**

* Branding loss: the Claude mark disappears from the bar when
  `compact` is on. Users who chose a custom `menubar_icon` (Slack
  bubble, SF Symbol, custom PNG) lose that too. Documented as an
  explicit trade in the README.
* Two rendering branches in `_print_menubar`. Both are short and the
  shared `_MENUBAR_COUNTER_ORDER` keeps them in sync, but they need
  to be updated together when render-group changes happen.
* Two ANSI palettes (dropdown vs. menu bar) instead of one. The
  duplication is intentional — see the Decision — but it does mean a
  palette tweak ("make `acknowledged` slightly more teal") may need
  to land in two constants, not one. `_ANSI_*_BAR` are colocated
  with `_ANSI_*` in the module header to make the pairing obvious.
* Dropping the icon means losing the "click target" affordance — on
  an empty bar the lone dim `●` is less obviously clickable than a
  recognised app icon. Acceptable: users who enable compact mode know
  what they're trading.

## Related

* [ADR-0006](./0006-json-config-stdlib-only.md) — the stdlib-only
  constraint that ruled out Pillow-based image composition (#6).
* [ADR-0008](./0008-menubar-template-image-with-multirep-tiff.md) —
  defines `menubar_icon`, which `compact` mode bypasses entirely.
