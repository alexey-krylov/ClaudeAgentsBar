# 0017. Anchor Homebrew symlinks at `opt`, not the versioned Cellar keg

* Status: Accepted
* Date: 2026-06-15

## Context

`claude-agents-bar setup` symlinks two things into the user's tree: the
SwiftBar plugin (`claude-agents.5s.py`) into the SwiftBar plugins folder,
and the Claude Code hooks into `~/.claude/hooks/`. The symlink *target*
is `setup.sh`'s `REPO_DIR` — computed as two levels up from the script.

Under a git clone that's the checkout, and it's stable. Under Homebrew it
is not. The CLI is installed as `$HOMEBREW_PREFIX/bin/claude-agents-bar`,
a symlink into the keg. The dispatcher (`bin/claude-agents-bar`) walks the
symlink chain with `cd -P` to find where `setup.sh` really lives, which
resolves to the **versioned keg**:

```
$HOMEBREW_PREFIX/Cellar/claude-agents-bar/<version>/libexec
```

So `setup` symlinked the plugin and hooks at a path containing the version
number. The next `brew upgrade` installs a new keg and **deletes the old
one** — at which point every symlink `setup` wrote dangles. The plugin
vanishes from the menu bar and the hooks stop firing until the user
re-runs `setup`. This was the "stops working / doesn't always come up
correctly after an upgrade" report.

Homebrew already solves this for its own linkage: `$HOMEBREW_PREFIX/opt/<formula>`
is a symlink it **repoints to the current keg on every upgrade**. Anything
anchored at `opt` follows the upgrade automatically.

Auto-running `setup` from the formula's `post_install` was considered and
rejected: it would silently rewrite `~/.claude/settings.json` (and drop a
timestamped backup) on every upgrade, which contradicts the project's
"confirm before touching `~/.claude`" rule, is discouraged by Homebrew
(formulae shouldn't mutate files outside the prefix), and only treats the
symptom — the symlinks would still be re-pointed at the new *Cellar* path,
re-breaking on the upgrade after.

## Decision

When `setup.sh` detects that `REPO_DIR` is a Homebrew Cellar keg
(`*/Cellar/claude-agents-bar/*/libexec`), it re-anchors `REPO_DIR` to the
stable `opt` prefix before computing any symlink source:

```
REPO_DIR = "$(brew --prefix claude-agents-bar)/libexec"
```

`brew --prefix` is the canonical lookup; if `brew` isn't on `PATH` the
script derives the same path from the Cellar layout by string surgery
(`${REPO_DIR%%/Cellar/claude-agents-bar/*}/opt/claude-agents-bar`). Either
way the rewrite only happens when the resulting `opt/libexec/claude-agents.5s.py`
actually exists; otherwise `REPO_DIR` is left as the Cellar path (no worse
than before). Outside Homebrew the `case` doesn't match and a git checkout
is untouched.

`teardown.sh` needs no change: it removes the symlinks by their
destination path, indifferent to where they point.

## Consequences

* A Homebrew install survives `brew upgrade` with **no re-run of `setup`** —
  the `opt` symlink Homebrew repoints carries the plugin and hooks to the
  new version automatically.
* `setup` must be run **once more** after upgrading to the first version
  that ships this change, to convert the existing Cellar-anchored symlinks
  to `opt`-anchored ones. From then on upgrades are transparent.
* `settings.json` hook *registrations* point at the `opt` hook path, so
  they keep resolving across upgrades too. A future version that changes
  the registration *shape* (argument/path rename) still needs a `setup`
  re-run — but that's rare and unrelated to the upgrade-breakage here.
* The formula stays free of `post_install` side effects on the user's
  dotfiles, in line with Homebrew guidance and ADR-style "confirm before
  touching `~/.claude`".
