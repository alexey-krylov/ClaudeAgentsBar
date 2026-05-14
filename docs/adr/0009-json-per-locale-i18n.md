# 0009. JSON-per-locale i18n, stdlib only

* Status: Accepted
* Date: 2026-05-14

## Context

The menu surfaces user-facing English text in several places: counters in
the menu bar, the *(no title)* placeholder, the right-side status labels
(`working` / `needs you` / `Xm ago`), the *Refresh / Tools / Delete
session…* commands, the *No sessions in the last …* placeholder, the
*Plugin error: …* fallback, and the AppleScript confirm dialog that
`bin/delete-session.sh` shows on deletion. Roughly 25 strings, and the
project is run by users who don't all speak English.

SwiftBar localises **plugin metadata** itself via `<xbar.title.<lang>>`
/ `<xbar.desc.<lang>>` headers (it reads `AppleLocale` and picks the
right variant for the About box). It does **not** localise the menu rows
the plugin emits — those are whatever bytes our script prints.

Mechanisms considered for the runtime strings:

1. **`gettext` + `.po`/`.mo`.** The canonical Python i18n module. Plus:
   industrial toolchain (Poedit, Crowdin, `xgettext`). Minus: requires a
   build step (`msgfmt` to compile `.mo`), the API is awkward for
   templates with named placeholders, and `install.sh` would have to
   grow a compilation step. Heavyweight for a single-file SwiftBar
   plugin with \~25 strings.
2. **TOML per locale.** Modern, comment-friendly. Blocked by the same
   reason as in [ADR-0006](./0006-json-config-stdlib-only.md): `tomllib`
   is Python 3.11+, system Python on supported macOS is 3.9.
3. **Single JSON file** keyed by locale (`locales.json` with
   `{"en": {...}, "ru": {...}}`). Smaller blast radius for editors, but
   harder to review per-locale PRs and harder to add new languages
   without conflicts.
4. **JSON per locale** (`locales/en.json`, `locales/ru.json`, …). Pure
   stdlib (`json`), no build step, mirrors the de-facto layout of
   modern JS i18n libraries (vue-i18n, react-intl, Next.js), one file
   per language so contributors can submit a new locale in a single
   self-contained PR.
5. **Hard-coded `STRINGS` dict in the plugin.** Where we started. Fine
   for two locales, gets unwieldy past four. Couples translation churn
   to plugin commits.

A second decision is **where translation lookups happen**. The
AppleScript dialog in `bin/delete-session.sh` also needs localised
strings; we don't want to duplicate the tables on the shell side.

## Decision

Translations live in `locales/<lang>.json`, colocated with
`claude-agents.5s.py`. The plugin loads every file at import time via
`_load_strings()` into a `{lang: {key: template}}` dict.

* **Flat keys** with an `area.specific` namespace (`menu.refresh`,
  `dialog.delete.body`). Translators see all keys at one indentation;
  `_t("menu.refresh")` is the only API.
* **`_meta` block** in each file (locale code, native name, fallback)
  is dropped at load time — purely documentation for translators.
* **Placeholders** use Python `str.format` syntax (`{n}`, `{title}`,
  `{sid}`, `{duration}`, `{exc}`). Identical across locales — the
  renderer passes the same kwargs regardless of language.
* **English is the source of truth.** Every other locale falls back
  per-key to `en.json`. A missing key in a translation degrades to the
  English string, not to a stack trace.
* **Locale resolution** consults `CONFIG.language` first (an explicit
  config override, supports `"auto"`), then `defaults read -g
  AppleLocale` (macOS GUI locale, the canonical source for launchd-
  spawned GUI apps), then `$LANG`. Unknown codes fall back to `en`.
* **Shell side** doesn't reload the JSON. The plugin exposes
  `--print-strings` which emits shell-quoted `MSG_*` variables for the
  `dialog.*` subset; `bin/delete-session.sh` does
  `eval "$(/usr/bin/python3 "$PLUGIN" --print-strings)"`. Single source
  of truth, no duplication, no shell-side string tables.

Symlinked install: `Path(__file__).resolve()` follows the symlink that
`install.sh` creates, so `locales/` next to the *source* file is what
gets read — no need to copy the directory into the SwiftBar plugins
folder.

## Consequences

**Wins:**

* Stdlib only — no `gettext`, no `msgfmt`, no third-party deps. Matches
  [ADR-0006](./0006-json-config-stdlib-only.md).
* No build step. Editing `locales/ru.json` is live on the next 5 s tick,
  same iteration loop as editing the plugin itself.
* New languages ship as one self-contained PR (a new `locales/<lang>.json`)
  with no plugin code changes.
* Graceful degradation at every layer: missing file → other locales
  unaffected; missing key → English fallback; missing `en.json` →
  literal key shown (visibly broken but diagnosable, not crashed).
* The shell side stays in sync without duplicating strings:
  `--print-strings` exports localised dialog text via `shlex.quote` so
  multi-line values with apostrophes/quotes survive the bash boundary.

**Costs:**

* No proper plural rules (Russian "5 минут" vs "1 минута"). We dodge
  this by using abbreviated forms with no trailing word (`5м`, `1м`,
  `2ч 13м`) where it matters — the "ago" / "назад" / "前" suffix is
  dropped on purpose since the surrounding UI (right side of a
  finished-session row, "No sessions in the last X" placeholder)
  already implies the meaning. If we ever need full plurals, that's a
  follow-up — not a reason to bring in `gettext` upfront.
* JSON has no native comments. `_meta` covers the per-locale header;
  per-key comments aren't supported. Acceptable; the keys are
  self-descriptive.
* Locale tables are loaded eagerly into memory on every tick. About
  3 KB per locale × 6 locales — negligible.

## Related

* [ADR-0006](./0006-json-config-stdlib-only.md) — same stdlib-only,
  JSON-on-disk principle, applied to user config rather than UI strings.
