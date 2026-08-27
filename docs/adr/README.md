# Architecture Decision Records

This directory captures the *why* behind ClaudeAgentsBar's structural
choices — the kind of context that doesn't fit in a code comment but is
needed before you can confidently change a load-bearing piece.

We use the [MADR](https://adr.github.io/madr/) template (Markdown ADR).
Each record is **immutable** once accepted: a later decision that
supersedes one is recorded as a new ADR that explicitly references the
prior one.

## Index

| # | Title | Status |
|---|---|---|
| [0001](./0001-swiftbar-plugin-not-native-app.md) | SwiftBar plugin, not a native Swift app | Accepted |
| [0002](./0002-stateless-tick-rendering.md) | Stateless rendering on every tick | Accepted |
| [0003](./0003-hook-driven-sidecar.md) | Hook-driven sidecar TSV for live state | Accepted |
| [0004](./0004-mkdir-lock-vs-flock.md) | `mkdir`-based mutex, not `flock` | Accepted |
| [0005](./0005-whitelist-interactive-entrypoints.md) | Whitelist interactive entrypoints | Accepted |
| [0006](./0006-json-config-stdlib-only.md) | JSON config, stdlib only | Accepted |
| [0007](./0007-project-outside-plugins-folder.md) | Project lives outside the SwiftBar plugins folder | Accepted |
| [0008](./0008-menubar-template-image-with-multirep-tiff.md) | Menu-bar icon: template image, resized via `sips`, stitched as multi-rep TIFF | Accepted |
| [0009](./0009-json-per-locale-i18n.md) | JSON-per-locale i18n, stdlib only | Accepted |
| [0010](./0010-compact-menubar-ansi-bullets.md) | Compact menu-bar mode: ANSI-coloured `●` bullets | Accepted |
| [0011](./0011-configurable-context-window.md) | Context-window total comes from config, not auto-detection | Accepted |
| [0012](./0012-open-config-from-menu.md) | Open the config file from the Tools menu | Accepted |
| [0013](./0013-completion-notification-hook.md) | Completion-notification hook bundled in the repo | Accepted |
| [0014](./0014-no-vscode-hidden-sessions-sync.md) | Don't sync with VSCode's hiddenSessionIds | Accepted |
| [0015](./0015-python-as-plugin-language.md) | Python as the plugin language | Accepted |
| [0016](./0016-multi-workspace-window-focus.md) | Multi-workspace window focus via open-document, not the deeplink alone | Accepted |
| [0017](./0017-symlink-homebrew-opt-not-cellar.md) | Anchor Homebrew symlinks at `opt`, not the versioned Cellar keg | Accepted |
| [0018](./0018-usage-sensor-statusline-chain.md) | Source subscription usage from a hidden background `claude` session | Superseded by 0020 |
| [0020](./0020-usage-via-sdk-get-usage.md) | Subscription usage via the SDK `get_usage` control request | Accepted |
| [0021](./0021-passive-rows-ansi-grey.md) | Grey passive menu rows with ANSI so they stay unselectable | Accepted |

## Workflow

* New decision → new file `NNNN-kebab-case-title.md` with the next
  available number.
* Status starts as **Proposed** during discussion; flip to **Accepted**
  when implemented.
* Superseding an old decision → new ADR with status **Accepted**;
  the old one gets its status flipped to **Superseded by ADR-NNNN**.
* Keep each ADR under a page. If it can't fit, the decision is probably
  two decisions.
