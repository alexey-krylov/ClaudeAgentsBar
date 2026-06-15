# Configuration reference

User-tunable knobs live in an **optional JSON config file**. Defaults
apply for any field you don't set, so the file is entirely optional.

## Where the file lives

Search order (first match wins):

1. `$CLAUDE_AGENTS_BAR_CONFIG` (explicit path)
2. `$XDG_CONFIG_HOME/claude-agents-bar/config.json`
3. `~/.config/claude-agents-bar/config.json`

To start from a working example:

```bash
mkdir -p ~/.config/claude-agents-bar
cp config.example.json ~/.config/claude-agents-bar/config.json
$EDITOR ~/.config/claude-agents-bar/config.json
```

…or click **Tools → Configuration…** in the menu — the plugin seeds
the file from `config.example.json` on first click and hands it to
your default text editor (`open -t`).

SwiftBar picks up new values on the next 5 s tick — no reinstall or
restart needed.

## All fields

| Key | Default | Meaning |
|---|---|---|
| `window_minutes` | `180` | How far back the dropdown lists sessions. Anything older is hidden. |
| `fresh_minutes` | `60` | An idle session stays 🟢 fresh this long after `Stop`. A click before the timer expires promotes it to 🔵 immediately; otherwise it auto-promotes when the window elapses. |
| `ack_minutes` | `60` | An acknowledged 🔵 session fades to ⚪ stale after this long without a new click. Each click restarts the timer. |
| `watchdog_seconds` | `90` | `working` entries whose last event is older than this get demoted to `idle` — covers sessions that crashed mid-tool and never emitted `Stop`. |
| `title_max` | `60` | Max length of a session title shown on a row, with an ellipsis appended on overflow. |
| `menubar_icon` | Claude.app tray icon (`template:` PNG) | Icon drawn before the counters. See *Menu-bar icon* below for the four accepted shapes. |
| `menubar_icon_fallback` | `"🤖"` | Glyph used when `menubar_icon` points at a missing file (e.g. Claude.app not installed). |
| `editor_url_scheme` | `"vscode://"` | URL scheme prefix used when opening a session on row click. Full URL: `<scheme>anthropic.claude-code/open?session=<uuid>`. Common values: `"vscode://"` (default, stock VSCode), `"vscodium://"` (VSCodium), `"cursor://"` (Cursor). Other Code-OSS forks may register their own scheme. Must include the trailing `://`. The editor must also have the `anthropic.claude-code` extension installed for the deeplink to land on a session. |
| `language` | `"auto"` | UI language for menu labels, dialogs, and `X ago` strings. Supported: `en`, `ru`, `zh`, `zh-TW`, `fr`, `de`, `it`, `vi`. `"auto"` detects from macOS `AppleLocale`, falling back to `$LANG`. Region tag optional — `zh-TW` picks Taiwan locale; others fall back to the primary subtag (`zh`) then `en`. |
| `compact` | `false` | When `true`, drops the icon and replaces `🟡🟢🔵` with ANSI-coloured `●` bullets (`●2 ●1 ●3`). Saves ~30 px — useful on notched MacBooks. See *Compact mode* below. |
| `model_badge` | `true` | Toggle for the full model row shown inside each session's submenu (the line with the `cpu` icon, e.g. `claude-opus-4-7`). Set to `false` to hide that row. The previous in-row badge (`ⓞ`/`ⓢ`/`ⓗ`/`ⓜ` next to the title) has been removed — the submenu line is the only model surface now. |
| `context_window_tokens` | `1000000` | Total context-window size used to compute the per-session `{N}% — {used}k/{total}k` indicator. Matches Claude Opus 4.7 / 4.6 and Sonnet 4.6 (Anthropic's API default since 2026-04-23). Override to `200000` when running Haiku 4.5 or Sonnet 4.5. See [ADR-0011](./adr/0011-configurable-context-window.md) for the alternatives we considered. |
| `context_warning_threshold` | `80` | Percent of context-window usage above which the main row gets an inline `⚠ {pct}%` marker between the title and the age label. Yellow up to 90 %, red beyond — same zones Claude Code's CLI uses. Set to `100` to suppress the inline marker while keeping the submenu gauge. Valid range `1..100`; out-of-range and non-numeric values fall back to `80`. |
| `notify_on_stop` | `true` | Play a chime, speak a phrase, and show a macOS notification banner when a session finishes. Requires `terminal-notifier` (`brew install terminal-notifier`). Set to `false` to silence all completion notifications. |
| `notify_threshold_sec` | `30` | Skip the notification if the last user turn was less than this many seconds ago. Avoids noise from quick one-liner exchanges. |
| `notify_phrases` | `["Check it", "Done", "Ready for review", "Your turn"]` | Phrases spoken aloud (via `say`) and shown in the notification banner. One is chosen at random on each `Stop`. Replace the list entirely to customise the voice lines. |
| `notify_on_wait` | `true` | Play a chime, speak a phrase, and show a macOS notification banner when Claude is blocked on a tool-approval dialog (`PermissionRequest` event). Requires `terminal-notifier`. Set to `false` to silence permission-prompt notifications while keeping completion notifications. |
| `notify_wait_phrases` | `["Need instructions", "Awaiting input", "Decision needed", "I'm blocked"]` | Phrases spoken aloud and shown in the notification banner when Claude needs permission. One is chosen at random on each `PermissionRequest`. |
| `notify_sound_stop` | `"Hero"` | Chime played on `Stop`. Bare name (`"Hero"`, `"Glass"`, `"Funk"`, …) resolves under `/System/Library/Sounds/`. Absolute or `~`-paths are used as-is. `null` suppresses the chime — banner and voice still fire. Missing files log a warning to SwiftBar's log and fall back to no chime for that event. |
| `notify_sound_wait` | `"Funk"` | Chime played on `PermissionRequest`. Same value shapes as `notify_sound_stop`. Default `"Funk"` is shorter and softer than `Hero` — *needs your attention* vs *task complete*. |
| `notify_voice` | `null` | `say(1)` voice for the spoken phrase. `null` / absent uses the system default voice. A voice name (`"Samantha"`, `"Daniel"`, `"Yuri"`, …) invokes `say -v <name>`. The sentinel `"off"` skips the spoken phrase entirely. Run `say -v '?'` in Terminal to list installed voices. Shared between Stop and PermissionRequest. |
| `notify_say_gap_sec` | `1` | Speech serialization (only one `say(1)` speaks at a time — see *Speech serialization* below). The pause held **after** each spoken notification before the next may start, in seconds (fractions ok). `0` keeps the serialization but adds no pause. |
| `notify_say_stale_sec` | `30` | Speech serialization. A spoken notification that has waited for the speech lock longer than this many seconds is **dropped unspoken** — a stale announcement lagging behind reality is noise. See *Speech serialization* below. |
| `notify_summary_marker` | `"-- "` | Prefix of the assistant's italic closing line `*-- Name - Summary*` (name and summary split on the first `" - "`). Drives the **Stop** speech/banner (the summary), the **awaiting** speech/banner (name + summary), and — only when `use_session_titles_for_menubar` is on — the **menu title** (the name). `null` / `""` disables it everywhere. Matched literally (no regex), last line only. See *Spoken summary* below. |
| `use_session_titles_for_menubar` | `false` | Whether the menu row title uses the response-marker **name** (`*-- Name - Summary*`). `false` (default): the row shows Claude Code's own `ai-title` — the same English label **VSCode displays**, so the menu stays consistent with the editor. `true`: the marker name takes priority over `ai-title`, surfacing your own wording (e.g. Russian) in the menu. Independent of this knob, the marker is **always** parsed for the spoken notifications (the awaiting hook reads name + summary in Bash) — so the primary reason to write the marker, *voice*, works either way. When off, the per-tick title parse is skipped entirely. See *Spoken summary* below. |
| `remind_recap_after_min` | `null` | Controls the *Remind* submenu action. When the time since a session's last output (its transcript mtime) is **≥** this many minutes, a Remind click speaks the session's **opening** summary first, then its **latest** one — so you recall what a cold session was about before where it is now. While you're still in the flow (less time elapsed) it speaks only the latest. `null` / absent (default): always latest only. `0`: always recap. A session with a single summary speaks it once either way. See *Spoken summary* below. |
| `notify_idle_interval_min` | `20` | Idle-session reminders. A finished session that sits 🟢 **green** (unread — you haven't clicked it) past this many minutes gets re-announced on the plugin tick (chime + spoken phrase + banner, like an awaiting prompt). Each subsequent reminder **doubles** the wait: 20, 40, 80, … minutes after the session finished. The number of reminders is bounded by how long the row stays green — `fresh_minutes` (default 60), after which it auto-fades to 🔵 and reminders stop — so the default 20-min start gives two reminders (20 and 40 min); raise `fresh_minutes` for more. Clicking the session (or *Tools → Acknowledge all*) ends the schedule. `0` / `null` turns the feature off. Respects `quiet_hours` and the *Banner only* audio mode. See *Idle reminders* below. |
| `notify_idle_phrases` | `["Don't forget me", "Still unread", "Pending review", "Your turn"]` | Phrases spoken aloud and shown in the banner for an idle-session reminder. One is chosen at random per reminder. |
| `notify_sound_idle` | `"Submarine"` | Chime played on an idle-session reminder. Same value shapes as `notify_sound_stop`. Default `"Submarine"` is a soft ping, distinct from `Hero` (done) and `Funk` (awaiting). |
| `quiet_hours` | `"23:00-08:00"` | Scheduled silence window in 24h local time, `"HH:MM-HH:MM"`. `start > end` wraps midnight (e.g. `"23:00-09:00"` covers the night). `null` disables. Malformed values fall back to the default with a warning. The window is half-open: 09:00 sharp is no longer quiet. |
| `quiet_hours_silences` | `["sound", "voice"]` | Channels suppressed during quiet hours. Subset of `["sound", "voice", "banner"]`. Default mutes audio (chime + voice) but the banner still appears so you don't miss the event. Add `"banner"` to go fully silent; list only `"voice"` to keep the chime. Unknown entries are dropped at load with a warning. |
| `notify_audio` | `true` | Master switch for notification audio (chime **and** spoken `say`), independent of quiet hours. `true` (default): notifications sound off per `notify_sound_*` / `notify_voice`. `false`: banner only — no chime, no speech (the banner still appears). Toggled live from *Tools → Notifications* (*Banner and voice* / *Banner only*); once you pick one there, that sidecar choice overrides this knob, so it's only the first-launch default. |
| `keep_awake` | `"off"` | First-launch keep-awake mode. `"off"` (default), `"auto"` (`caffeinate -i` while any session is *working*), `"always"` (until disabled). Once you click a mode in *Tools → Keep awake* the sidecar takes precedence — this knob is only consulted on a clean install. See *Keep awake* below for limits. |
| `multi_workspace_mode` | `true` | Raise the editor window that owns a clicked session before firing the deeplink, so it lands in the right window even with several windows / a multi-root workspace open. Set to `false` for the snappy single-window path: clicks fire the deeplink directly (instant, no extra tab) but land in whatever window is frontmost. See *Multi-workspace focus* below. |
| `editor_focus_settle_sec` | `0.1` | Only used when `multi_workspace_mode` is `true`. Seconds to wait after raising the window before firing the deeplink, so the anchor tab renders and the resumed chat lands on top of it. Lower trims latency but risks landing on the file under load; `0` skips the wait. Range `0..5`. |

Fractional values are accepted where they make sense — e.g.
`"window_minutes": 30` for a half-hour window, or `"fresh_minutes": 0.5`
for thirty-second granularity. Keys starting with `//` are ignored, so
JSON-style "comments" in the file are fine. Unknown keys are ignored
too: forward-compatible config files don't error. Invalid values emit
a warning to SwiftBar's log (*Show Logs…*) and the default is used —
the menu never goes down on a broken config.

## Menu-bar icon

`menubar_icon` accepts four shapes:

| Prefix | Example | Effect |
|---|---|---|
| *(none)* | `"✱"`, `"🤖"` | Embedded as an inline glyph. Apple Color Emoji won't line up with SF Pro baselines — use sparingly. |
| `sf:` | `"sf:bubble.left.fill"` | Rendered as an SF Symbol via SwiftBar's `sfimage=`. |
| `template:` | `"template:/Applications/Claude.app/Contents/Resources/TrayIconTemplate@2x.png"` | A monochrome PNG. macOS auto-tints it for the current menu-bar appearance (light / dark / active). **Default.** |
| `image:` | `"image:~/Pictures/my-icon.png"` | A full-colour PNG, no theme adaptation. |

Paths may be absolute or relative to the plugin directory. For
`template:` and `image:` sources the plugin auto-resizes to fit the
menu-bar height and stitches the 1× / 2× / 3× variants into a
multi-rep TIFF so retina displays render crisply. The cached output
lives under `$XDG_CACHE_HOME/claude-agents-bar/` (or
`~/.cache/claude-agents-bar/`); safe to delete at any time — it'll be
rebuilt on the next tick.

If the configured file is missing the plugin falls back to
`menubar_icon_fallback` (default `"🤖"`), keeping the bar populated
even when e.g. Claude.app isn't installed.

Multi-rep TIFF rationale: [ADR-0008](./adr/0008-menubar-template-image-with-multirep-tiff.md).

## Compact mode

Setting `"compact": true` collapses the menu-bar title to its
narrowest form:

```
●2 ●1 ●3        ← compact: true   (ANSI-coloured bullets, no icon)
◐ 🟡2 🟢1 🔵3   ← compact: false  (default)
```

The icon is suppressed and `🟡🟢🔵` are replaced with `●` rendered
through SwiftBar's `ansi=true`, in a brighter palette than the
dropdown rows (the 9 px glyph competes with the wallpaper). Colour
semantics line up: yellow = active, green = fresh, blue =
acknowledged. Empty buckets are omitted; if nothing is active, a
single dim `●` keeps the slot occupied so the plugin doesn't
disappear from the bar entirely.

Trade-off: loss of branding (no Claude mark) in exchange for ~30 px
of horizontal space. Recommended only on notched MacBooks where the
bar is contended; on a roomy external display the default is easier
to read at a glance.

Rationale for picking ANSI bullets over SF Symbols, narrower emoji,
or plain numbers: [ADR-0010](./adr/0010-compact-menubar-ansi-bullets.md).

## Notifications

ClaudeAgentsBar speaks up in three situations — a session **finishing**,
a session **blocked** on a permission prompt, and a finished session left
**unread** too long. Each plays a chime, speaks a line via macOS `say`,
and shows a clickable `terminal-notifier` banner whose three lines are
laid out the same way (spec 0009). Clicking a banner deep-links straight
to that session in your editor — no hunting through the menu bar — and
**marks it read**, exactly like clicking the menu row: the session leaves
the 🟢 green group on the next tick (and, for an idle reminder, the
reminder schedule ends).

| | **Stop** (done) | **Awaiting** (blocked) | **Idle** (unread) |
|---|---|---|---|
| **Trigger** | `Stop` hook — session finished | `PermissionRequest` hook — tool-approval prompt | plugin tick — green & unread past the interval |
| **Banner line 1** | session `ai-title` | `❓ <phrase>` | `⚠️ <phrase>` |
| **Banner line 2** | `<project> — <icon> <branch>` | ← same | ← same |
| **Banner line 3** | summary (else phrase) | `name — summary` | `name — summary` |
| **Spoken (`say`)** | phrase → summary | phrase → name → summary | phrase → name → summary |
| **Chime** | `Hero` (`notify_sound_stop`) | `Funk` (`notify_sound_wait`) | `Submarine` (`notify_sound_idle`) |
| **Phrases** | `notify_phrases` | `notify_wait_phrases` | `notify_idle_phrases` |
| **Off switch** | `notify_on_stop: false` | `notify_on_wait: false` | `notify_idle_interval_min: 0` |

Reading across the rows:

- **Line 2** is the session's `<project> — <icon> <branch>` (just
  `<project>` outside a repo), read from its working dir so it matches the
  row's submenu — `<icon>` is `ⓦ` for a worktree, `⎇` for an ordinary
  branch (the same glyphs the menu row uses inline).
- The line-1 type **emoji** (❓ / ⚠️) is plain-text colour — there's no
  rich text in a macOS banner — and is **banner-only**: `say` never
  speaks it.
- `name` / `summary` come from your `*-- Name - Summary*` marker (see
  *Spoken summary* below); line 3 is empty when a turn carried no marker.
- **Spoken `say`** stitches the parts with a short pause between them so
  they don't run together as one breath ("Done. … Migrated the auth
  module").

Stop alone has a `notify_threshold_sec` (30 s) floor so quick one-liners
stay silent; awaiting and idle have none. Idle isn't a Claude Code event —
it rides the plugin tick on a doubling schedule (20 → 40 → … min); its
mechanics are under *Idle reminders* below. All three obey `quiet_hours`
and the *Banner only* mode (`notify_audio`).

**Why two knobs, not one.** Stop notifications are most useful for
long turns and noisy for quick one-liners — that's what
`notify_threshold_sec` (default 30 s) silences. Permission
notifications have no threshold: every approval prompt is
intentional, and Claude is genuinely stuck until you respond.
Silencing one channel doesn't affect the other.

**Customising the phrases.** Replace `notify_phrases` /
`notify_wait_phrases` with whatever you want spoken aloud — your
name, inside jokes, lines from a movie, your dog's name. `say` reads
any UTF-8 string, so non-ASCII works too. One phrase is picked at
random on each event, so a longer list gives more variety. To pick a
specific voice, edit `hooks/notify-stop.sh` / `hooks/notify-wait.sh`
(`say -v Samantha …`) — `say -v '?'` lists what's installed on your
Mac.

**Dependencies.** Banners require [`terminal-notifier`][tn]
(`brew install terminal-notifier`); without it the chime and `say`
still fire but no banner appears. `claude-agents-bar doctor` reports
its presence under the `notify/` check.

[tn]: https://github.com/julienXX/terminal-notifier

**Silencing everything temporarily.** macOS Focus / Do Not Disturb
suppresses the banner (and on Sonoma+, the chime as well) — usually
the right escape hatch when you don't want to edit config. For a
permanent off-switch:

```json
{ "notify_on_stop": false, "notify_on_wait": false }
```

Underlying hooks: `hooks/notify-stop.sh` and `hooks/notify-wait.sh`.
Both are plain Bash, source the shared `hooks/_notify-common.sh`, read
the same JSON config the plugin uses, and degrade silently when
`terminal-notifier` / `jq` / the icon asset is missing.

## Idle reminders

`Stop` fires once, when the session finishes. But a finished session you
never came back to just sits there 🟢 green — easy to forget. The
**idle reminder** re-announces it.

Unlike the notifications above, this isn't tied to a Claude Code event —
there's no "20 minutes after Stop" hook, and the plugin runs no daemon.
Instead it rides the SwiftBar tick (the plugin re-runs every 5 s whether
or not the menu is open): on each tick the plugin checks which green,
**unread** (not yet clicked) sessions have crossed their next reminder
interval and fires `hooks/notify-idle.sh` for them — same chime + spoken
phrase + clickable banner as an awaiting prompt, just with its own sound
(`notify_sound_idle`), phrases (`notify_idle_phrases`) and title.

The schedule **doubles** each time, measured from when the session
finished:

| Reminder | Default time after finish |
|---|---|
| 1st | 20 min (`notify_idle_interval_min`) |
| 2nd | 40 min |
| 3rd | 80 min |
| … | ×2 each |

How many actually fire is bounded by how long the row stays green —
`fresh_minutes` (default **60**), after which the session auto-fades to
🔵 *acknowledged* and reminders stop. So with the defaults you get **two**
reminders (20 and 40 min; 80 > 60 is past the green window). Raise
`fresh_minutes` to allow more, or shorten `notify_idle_interval_min` to
fit more in.

**Stopping reminders.** Clicking the session — its menu row *or* its
reminder banner — (or *Tools → Acknowledge all*) marks it read: it leaves
the green group and the schedule ends.
A new turn that finishes again restarts the schedule from the first
reminder.

**Turning it off.** Set `notify_idle_interval_min` to `0` or `null`.
The feature is on by default at 20 minutes.

Quiet hours and the *Banner only* audio mode apply exactly as they do to
the other notifications. Progress is tracked in the
`~/.claude/agent-state.idle-reminders` sidecar.

## Custom audio

Three knobs, each independent of `notify_on_*`:

```json
{
  "notify_sound_stop": "Hero",
  "notify_sound_wait": "Funk",
  "notify_voice": "Samantha"
}
```

`notify_sound_*` accepts a bare name (`"Hero"`, `"Glass"`, `"Funk"`,
`"Submarine"`, …), an absolute path (`"/Users/me/Sounds/foo.aiff"`), or
a `~`-path (`"~/Sounds/foo.aiff"`). Set to `null` to skip just the chime
— banner and voice still fire. A missing file logs a warning to
SwiftBar's log and falls back to no chime for that event; the
notification is never taken down by a misconfigured value.

`notify_voice` is the macOS `say` voice. `null` / absent uses the system
default. Any installed voice (`"Samantha"`, `"Daniel"`, `"Yuri"`,
`"Tessa"`, …) is passed straight to `say -v <name>`. The sentinel
`"off"` skips the spoken phrase entirely. List installed voices with:

```bash
say -v '?'
```

The voice setting is shared between Stop and PermissionRequest — Apple
voices don't ship per-event variants, and one voice with two phrase
lists is enough variety in practice.

## Spoken summary

By default `say` reads a random `notify_phrases` line ("Done", "Your
turn", …) when a session finishes. It can instead read a one-line
summary of what the session just did — and, if you give your replies a
short session **name**, show that name as the menu title and speak it
when a session is waiting on you.

This is controlled by a single knob, `notify_summary_marker` (default
`"-- "`). Your assistant ends each reply with an italic closing line in a
**two-field** form:

```
*-- Session name - one-line summary*
```

The marker (`-- `) is the line **prefix**; the **name** and **summary**
are split on the first `" - "` (a lone hyphen padded with spaces). Both
the menu and the hooks strip surrounding markdown emphasis (`*…*`, `_…_`,
`**…**`, `***…***`) and compare the prefix literally (no regex). Only the
**last** line of a reply is considered, so a `-- `/`- ` that appears
mid-reply is ignored.

The two fields feed four places:

| Where | Field used |
|---|---|
| **Menu title** | the **name** — shown in place of Claude Code's auto-generated English title. |
| **Stop** (session finished) | the **summary** — `say` reads the random phrase **then** the summary ("Done. Migrated the auth module"); the banner shows the summary alone. |
| **Awaiting** (permission prompt) | the **name + summary** — `say` reads the awaiting phrase, then the name, then the summary, so you can tell by ear which session is blocked and what it was doing; the banner shows `name — summary`. At a prompt the current turn hasn't closed with its marker yet, so these come from the last completed turn. |
| **Idle** (unread reminder) | the **name + summary** — same as awaiting (phrase → name → summary spoken; banner line 3 `name — summary`), re-announcing a finished session you haven't read. |

**Backward compatible:** a single-field line (`*-- just a summary*`, no
`" - "`) still works — there's no name, so the menu falls through to the
auto-generated title and only the summary is spoken. So existing
spec-0005 setups keep working unchanged.

The feature is on out of the box but **inert** until your assistant
actually ends replies with a marker line (see below). `null` / `""`
disables it everywhere (menu title falls through; speech falls back to a
random phrase). `notify_voice: "off"` and quiet hours still suppress
speech entirely — the marker changes *what* is spoken/shown, never
*whether*.

The same marker gates the **Remind** item at the top of each session
row's submenu: it re-speaks that session's summary on demand, using
`notify_voice`. The transcript is read **when you click**, not on the
render tick, so it adds no per-tick cost. The item is enabled whenever a
marker is configured and greyed-out when you disable it (`null` / `""`).

By default a click speaks just the **latest** summary. Set
[`remind_recap_after_min`](#configuration) to also hear the session's
**opening** summary first when the session has gone cold (no output for
that many minutes) — handy for picking a thread back up: you hear what it
was about, then where it is now. While you're still in the flow it stays
on the latest only, so an active session isn't verbose. A session with
only one summary speaks it once regardless.

If the marker is on but the session has no summary at all, the click
speaks a short "configure Claude to end replies with a summary line" hint
rather than staying silent. Unlike the automatic Stop speech, an explicit
*Remind* click speaks even under *Banner only* or `notify_voice: "off"` —
those mute only the automatic notification, not a deliberate click.

### Speech serialization

Several sessions can want to speak at once — one finishes (Stop) just as
the plugin fires an idle nudge for another, or you click *Remind* while a
notification is mid-sentence. Each spoken notification runs in its own
background `say(1)`, so without coordination they talk over each other and
you can't tell which session is which.

A shared lock fixes that: **only one `say` speaks at a time.** The others
queue. After each utterance there's a configurable pause —
[`notify_say_gap_sec`](#configuration) (default `1` s) — before the next
one starts, so they don't run together as one breath. Set it to `0` to
drop the pause but keep the one-at-a-time ordering.

To stop the queue from reading you a backlog of stale announcements, a
notification that has waited for the lock longer than
[`notify_say_stale_sec`](#configuration) (default `30` s) is **dropped
unspoken** — a "check me" that's a minute behind reality is just noise.
Raise it if you'd rather hear everything eventually; lower it to favour
freshness.

The lock covers **speech only**. The chime and the banner still fire
immediately and in parallel — short chimes overlapping isn't a problem,
and the banners are visual. Quiet hours, *Banner only*, and
`notify_voice: "off"` still suppress speech as before; the lock only
orders what does get spoken. It's a crash-safe file lock under
`~/.claude/` — if a speaking process dies, the next one detects the stale
lock and proceeds, so speech never wedges permanently.

### Setting up Claude to produce the summary line

The summary only works if your assistant ends its replies with the
marker line. Claude won't do that on its own — you ask it to, once, in an
instruction it reads every session. Two equivalent places:

- **Global** — `~/.claude/CLAUDE.md` (applies to every project), or
- **Per-project** — a `CLAUDE.md` checked into the repo root.

Add a rule like this:

```markdown
## Session marker

End every reply with a final line in italics in the form
`*-- Name - Summary*`:

  - Name: a short 2–4 word session name (shown as the menu title).
  - " - ": a single hyphen padded with spaces, separating the two fields.
  - Summary: a one-line, plain-language note of what you did or what I
    should do next (read aloud, shown in a banner). No need to capitalise.

    *-- Migrating the auth module - tests are green, ready for review*

Keep each field short — the summary gets read aloud.
```

The italics keep the line unobtrusive on screen while the hooks still
read it aloud — the emphasis markers are stripped before the prefix is
matched. The name/summary divider is the first `" - "`, so a hyphen
inside the summary is fine (only the first one splits). If you change
`notify_summary_marker` (e.g. to a different prefix or language), change
the prefix in your CLAUDE.md instruction to match — it's compared byte
for byte.

## Quiet hours

A nightly silence window plus a one-click "pause for an hour" / "pause
until morning" pair lives under *Tools → Notifications*:

```
Tools
  Notifications
    Quiet hours: 23:00 — 08:00 (active, 6h 12m left)
    Pause for 1 hour
    Pause until tomorrow morning
  ✓ Banner and voice
    Banner only
```

The *Banner and voice* / *Banner only* radio pair is the menu twin of the
[`notify_audio`](#configuration) knob: *Banner only* mutes the chime and
the spoken `say` for every notification (the banner still appears),
*Banner and voice* restores them. Your pick writes a sidecar at
`~/.claude/agent-state.notify-audio.mode` (`on`/`off`) that overrides the
config knob — unlike quiet hours it isn't time-bound, it's a standing
preference.

Two pieces of config drive the scheduled window:

```json
{
  "quiet_hours": "23:00-08:00",
  "quiet_hours_silences": ["sound", "voice"]
}
```

* `quiet_hours` is `"HH:MM-HH:MM"` in 24h local time. `start > end`
  wraps midnight, so `"23:00-09:00"` covers a normal overnight. `null`
  disables the schedule entirely. The default ships opinionated
  (`"23:00-08:00"`) so a stock install isn't loud at 02:00.
* `quiet_hours_silences` is the subset of `["sound", "voice", "banner"]`
  that's suppressed while quiet. The default `["sound", "voice"]` mutes
  audio but keeps the banner — you still *see* that Claude is asking,
  you just don't hear it. Add `"banner"` to go fully silent, or list
  only `"voice"` to keep the chime.

The Tools menu's *Pause for 1 hour* writes a sidecar timestamp at
`~/.claude/agent-state.quiet-until`; *Pause until tomorrow morning*
resolves to the next end of the configured window (or 09:00 local if no
window is set). *Resume now* clears the sidecar. The ad-hoc pause is
independent of the schedule — clearing it leaves any active scheduled
window in effect.

While the scheduled window is active, two extra entries appear:
*Bypass until window ends* and *Cancel bypass*. The bypass writes
`~/.claude/agent-state.quiet-bypass-until` pinned to the end of the
current window — notifications then fire normally until the window
closes, at which point the sidecar timestamp goes stale and the
plugin treats the bypass as gone. Useful when you're up late on
deadline and want the menu to behave normally for the rest of *this*
night without editing `quiet_hours`. *Pause* always wins over
*bypass* when both are held — "do not bother me" beats "do bother
me even during quiet", and the status line surfaces both
remaining durations so the contradiction is visible.

DST: window endpoints are wall-clock local. On a spring-forward day the
"missing" minute simply doesn't exist; the next minute is back inside
the window and the hook silences notifications normally.

## Keep awake

A reconciled `caffeinate -i` lifecycle lives under *Tools → Keep awake*:

```
Tools
  Keep awake: auto · holding while 2 working
    ✔ Off
       Auto (keep awake while sessions are running)
       Always (keep awake until disabled)
```

Three modes:

| Mode | Behaviour |
|---|---|
| `off` *(default)* | Never hold awake. |
| `auto` | Hold awake while any session is in `working` state — including subagent rollups from `Task` spawns. Waiting on a permission prompt does *not* count: if the user is away there's nothing for the screen to be lit for. |
| `always` | Hold awake until disabled. |

The plugin owns one detached `caffeinate -i` process; its PID lives at
`~/.claude/agent-state.caffeinate`. Each tick (~5 s) re-reads the mode
sidecar at `~/.claude/agent-state.keep-awake.mode`, decides whether to
hold, and spawns or kills as needed. PID reuse across reboots is
defended against with a `ps -p <pid> -o comm=` check before any signal
is sent.

**Limits.** `caffeinate -i` inhibits idle and display sleep but does
*not* override macOS's clamshell sleep policy — a closed lid with no
external display still sleeps the Mac. On battery power the hold still
applies (no separate AC/battery gate today).

`Off` while a caffeinate is running tears it down immediately; the next
render tick reflects the new state. `claude-agents-bar teardown` also
kills any caffeinate we own before stripping the symlinks.

The `keep_awake` config knob is only consulted on a clean install —
once you click a mode in the menu, the sidecar wins. Editing config to
flip the mode requires deleting the sidecar (`rm
~/.claude/agent-state.keep-awake.mode`) so config can take over again.

## Multi-workspace focus

The session deeplink (`<scheme>anthropic.claude-code/open?session=<id>`)
carries only the session id. The editor delivers it to whichever window
is **frontmost** — it doesn't route by workspace. With several windows
open that lands the session in the wrong one; in a multi-root workspace,
naively opening the folder would even spawn a brand-new window
([VS Code #215749](https://github.com/microsoft/vscode/issues/215749)).

With **`multi_workspace_mode`** on (the default), a session click — from
a dropdown row *or* a notification banner — first raises the window that
owns the session's working directory, then fires the deeplink so it lands
there. It raises the window by opening a *file* inside the cwd via
`open -a <editor> <file>` (an "open document" event the editor routes to
the owning window, multi-root included), using the session's **last
touched file** as the anchor (so you land where the work was), falling
back to a stable project file like `README`. That costs one extra editor
tab; `editor_focus_settle_sec` is the brief pause that lets the tab
render so the resumed chat ends up on top of it rather than under it.
When the cwd has no file to anchor on yet — a freshly-opened folder with
nothing created in it — it opens the *folder* instead (`open -a <editor>
<cwd>`), which focuses a single-folder window without an extra tab.

Turn `multi_workspace_mode` **off** if you only ever run one editor
window and want the snappiest open: clicks then fire the deeplink
directly — instant, no extra tab — but they land in whatever window is
frontmost. This only ever engages for the built-in editor schemes
(`vscode://`, `vscodium://`, `cursor://`, `windsurf://`, `positron://`);
a custom `editor_url_scheme` always uses the direct path.

You don't have to edit the file to flip it: **Tools → Multi-workspace
mode** is a checkbox that toggles the same behaviour live (it writes a
sidecar at `~/.claude/agent-state.multi-workspace.mode`, which takes
precedence over the config knob — the knob is just the first-launch
default, exactly like *Keep awake*). The checkmark reflects the current
effective state, and the change applies to both dropdown rows and
notification-banner clicks on the next tick.

## Changing the refresh rate

SwiftBar derives the polling cadence from the filename —
`claude-agents.5s.py` means *every 5 seconds*. To poll every 10
seconds, rename the file (and the symlink) to `claude-agents.10s.py`.

5 s is SwiftBar's minimum useful cadence; going lower won't make the
menu more responsive but will cost CPU.

## Examples

Half-hour dropdown window and a tighter watchdog:

```json
{ "window_minutes": 30, "watchdog_seconds": 45 }
```

SF-symbol icon, faster fresh promotion, longer ack window:

```json
{
  "menubar_icon": "sf:bubble.left.fill",
  "fresh_minutes": 30,
  "ack_minutes": 90
}
```

Compact mode for a 14" MacBook with a crowded menu bar:

```json
{ "compact": true }
```

Haiku 4.5 context window:

```json
{ "context_window_tokens": 200000 }
```

VSCodium instead of VSCode:

```json
{ "editor_url_scheme": "vscodium://" }
```

Cursor instead of VSCode:

```json
{ "editor_url_scheme": "cursor://" }
```

Force Russian UI:

```json
{ "language": "ru" }
```

Custom voice lines, no chime on quick turns, banner only on permission prompts:

```json
{
  "notify_on_stop": false,
  "notify_wait_phrases": [
    "Need a human",
    "Permission, captain",
    "Halt and catch fire",
    "Standing by"
  ]
}
```

## Files on disk

Five sidecar files live under `~/.claude/`, maintained by the plugin
and its hook/action scripts:

| File | Writer(s) | Purpose |
|---|---|---|
| `agent-state.tsv` | `hooks/agent-state.sh`, plugin (gc) | One row per session: latest hook state + cwd. |
| `agent-state.subagents.tsv` | `hooks/agent-state.sh`, plugin (gc) | One row per live subagent (`Task` spawn), keyed on `(parent_sid, agent_id)`. Drives the 🤖×N badge and keeps the parent row 🟡 while subagents are in flight. |
| `agent-state.clicks` | `hooks/record-click.sh` (shared writer, via `bin/open-session.sh` row click and `hooks/raise-and-open.sh` banner click), `bin/ack-session.sh`, `bin/ack-fresh.sh` via plugin | `{session_id: click_ts}` — drives 🟢 → 🔵 promotion. |
| `agent-state.dismiss` | `bin/forget-sessions.sh` | Single timestamp; sessions whose latest activity is at or before it are hidden. |
| `agent-state.forget` | `bin/forget-session.sh`, plugin (gc) | `{session_id: forget_ts}` — per-row cutoff. Scoped variant of `agent-state.dismiss`. |
| `agent-state.quiet-until` | `bin/quiet-pause.sh`, `bin/quiet-resume.sh` | Single naive ISO-8601 local timestamp — ad-hoc quiet-hours pause deadline. Absent / past / unparseable = not paused. |
| `agent-state.quiet-bypass-until` | `bin/quiet-bypass.sh`, `bin/quiet-bypass-cancel.sh` | Single naive ISO-8601 local timestamp — opt-in bypass of the scheduled quiet window. Auto-expires at the end of the current window. Pause wins when both are held. |
| `agent-state.keep-awake.mode` | `bin/keep-awake-set.sh` (via plugin) | One line, `off` / `auto` / `always`. Takes precedence over the `keep_awake` config knob once written. |
| `agent-state.caffeinate` | plugin reconcile loop | Single decimal PID of the detached `caffeinate -i` we hold. Cleared on stop / teardown. |

`uninstall.sh` leaves these in place — delete them manually if you
want a fully clean slate. The cache directory
`$XDG_CACHE_HOME/claude-agents-bar/` (re-rasterised menu-bar icons)
is also safe to delete at any time.

Locking and gc semantics for these files: [PLUGIN.md](../PLUGIN.md).
