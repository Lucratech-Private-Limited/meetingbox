#!/usr/bin/env bash
# Same as run_device_ui.sh — convenience alias for "run device interface".
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/run_device_ui.sh" "$@"
