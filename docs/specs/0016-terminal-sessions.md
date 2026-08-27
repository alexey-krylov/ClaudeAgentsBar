# Spec 0016 — Terminal sessions: a row marker and a click that goes to the terminal

* Status: **Implemented**
* Date: 2026-08-24

## Why

The bar has always shown terminal sessions — `cli` is in
`core.INTERACTIVE_ENTRYPOINTS`, and only headless runs (`claude -p`, SDK)
are filtered out. But the menu treated them as if they were editor sessions
in two ways, both wrong:

1. **The row didn't say where the session lives.** Two rows, identical in
   shape, one running in VSCodium and one in a Terminal tab.
2. **The click fired the editor deeplink at it.**
   `<scheme>anthropic.claude-code/open?session=<id>` makes the extension
   *resume that transcript in the editor*. The terminal process doesn't
   stop — so you end up with two live sessions appending to one transcript,
   which is neither what the user asked for nor safe.

Both are fixed by knowing something we already had in hand: the session's
`entrypoint`.

## Product decisions (locked with the user)

1. **Always terminal-aware.** No opt-in knob for the behaviour: the bar
   knows exactly which sessions are terminal ones, so it routes them
   correctly, always. Editor sessions keep the deeplink path unchanged.
2. **A marker on the row**, directly after the state circle: a dim `❯`. A
   shell prompt is what a terminal looks like; a circled `ⓣ` was tried in
   between and was legible but arbitrary.
3. **The click goes to the running terminal**, with `claude --resume` as the
   fallback when there's nothing running to go to.

## What counts as a terminal session

`core.TERMINAL_ENTRYPOINTS = {"cli"}`, exposed as `Session.is_terminal`. An
**unset** entrypoint (older transcripts) is deliberately *not* terminal: the
editor deeplink is the safer default when we don't know.

`TERMINAL_ENTRYPOINTS` is a subset of `INTERACTIVE_ENTRYPOINTS` — enforced by
a test, since a terminal entrypoint that doesn't survive the interactive
filter would never render a row to click.

## Rendering

```
🟡 ❯ ⓑ infra · Release 1.4.2 · 3m
```

Order on the row: state circle, marker, tag glyph, group prefix, title. The
`❯` is dimmed (`core._ANSI_STALE`), like the group prefix — it's provenance,
not status.

**Position is what makes the glyph work.** An earlier revision put the marker
between the group prefix and the title, where it read as punctuation and got
lost among the middle dots separating the row's segments; that's what sent us
looking for a circled letter instead. Anchored to the state circle it reads
as a marker, and the shell prompt says "terminal" more directly than a `ⓣ`
ever did.

Two ways of drawing the real `greaterthan.square` SF Symbol were tried and
rejected:

* **`sfimage=greaterthan.square`** — SwiftBar always draws an image at the
  head of the row, ahead of the state circle, and can't reserve that slot on
  rows carrying no image, so every terminal row sat indented against its
  neighbours.
* **SwiftBar's inline `:greaterthan.square:`** — substituted only while
  building a row *without* `ansi=true` (under that flag SwiftBar takes a
  separate path that reads SGR escapes instead). The row's colours all ride
  on ANSI: the tag letter, the green `ⓦ`, the red `⎇`, the state-coloured
  duration. Trading every one of them for one symbol isn't worth it.

## The click

`bin/app/open-terminal-session.sh <session-id> [cwd] [terminal-app]`,
invoked via `/bin/bash` so a lost executable bit can't kill the row (the
1.1.1 *Multi-workspace mode* failure). It records the click into the clicks
sidecar first — identical to `open-session.sh`, so 🟢 → 🔵 works the same —
then tries, in order:

1. **Raise the tab that owns the process.** The session registry Claude Code
   2.1.228+ maintains at `~/.claude/sessions/<pid>.json` maps `sessionId` →
   `pid` (plus `cwd` and, inside tmux, the tmux session name). `ps -o tty=`
   turns the pid into a tty; AppleScript matches that tty against the `tty`
   of every open tab — Terminal.app exposes it on `tab`, iTerm2 on `session`
   — and selects the match. This is the only branch that's a real "switch to
   it".
2. **`tmux attach -t <name>`** in a new window, when the registry carries a
   tmux name. A session inside tmux has no window of its own — its tty
   belongs to the tmux server.
3. **`screen -r <ppid>`** in a new window, when the parent process is GNU
   screen. Same reasoning.
4. **`claude --resume <id>`** in a new window, `cd`'d to the session's cwd.
   The session is dead, or detached with nothing to raise — a fresh session
   on an old transcript, which is the right answer once nothing is running.

Registry entries outlive the processes they describe, so liveness is
rechecked with `kill -0` before any of the first three branches.

**Which terminal**: the `terminal_app` knob — `"auto"` (default: iTerm when
installed, else Terminal), `"Terminal"`, or `"iTerm"`. The value selects an
AppleScript branch, so it's validated at config load; an unknown name is
refused rather than silently producing a dead click.

**Safety.** The session id is already constrained by `core._SESSION_ID_RE`
before it can reach a row. The tmux name comes from the registry (foreign
data) and is re-validated against `^[A-Za-z0-9_.-]+$` in the script before
interpolation; cwd and the assembled command go through `printf %q`, and
every AppleScript receives its arguments via `on run argv` rather than
string interpolation.

## Failure modes

| Situation | Behaviour |
|---|---|
| No `jq`, or no session registry | Straight to `claude --resume` (needs neither). |
| Process dead | Same. |
| Process alive, tab closed / detached | tmux or screen branch, else `claude --resume`. |
| Automation permission denied | Click does nothing; macOS asks once, on the first click. See [troubleshooting](../troubleshooting.md). |
| Terminal isn't running | AppleScript launches it. |

## Not in scope

* Terminal emulators beyond Terminal.app and iTerm2 (Ghostty, WezTerm, Warp
  and friends expose no comparable tty-to-tab lookup over AppleScript).
* Attaching to a *live* session's I/O — `messagingSocketPath` in the registry
  hints at what that would take, and it's a different feature.
* Any change to how editor sessions open.
