#!/usr/bin/env bash
set -Eeuo pipefail

source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bridge_dir="${OPENCODE_PUTER_BRIDGE_DIR:-$HOME/.local/share/opencode-puter-bridge}"
bin_dir="$HOME/.local/bin"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/opencode"
real_opencode="$HOME/.opencode/bin/opencode"

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
install -m 0644 "$source_dir/puter_bridge.py" "$bridge_dir/puter_bridge.py"
install -m 0644 "$source_dir/puter_bridge.html" "$bridge_dir/puter_bridge.html"
install -m 0755 "$source_dir/run_opencode_puter.sh" "$bridge_dir/run_opencode_puter.sh"
install -m 0755 "$source_dir/opencode-wrapper.sh" "$bin_dir/opencode"

if [[ ! -e "$config_dir/opencode.jsonc" ]]; then
    install -m 0644 "$source_dir/templates/opencode.jsonc" "$config_dir/opencode.jsonc"
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
