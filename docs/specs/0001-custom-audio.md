# Spec 0001 — Custom audio

* Status: **Implemented in 1.1.0**
* Date: 2026-05-26

## Why

The chime is currently hardcoded to `Hero.aiff` (stop) and
`Funk.aiff` (permission). They're fine defaults but should not be
the only options. Letting the user point at any audio file — and
silence the chime independently of the banner and the voice — is a
small change for a high-frequency annoyance.

## What the user sees

No new menu surface. Tools submenu's existing notification status
line absorbs the result:

```
Notifications
  ✔ Sound: Hero · Voice: Samantha · Banner: on
  …
```

Three new config knobs, all optional, defaults preserve current
behaviour.

## Config

```jsonc
{
  "notify_sound_stop": "Hero",         // built-in name, path, or null
  "notify_sound_wait": "Funk",
  "notify_voice": null                 // say -v <voice>; null = default; "off" disables
}
```

### Resolving `notify_sound_*`

| Value | Resolves to |
|---|---|
| `"Hero"` (bare name) | `/System/Library/Sounds/Hero.aiff` |
| `"~/Sounds/foo.aiff"` (path) | Expanded path |
| `"/abs/path/foo.aiff"` | Used as-is |
| `null` | Sound suppressed; banner and voice unaffected |

Anything else (relative path, URL, bare name not in
`/System/Library/Sounds`) — log warning to SwiftBar log, treat as
`null` for this event, continue with banner + voice.

### Resolving `notify_voice`

| Value | Effect |
|---|---|
| `null` | `say <phrase>` — system default voice |
| `"Samantha"` | `say -v Samantha <phrase>` |
| `"off"` (sentinel) | Skip `say` entirely |

We don't pre-validate the voice name; if the voice isn't
installed, `say` errors silently — same behaviour as today.

## Implementation sketch

[hooks/notify-stop.sh](../../hooks/notify-stop.sh) and
[hooks/notify-wait.sh](../../hooks/notify-wait.sh) already read the
plugin's JSON config via `jq`. Add three reads at the top of each:

```bash
SOUND=$(jq -r '.notify_sound_stop // "Hero"' < "$CONFIG")
VOICE=$(jq -r '.notify_voice // empty' < "$CONFIG")
```

Then a small helper to resolve `Hero` → full path, expand `~`, and
fall back when the file doesn't exist.

`afplay "$SOUND_PATH"` stays as today. `say` gains
`${VOICE:+-v "$VOICE"}` argv expansion.

## Edge cases

- Path doesn't exist on disk → log warning to SwiftBar log
  (`echo "..." >&2` in the hook, SwiftBar captures stderr), skip
  the chime, continue with banner + voice. We never take the whole
  notification down on a bad sound file.
- `notify_sound_stop: null` with `notify_on_stop: true` — banner +
  voice still fire; just no chime.
- User's macOS doesn't ship the named voice — `say` exits with
  code 1; we don't surface the error.

## Technical feasibility

**Confidence:** high &nbsp;·&nbsp; **Estimated effort:** ~½ day

**Confirmed:**
- `afplay`, `say`, `jq` are already used by the current hooks. No
  new dependencies, no new external binaries.
- Stdlib `Path.expanduser()` + `Path.is_file()` is enough for path
  resolution. Hooks do the same in bash via `[ -f "$path" ]`.
- Both hooks already read the same JSON config the plugin uses.

**Needs verification:** none.

**Risks:** none material. Worst case a misconfigured path triggers
a `[ -f ]` miss and the hook warns + skips sound — same fall-soft
path as today's missing `terminal-notifier`.

**Mitigations:** existing fail-soft pattern in
[hooks/agent-state.sh](../../hooks/agent-state.sh) already covers
the shape — copy it.
