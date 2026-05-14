#!/bin/bash
#
# Back-compat wrapper. Delegates to `claude-agents-bar teardown`.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$HERE/bin/claude-agents-bar" teardown "$@"
