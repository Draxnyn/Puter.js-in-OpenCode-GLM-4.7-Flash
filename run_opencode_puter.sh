#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

# The token protects the local bridge and is also used as the OpenCode API key.
export PUTER_BRIDGE_TOKEN="${PUTER_BRIDGE_TOKEN:-$(openssl rand -hex 32)}"

state_dir="${XDG_STATE_HOME:-"${HOME}/.local/state"}/opencode"
bridge_log="${state_dir}/puter-bridge.log"
mkdir -p "$state_dir"

# A ponte continua registrando falhas, mas fora do terminal do TUI.
python3 puter_bridge.py >>"$bridge_log" 2>&1 &
bridge_pid=$!

cleanup() {
    kill "$bridge_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

puter_url="http://127.0.0.1:8765/?token=${PUTER_BRIDGE_TOKEN}&concurrency=${PUTER_MAX_CONCURRENT:-2}"
printf 'Open this page, sign in to Puter if needed, and keep it open:\n%s\nBridge logs: %s\n\n' "$puter_url" "$bridge_log"

open_browser() {
    if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null \
        && [[ -x /mnt/c/Windows/System32/cmd.exe ]]; then
        /mnt/c/Windows/System32/cmd.exe /c start "" "$puter_url" >/dev/null 2>&1
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$puter_url" >/dev/null 2>&1
    elif command -v open >/dev/null 2>&1; then
        open "$puter_url" >/dev/null 2>&1
    else
        return 1
    fi
}

open_browser || printf 'The browser could not be opened automatically. Open the URL above manually.\n'

opencode_bin="${OPENCODE_BIN:-opencode}"
"$opencode_bin" "$@"
