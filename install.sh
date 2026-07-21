#!/usr/bin/env bash
set -Eeuo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bridge_dir="${OPENCODE_PUTER_BRIDGE_DIR:-$HOME/.local/share/opencode-puter-bridge}"
bin_dir="$HOME/.local/bin"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
real_opencode="$HOME/.opencode/bin/opencode"

port_available() {
    python3 - "$1" <<'PY'
import socket
import sys

with socket.socket() as listener:
    try:
        listener.bind(("127.0.0.1", int(sys.argv[1])))
    except OSError:
        raise SystemExit(1)
PY
}

for dependency in git curl python3 openssl; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        printf 'Missing dependency: %s\n' "$dependency" >&2
        exit 1
    fi
done

if [[ ! -x "$real_opencode" ]]; then
    printf 'Installing OpenCode…\n'
    curl -fsSL https://opencode.ai/install | bash
fi

mkdir -p "$bridge_dir" "$bin_dir" "$config_dir"

bridge_port="${PUTER_BRIDGE_PORT:-8765}"
if [[ -z "${PUTER_BRIDGE_PORT:-}" ]]; then
    while ! port_available "$bridge_port"; do
        bridge_port=$((bridge_port + 1))
        if (( bridge_port > 8799 )); then
            printf 'No free bridge port found between 8765 and 8799.\n' >&2
            exit 1
        fi
    done
elif ! port_available "$bridge_port"; then
    printf 'Requested bridge port is already in use: %s\n' "$bridge_port" >&2
    exit 1
fi

install -m 0644 "$source_dir/puter_bridge.py" "$bridge_dir/puter_bridge.py"
install -m 0644 "$source_dir/puter_bridge.html" "$bridge_dir/puter_bridge.html"
install -m 0755 "$source_dir/run_opencode_puter.sh" "$bridge_dir/run_opencode_puter.sh"
install -m 0755 "$source_dir/opencode-wrapper.sh" "$bin_dir/opencode"
printf 'PUTER_BRIDGE_PORT=%q\n' "$bridge_port" > "$bridge_dir/bridge-settings.sh"

if [[ ! -e "$config_dir/opencode.jsonc" ]]; then
    install -m 0644 "$source_dir/templates/opencode.jsonc" "$config_dir/opencode.jsonc"
    sed -i "s#127.0.0.1:8765#127.0.0.1:${bridge_port}#g" "$config_dir/opencode.jsonc"
    printf 'Installed OpenCode configuration.\n'
else
    printf 'Kept existing OpenCode configuration: %s\n' "$config_dir/opencode.jsonc"
fi

path_line='export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"'
if [[ -f "$HOME/.bashrc" ]] && ! grep -Fqx "$path_line" "$HOME/.bashrc"; then
    printf '\n# OpenCode Puter bridge\n%s\n' "$path_line" >> "$HOME/.bashrc"
fi

printf '\nInstalled successfully.\n'
printf 'Restart the terminal or run: source ~/.bashrc\n'
printf 'Puter mode: opencode\n'
printf 'Normal mode: opencode -n\n'
printf 'Local bridge port: %s\n' "$bridge_port"
