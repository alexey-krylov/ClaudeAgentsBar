#!/bin/bash
#
# Back-compat wrapper. Delegates to `claude-agents-bar setup`. Kept so that
# existing instructions ("bash install.sh") keep working after the lifecycle
# was moved into a single CLI dispatcher.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$HERE/bin/claude-agents-bar" setup "$@"
