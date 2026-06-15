#!/bin/bash
#
# Session-click action: raise the editor window that holds the session's
# working directory, wait for it to actually come to front, then open the
# session deeplink.
#
# Why this exists: the deeplink carries only the session id, and the
# editor's anthropic.claude-code handler delivers it to whichever window
# is *frontmost* — not the one whose workspace matches the session. With
# several windows open, clicking would otherwise resume in the wrong
# window (and can miss entirely, since the session must belong to the
# focused workspace). Surfacing the matching window first fixes that.
#
# How it surfaces the window: preferably by opening a *file* inside <cwd>
# with `open -a <app> <file>`, falling back to the *folder* when no file is
# anchorable. The file-vs-folder distinction is the trick:
#   * `open -a <app> <file>` sends an "open document" Apple Event, which
#     the editor routes to the window whose workspace already contains
#     that file — multi-root included. Same workspace-aware result as
#     `code -g <file>`, but via LaunchServices, so it skips the ~1s
#     node-CLI startup and stays near-instant (~0.07s). That speed is why
#     no "is more than one window open?" check is needed — the focus step
#     is cheap enough to always run. This is the preferred path.
#   * `open -a <app> <cwd>` / `code <cwd>` are folder-identity based. For a
#     single-folder window VS Code focuses the window already holding <cwd>
#     (its documented reuse behaviour) — which is exactly what we want when
#     <cwd> has no anchorable file yet (a freshly-opened folder with nothing
#     created in it). The one caveat: if <cwd> is one root of an already-open
#     *multi-root* workspace, this spawns a brand-new single-folder window
#     instead of focusing the existing one (VS Code issue #215749). A
#     brand-new empty folder is single-root in practice, so the folder path
#     is the right recovery for the common "new window, no files yet" case —
#     and it needs no Accessibility/Automation permission. We only reach it
#     when there's no file to anchor on, so the multi-root hazard stays rare.
#
# Anchor choice: the deeplink itself can't be the anchor — a Claude Code
# session tab is a virtual extension document, not a file we can target,
# and its on-disk transcript lives under ~/.claude/projects (outside
# <cwd>, so it wouldn't surface the right window). Instead we use the last
# real file Claude touched in this session (newest tool_use `file_path`
# inside <cwd>, read from the transcript) — that both surfaces the correct
# window AND lands you on the file the work was about. Falls back to a
# stable project file (README, …) when the transcript has no usable path,
# and finally to opening <cwd> itself when the folder holds no file at all.
# The cost is one editor tab (none for the folder fallback); the deeplink
# then focuses the session on top.
#
# Two callers, both clicks that resume a session in the editor:
#   * the menu-bar dropdown row — bin/app/open-session.sh runs this
#     detached after recording the click;
#   * a notification banner — hooks/notify-stop.sh / notify-wait.sh wire
#     it into terminal-notifier's -execute via _raise_open_cmd in
#     _notify-common.sh.
# Standalone (no sourcing) because -execute spawns a bare shell, not a
# child of the hook, and open-session.sh invokes it the same way.
#
#     raise-and-open.sh <session-url> [cwd] [editor-app] [session-id] [settle-sec]
#
# <settle-sec> is the post-raise pause before the deeplink (see below);
# defaults to 0.1 when blank/invalid. Best-effort throughout: a
# blank/unknown cwd or app skips the focus step and opens the deeplink
# directly (the pre-fix behaviour: it lands in the frontmost window). A
# cwd with no anchorable file no longer skips — it now raises the window
# by opening the folder itself (see "How it surfaces the window" above).

set -u

URL="${1:-}"
CWD="${2:-}"
APP="${3:-}"
SID="${4:-}"
SETTLE="${5:-}"

[ -n "$URL" ] || exit 0

# Sanitise the settle delay: a non-numeric value would make `sleep` error
# out (skipping the pause and losing the race). The plugin already clamps
# the config value to 0..5; this just guards a blank/garbage arg.
case "$SETTLE" in
    ''|*[!0-9.]*) SETTLE="0.1" ;;
esac

