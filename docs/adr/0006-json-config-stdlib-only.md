# 0006. JSON config, stdlib only

* Status: Accepted
* Date: 2026-05-13

## Context

Some plugin knobs need to be tunable by the user: how far back the
dropdown looks, how aggressive the watchdog is, what icon sits on the
menu bar. Mechanisms considered:

1. **TOML config** — modern, supports comments natively. But
   `tomllib` only landed in Python 3.11; SwiftBar uses the system
   `/usr/bin/python3` which is **3.9** on current macOS. Using TOML
   means either bundling `tomli` (third-party dep) or shipping our own
   parser. Both violate the no-deps rule.
2. **YAML** — same dependency problem (`PyYAML` not in stdlib).
3. **`.ini` / `configparser`** — in stdlib but the schema (sections,
   string-typed values, no nesting) is awkward for our use case.
4. **`defaults read` plist** — native to macOS but writing it by hand
   is ugly; users expect a text file.
5. **JSON** — stdlib, widely understood, easy to edit. The only loss
   vs. TOML is comments, but we work around that by silently dropping
   keys starting with `//`.
6. **`<swiftbar.environment>` plugin metadata** — SwiftBar can prompt
   the user for env-var values from a UI. Limited to strings, no
   nesting, no typed validation.

## Decision

Plain JSON, read once at import time into a frozen `Config` dataclass.

* Location follows XDG semantics:
  `$XDG_CONFIG_HOME/claude-agents-bar/config.json`, with
  `~/.config/...` as the fallback and `$CLAUDE_AGENTS_BAR_CONFIG` as
  an explicit override.
* Keys starting with `//` are dropped, allowing JSONC-style comments
  in the file.
* Unknown keys are ignored — forward-compatible files don't break.
* A field with an invalid value falls back to the default for *that
  field only*; everything else still loads. A warning goes to stderr,
  which SwiftBar surfaces under *Show Logs*.

Time-valued knobs are exposed in human units (`window_minutes`,
`fresh_minutes`, `ack_minutes`, `watchdog_seconds`) and converted to
seconds internally. Internal fields are `_sec`-suffixed to avoid unit
confusion.

## Consequences

**Wins:**

* Stdlib only — works with any Python 3.9 available on macOS.
* The frozen dataclass is the single source of truth: defaults and
  schema live in one place, fields are typed, the test suite exercises
  the loader against representative inputs.
* Graceful degradation: a syntax-broken config file produces a stderr
  warning and the menu still renders with defaults.

**Costs:**

* No native comments in JSON; the `//`-key workaround is mildly hacky.
  Acceptable.
* No structural validation beyond per-field type coercion. We don't
  catch `"window_minutes": -1` as a bad value, for instance. Could be
  added later if it turns out to bite.
