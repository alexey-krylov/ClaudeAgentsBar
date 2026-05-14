# 0008. Menu-bar icon: template image, resized via `sips`, stitched as multi-rep TIFF

* Status: Accepted
* Date: 2026-05-13

## Context

The menu-bar title started as a literal emoji (`🤖`). That works but has
two problems on a real menu bar:

* Apple Color Emoji glyphs sit on a different baseline than SF Pro, so
  the icon looks misaligned next to the counters.
* The icon stays full-colour in dark mode, in light mode, and when the
  menu is open (active state). Every native app — Mail, Slack, the
  Claude.app tray itself — uses a *template image* that macOS tints to
  the current menu-bar appearance.

Once we want to render an image we have to deal with retina density.
SwiftBar passes our base64 PNG straight to `NSImage(data:)`. A single
22×22 PNG looks pixelated on a 2×/3× display because `NSImage` has no
way to know the file is a 1× asset — it draws one physical pixel per
source pixel.

Approaches considered for the image pipeline:

1. **Ship a static 1× PNG.** Simplest, pixelated on retina. Rejected.
2. **Ship a static @2x PNG.** Better on retina, soft on 1× and on
   ProMotion 3× phones-as-displays. Still single-rep.
3. **Pillow / CoreGraphics for resize.** Pillow isn't stdlib (ADR-0006
   bans deps). CoreGraphics via `objc` is the same dep story.
4. **`sips` + `tiffutil`.** Both ship with every macOS install. `sips`
   resizes a PNG to an exact pixel size; `tiffutil -cathidpicheck`
   stitches multiple PNGs into a multi-representation TIFF that
   `NSImage` reads as 1× / 2× / 3× variants of the same logical image.
   That's what Apple's own tools emit for asset catalogues.

Approaches considered for tinting:

1. **`image=<base64>`** — full-colour, no tinting. We expose this as
   the `image:` prefix for users who want a coloured icon.
2. **`templateImage=<base64>`** — macOS converts a monochrome PNG to a
   template image automatically (transparent pixels stay transparent,
   opaque pixels get tinted with the current menu-bar foreground
   colour). This is the right default — it matches every native app.

## Decision

`menubar_icon` accepts four forms:

* plain glyph (emoji / Unicode) — embedded inline;
* `sf:<name>` — SF Symbol via SwiftBar's `sfimage=`;
* `template:<path>` — monochrome PNG via `templateImage=<base64>`;
* `image:<path>` — full-colour PNG via `image=<base64>`.

For `template:` and `image:` paths the plugin:

1. Hashes the absolute source path to a stable short filename.
2. If a cached TIFF exists and its mtime is at or after the source's,
   uses it directly.
3. Otherwise runs `/usr/bin/sips -Z <pt * scale>` once per scale (1, 2,
   3) into per-scale PNGs, then `/usr/bin/tiffutil -cathidpicheck` to
   stitch them into a single multi-rep TIFF. The intermediate PNGs are
   deleted after stitching.
4. On *any* failure (missing `sips`, unreadable source, `tiffutil`
   exit ≠ 0) it logs the error to stderr and returns the original
   path, so the menu still renders something.

The cache lives under `$XDG_CACHE_HOME/claude-agents-bar/` (or
`~/.cache/claude-agents-bar/`). Cache files are named
`icon-<sha1prefix>-<pt>.tiff`.

When the configured file is missing (e.g. Claude.app not installed) the
plugin falls back to a new `menubar_icon_fallback` knob — a plain glyph,
default `"🤖"` — so the bar always has *something*.

The default `menubar_icon` is now
`template:/Applications/Claude.app/Contents/Resources/TrayIconTemplate@2x.png`:
it shows the Claude mark when Claude.app is installed, and gracefully
degrades to the emoji fallback otherwise.

## Consequences

**Wins:**

* Native rendering: the icon tints with the menu bar, lines up on the
  SF baseline, stays crisp on every density.
* Stdlib-only pipeline — `sips` and `tiffutil` are part of macOS, no
  Python or Homebrew dependency added.
* Cache-driven: the expensive part (two subprocess calls, three PNG
  encodes) runs once per icon source and is invalidated automatically
  when the source changes.
* Graceful degradation at every step: missing source → fallback glyph;
  failed resize → original path; broken cache → regenerated.

**Costs:**

* Two subprocess invocations on first render after a config or source
  change. Negligible in practice (≪ 50 ms cold) and amortised on
  subsequent ticks.
* The cache directory accumulates entries until the user manually
  clears it. Each entry is a few kB; not worth a sweeper.
* `tiffutil -cathidpicheck` is undocumented public API — the flag has
  been stable since 10.6 but isn't covered by an API contract. If it
  ever disappears we'd need a fallback (probably `iconutil` or
  Pillow); the existing fail-soft path already handles the failure
  mode.

## Related

* [ADR-0006](./0006-json-config-stdlib-only.md) — the "stdlib only"
  constraint that ruled out Pillow / CoreGraphics here.
