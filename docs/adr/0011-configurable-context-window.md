# 0011. Context-window total comes from config, not auto-detection

* Status: Accepted
* Date: 2026-05-14

## Context

The per-session submenu carries a `{N}% — {used}k/{total}k` indicator
(added in the same change as this ADR). The numerator — current
context size — is parsed straight from the freshest `usage` block in
the JSONL (`input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`). The denominator — the model's context
window — is harder: neither the Anthropic API response nor the Claude
Code transcript carries it. The JSONL records `message.model`
("claude-opus-4-7", "claude-sonnet-4-6", …) but nothing about the
window size.

The current Claude lineup is genuinely heterogeneous on this axis as
of May 2026:

| Model | Context window |
|-------|---------------:|
| Opus 4.7, Opus 4.6, Sonnet 4.6 | 1,000,000 |
| Sonnet 4.5 | 200,000 |
| Haiku 4.5 | 200,000 |

So a single hardcoded denominator is wrong for *some* user no matter
which value we pick. And the value can flip mid-workspace — the user
switches from Opus 4.7 to Haiku 4.5 with `/model` and the indicator
should follow.

## Alternatives considered

| # | Approach | Verdict |
|---|----------|---------|
| 1 | **Hardcoded constant in code** | Cheap, but wrong for some user no matter which number we pick (1M for Haiku users, 200K for Opus users). No way for the user to fix it without editing source. Rejected as the only mechanism. |
| 2 | **In-code `{model: window}` table** keyed off `message.model` | Auto-picks 1M for Opus 4.7 / Sonnet 4.6, 200K for Haiku 4.5. Honest for the vanilla case, but requires shipping a code update each time Anthropic adds a model or flips a default (Opus' default flipped *to* 1M on 2026-04-23 — we'd have shipped a release for that). Doesn't cover non-default beta flags either. Rejected as primary. |
| 3 | **Call Anthropic's `GET /v1/models/{id}`** | Would introduce an API-key requirement (the plugin currently needs zero credentials), a network round-trip, and — based on the public API surface as of 2026-05 — wouldn't actually help: the endpoint returns `display_name` and `created_at`, not the context window. Rejected. |
| 4 | **Reverse-engineer the Claude Code CLI bundle** to extract the lookup table the CLI itself uses | The CLI has to know these numbers (it shows "X% context left" in its own UI). But the bundle path varies by install method (npm global, volta, asdf, Homebrew tap, Mac app), the table layout has no compatibility guarantee, and any CLI upgrade can silently break us. Rejected as too fragile. |
| 5 | **Config knob in `config.json`** with default `1_000_000` | Accepted. Default matches the current Anthropic API default model (Opus 4.7) and what most Claude Code users see out of the box. Users on Haiku 4.5 / Sonnet 4.5 override down to `200_000`. Cost: the user has to remember to flip the knob if they switch models — acceptable because most users stay on one tier. |
| 6 | **5 + 2 hybrid** (auto-detect from model name, override via config) | Tempting; the model-table covers most users without config edits, the override covers the rest. Deferred — the table maintenance burden is real (an upstream default change requires a code release) and the marginal value is small compared with telling the user "set this to 200000 if you switched to Haiku". Stays an option for a follow-up if config friction proves load-bearing. |

## Decision

Adopt alternative #5. Add a single `context_window_tokens` field to
`Config` (default `1_000_000` — matches Opus 4.7 / Opus 4.6 /
Sonnet 4.6, the current Anthropic API default tier). The JSON loader
rejects ≤ 0 with a warning and falls back to the default. The
indicator's `_format_context_left` function takes `total` as a required
parameter so tests pin it explicitly and so the call site at render
time pulls `CONFIG.context_window_tokens` rather than reading a module
global.

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

* A user who switches between Opus/Sonnet (1M) and Haiku (200K)
  within the same workspace has to remember to re-edit the config.
  We don't read `message.model` to override per-session.
* The default tracks "what most users on Claude Code see today"
  (Opus 4.7 has been the API default since 2026-04-23). If Anthropic
  changes the default tier or ships a new family with a different
  window, the default will silently mis-classify those sessions
  until the project default is bumped or the user adjusts their
  config. Documented in `config.example.json` and the
  `Config.context_window_tokens` docstring.

If those costs ever become load-bearing (multiple Claude Code users
running mixed model tiers in parallel), the hybrid #6 stays
available — `context_window_tokens` would then act as the explicit
override on top of an auto-detected base, which is a strictly larger
version of today's design.

## Related

* [ADR-0006](./0006-json-config-stdlib-only.md) — the stdlib-only
  constraint that ruled out reaching for the Anthropic SDK (alt #3).