# The newest file Claude touched in this session that still lives inside
# <cwd>. Reads the session transcript (~/.claude/projects/*/<sid>.jsonl),
# walking it newest-first and taking the first tool_use `file_path` under
# <cwd> that still exists. Echoes a path or nothing. Mirrors the tool_use
# shape sidecars.last_tool_use_summary parses in Python.
_session_last_file() {
    local cwd="$1" sid="$2" t f
    [ -n "$sid" ] || return
    [ -x /usr/bin/jq ] || return
    for t in "${HOME}/.claude/projects/"*/"${sid}.jsonl"; do
        [ -f "$t" ] || continue
        while IFS= read -r f; do
            [ -n "$f" ] || continue
            case "$f" in
                "${cwd}"/*) [ -f "$f" ] && { printf '%s\n' "$f"; return; } ;;
            esac
        done < <(/usr/bin/tail -r "$t" 2>/dev/null | /usr/bin/jq -r '
            (.message.content // empty)
            | if type == "array" then .[] else empty end
            | select(type == "object" and .type == "tool_use")
            | (.input.file_path // empty)
        ' 2>/dev/null)
        return  # only the first matching transcript
    done
}

# Pick an existing file inside <cwd> to open. Any file in the workspace
# surfaces the owning window; prefer the session's last touched file
# (relevant — you land where the work was), then a stable common project
# file (reused across clicks so the editor focuses the existing tab rather
# than piling new ones up), then the first regular, non-hidden top-level
# file. Echoes a path or nothing.
_anchor_file() {
    local cwd="$1" sid="$2" cand picked
    picked="$(_session_last_file "$cwd" "$sid")"
    if [ -n "$picked" ]; then echo "$picked"; return; fi
    for cand in README.md README.rst README.txt README readme.md \
                package.json pyproject.toml go.mod Cargo.toml \
                CHANGELOG.md .gitignore; do
        if [ -f "${cwd}/${cand}" ]; then echo "${cwd}/${cand}"; return; fi
    done
    /usr/bin/find "$cwd" -maxdepth 1 -type f ! -name '.*' 2>/dev/null \
        | LC_ALL=C sort | head -n 1
}

# Raise the window owning <cwd>. Returns 0 if it issued a focus command
# (so the caller waits for the window), non-zero to skip straight to the
# deeplink.
_raise_window() {
    [ -n "$CWD" ] && [ -n "$APP" ] && [ -d "$CWD" ] && [ -d "$APP" ] || return 1
    local anchor
    anchor="$(_anchor_file "$CWD" "$SID")"
    if [ -n "$anchor" ]; then
        # `open -a <app> <file>` = "open document" Apple Event → routed to the
        # window owning <file>'s workspace (multi-root aware), near-instant.
        /usr/bin/open -a "$APP" "$anchor" >/dev/null 2>&1 || return 1
        return 0
    fi
    # No anchorable file — a freshly-opened folder with nothing created in it
    # yet. Open the *folder*: for a single-folder window VS Code focuses the
    # window already holding <cwd> instead of spawning a new one, recovering
    # the otherwise-unreachable "new window on its own folder, no files yet"
    # case with no Accessibility permission. (Multi-root caveat noted in the
    # header; rare here since we only reach this when no file exists at all.)
    /usr/bin/open -a "$APP" "$CWD" >/dev/null 2>&1 || return 1
    return 0
}

# Record the click (ack) so the plugin clears the session's 🟢 FRESH state
# on its next tick — the banner-click path reaches the editor through here,
# and without this the session would stay green until fresh_sec elapsed even
# though the user already opened it. The menu-row path records the ack via
# open-session.sh and sets CAB_CLICK_RECORDED so we skip the duplicate write
# when it delegates here. Best-effort: a failed ack must not block the open.
if [ -n "$SID" ] && [ -z "${CAB_CLICK_RECORDED:-}" ]; then
    _HELPER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)"
    /bin/bash "${_HELPER_DIR}/record-click.sh" "$SID" >/dev/null 2>&1 || true
fi

if _raise_window; then
    # Wait (≈2 s cap) for that .app to be frontmost before handing over the
    # deeplink — the extension reads the focused window at delivery time, so
    # firing too early races the window switch. lsappinfo needs no
    # Automation/Accessibility permission, unlike System Events, so this
    # stays prompt-free.
    attempts=0
    while [ "$attempts" -lt 25 ]; do
        front_asn="$(/usr/bin/lsappinfo front 2>/dev/null)" || break
        [ -n "$front_asn" ] || break
        front_path="$(/usr/bin/lsappinfo info -only bundlepath "$front_asn" 2>/dev/null)"
        # front_path looks like: "LSBundlePath"="/Applications/VSCodium.app"
        case "$front_path" in
            *\"$APP\"*) break ;;
        esac
        attempts=$((attempts + 1))
        sleep 0.08
    done

    # `open -a <file>` returns before the editor has actually rendered the
    # anchor tab. Without a beat here the deeplink fires first and the
    # anchor tab renders *on top of* the resumed session — you land on the
    # file, not the chat. This short settle lets the anchor tab land so the
    # session ends up focused. Duration comes from the editor_focus_settle_sec
    # config knob (default 0.1); bump it if a cold/slow editor still lands
    # on the file. Fall back to the default if the value is somehow bad.
    sleep "$SETTLE" 2>/dev/null || sleep 0.1
fi

/usr/bin/open "$URL" >/dev/null 2>&1 || true
exit 0
