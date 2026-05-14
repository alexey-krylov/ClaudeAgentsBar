# 0011. Context-window total comes from config, not auto-detection

* Status: Accepted
* Date: 2026-05-14

## Context

The per-session submenu carries a `{N}% — {used}k/200k` indicator (added
in the same change as this ADR). The numerator — current context size —
is parsed straight from the freshest `usage` block in the JSONL
(`input_tokens + cache_creation_input_tokens + cache_read_input_tokens`).
The denominator — the model's context window — is harder: neither the
Anthropic API response nor the Claude Code transcript carries it. The
JSONL records `message.model` ("claude-opus-4-7", "claude-sonnet-4-6",
…) but nothing about the window size.

Worse, the *real* window depends not just on the model but on whether
the client opted into a beta. Sonnet 4 supports 200K by default and 1M
behind the `context-1m-2025-08-07` beta flag; transcripts don't record
which flags were active for a request. So even a perfect
model-name → window lookup would mis-classify any 1M-beta session as
200K.

## Alternatives considered

| # | Approach | Verdict |
|---|----------|---------|
| 1 | **Hardcoded constant in code** (200K) | What the indicator originally shipped with. Cheap, but wrong for any non-200K deployment with no way for the user to fix it without editing source. Rejected as the only mechanism. |
| 2 | **In-code `{model: window}` table** keyed off `message.model` | Honest for the vanilla case (auto-pick 200K vs Haiku 200K vs etc.) but doesn't help the 1M-Sonnet beta case at all (same model name, different window). Still requires shipping a code update each time Anthropic adds a model. Rejected as primary. |
| 3 | **Call Anthropic's `GET /v1/models/{id}`** | Would introduce an API-key requirement (the plugin currently needs zero credentials), a network round-trip, and — based on the public API surface as of 2026-05 — wouldn't actually help: the endpoint returns `display_name` and `created_at`, not the context window. Rejected. |
| 4 | **Reverse-engineer the Claude Code CLI bundle** to extract the lookup table the CLI itself uses | The CLI has to know these numbers (it shows "X% context left" in its own UI). But the bundle path varies by install method (npm global, volta, asdf, Homebrew tap, Mac app), the table layout has no compatibility guarantee, and any CLI upgrade can silently break us. Rejected as too fragile. |
| 5 | **Config knob in `config.json`** with default 200K | Accepted. The user knows which model they run and whether they have the 1M beta turned on; everyone else gets the 200K default that matches the entire current Claude 4.x family. Cost: the user has to remember to flip the knob if they switch models. |
| 6 | **5 + 2 hybrid** (auto-detect from model name, override via config) | Tempting, but the auto path solves only the "what model" axis without solving the more interesting "what beta flags" axis, and adds a model-table maintenance burden. Deferred until a concrete case shows up where the auto-detect would meaningfully reduce config friction. |

## Decision

Adopt alternative #5. Add a single `context_window_tokens` field to
`Config` (default `200_000`, JSON loader rejects ≤ 0 with a warning and
falls back to the default). The indicator's `_format_context_left`
function takes `total` as a required parameter so tests pin it
explicitly and so the call site at render time pulls
`CONFIG.context_window_tokens` rather than reading a module global.

No in-code model table; no API call; no bundle parsing.

## Consequences

**Wins:**

* Zero new dependencies (still stdlib only — see ADR-0006).
* No new credentials; the plugin remains usable on machines that have
  Claude.app but no Anthropic API key.
* The single knob covers both axes that move the window — model choice
  *and* beta flags — without us having to enumerate either.
* Invalid config values (`0`, negative, non-numeric) are loud rather
  than silent: they warn to SwiftBar's log and keep the 200K default.

**Costs:**

* A user who switches between 200K and 1M Sonnet within the same
  workspace has to remember to re-edit the config. We don't read
  `message.model` to override per-session.
* The default is right for the current Claude 4.x family; if Anthropic
  ever ships a default-window-other-than-200K model, the default will
  silently mis-classify it until either the project default is bumped
  or the user adjusts their config. Documented in
  `config.example.json` and the `Config.context_window_tokens`
  docstring.

If the second cost ever becomes load-bearing (a single user running
multiple windows in parallel without wanting to touch config), the
hybrid #6 stays available — `context_window_tokens` would then act as
the explicit override on top of an auto-detected base, which is a
strictly larger version of today's design.

## Related

* [ADR-0006](./0006-json-config-stdlib-only.md) — the stdlib-only
  constraint that ruled out reaching for the Anthropic SDK (alt #3).
