#!/usr/bin/env bash
set -Eeuo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bridge_dir="${OPENCODE_PUTER_BRIDGE_DIR:-$HOME/.local/share/opencode-puter-bridge}"
bin_dir="$HOME/.local/bin"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
tui_config="$config_dir/tui.json"
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
    existing_opencode=""
    for candidate in /usr/local/bin/opencode /usr/bin/opencode; do
        if [[ -x "$candidate" && "$candidate" != "$bin_dir/opencode" ]]; then
            existing_opencode="$candidate"
            break
        fi
    done
    if [[ -z "$existing_opencode" ]]; then
        existing_opencode="$(command -v opencode || true)"
    fi
    if [[ -n "$existing_opencode" && "$existing_opencode" != "$bin_dir/opencode" ]]; then
        real_opencode="$existing_opencode"
    else
        printf 'Installing OpenCode…\n'
        curl -fsSL https://opencode.ai/install | bash
    fi
fi

mkdir -p "$bridge_dir" "$bin_dir" "$config_dir"

configured_port=""
if [[ -f "$config_dir/opencode.jsonc" ]]; then
    configured_port="$(sed -nE 's#.*127\.0\.0\.1:([0-9]+)/v1.*#\1#p' "$config_dir/opencode.jsonc" | head -n 1)"
fi

bridge_port="${PUTER_BRIDGE_PORT:-${configured_port:-8765}}"
if [[ -n "$configured_port" && -z "${PUTER_BRIDGE_PORT:-}" ]]; then
    # Keep updates aligned with the configuration, even if the bridge is running.
    :
elif [[ -z "${PUTER_BRIDGE_PORT:-}" ]]; then
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
printf 'OPENCODE_REAL_BIN=%q\n' "$real_opencode" >> "$bridge_dir/bridge-settings.sh"

if [[ ! -e "$config_dir/opencode.jsonc" ]]; then
    install -m 0644 "$source_dir/templates/opencode.jsonc" "$config_dir/opencode.jsonc"
    sed -i "s#127.0.0.1:8765#127.0.0.1:${bridge_port}#g" "$config_dir/opencode.jsonc"
    printf 'Installed OpenCode configuration.\n'
elif grep -Eq 'Puter \(GLM-4\.7 Flash\)|Puter free models' "$config_dir/opencode.jsonc"; then
    cp -p "$config_dir/opencode.jsonc" "$config_dir/opencode.jsonc.before-puter-update"
    install -m 0644 "$source_dir/templates/opencode.jsonc" "$config_dir/opencode.jsonc"
    sed -i "s#127.0.0.1:8765#127.0.0.1:${bridge_port}#g" "$config_dir/opencode.jsonc"
    printf 'Updated the Puter configuration (backup: opencode.jsonc.before-puter-update).\n'
else
    printf 'Kept existing OpenCode configuration: %s\n' "$config_dir/opencode.jsonc"
fi

# Remove the selector used by older releases. The master routes work across
# the code, reasoning, and vision pools while keeping a shared seven-agent cap.
if [[ -f "$tui_config" ]]; then
    python3 - "$tui_config" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    config = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
plugins = config.get("plugin")
if not isinstance(plugins, list):
    raise SystemExit(0)
filtered = [item for item in plugins if "subagent-selector.ts" not in str(item)]
if filtered != plugins:
    config["plugin"] = filtered
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print("Removed the obsolete /subagent selector.")
PY
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
