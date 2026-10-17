#!/bin/sh
# Install the Linux recorder onto this computer.
# From the repo: ./install.sh
# On a Grok Bot computer:
#   curl -fsSL https://raw.githubusercontent.com/isaacdonnelson/screenreel/main/install.sh | sh
set -eu

ORIGIN="https://raw.githubusercontent.com/isaacdonnelson/screenreel/main"
if [ -n "${SCREENREEL_HOME:-}" ]; then
  ROOT="$SCREENREEL_HOME"
elif [ -d /workspace ] && [ -w /workspace ]; then
  ROOT="/workspace/screenreel"
else
  ROOT="${HOME}/.local/share/screenreel"
fi
mkdir -p "$ROOT/linux" "${HOME}/.local/bin"

copy_local() {
  source_dir=$1
  cp "$source_dir/linux/screenreel" "$ROOT/linux/screenreel"
  cp "$source_dir/linux/bridge.py" "$ROOT/linux/bridge.py"
  cp "$source_dir/linux/mcp_server.py" "$ROOT/linux/mcp_server.py"
  cp "$source_dir/linux/api_server.py" "$ROOT/linux/api_server.py"
  mkdir -p "$ROOT/skills/screenreel"
  cp "$source_dir/skills/screenreel/SKILL.md" "$ROOT/skills/screenreel/SKILL.md"
}

if [ -f "$(CDPATH= cd -- "$(dirname "$0")" && pwd)/linux/screenreel" ]; then
  copy_local "$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
else
  for file in linux/screenreel linux/bridge.py linux/mcp_server.py linux/api_server.py skills/screenreel/SKILL.md; do
    mkdir -p "$ROOT/$(dirname "$file")"
    curl -fsSL "$ORIGIN/$file" -o "$ROOT/$file"
  done
fi

chmod +x "$ROOT/linux/screenreel" "$ROOT/linux/mcp_server.py" "$ROOT/linux/api_server.py"

if [ "$(uname)" = "Darwin" ] && [ -x "${HOME}/.local/bin/screenreel" ]; then
  echo "Left the Mac screenreel binary at ${HOME}/.local/bin/screenreel"
  echo "Linux files are in $ROOT"
  exit 0
fi

cat > "${HOME}/.local/bin/screenreel" <<EOF
#!/bin/sh
set -eu
ROOT="$ROOT"
cmd=\${1:-}
case "\$cmd" in
  mcp)
    exec python3 "\$ROOT/linux/mcp_server.py"
    ;;
  api)
    shift
    exec python3 "\$ROOT/linux/api_server.py" "\$@"
    ;;
  *)
    exec python3 "\$ROOT/linux/screenreel" "\$@"
    ;;
esac
EOF
chmod +x "${HOME}/.local/bin/screenreel"

echo "Installed screenreel at ${HOME}/.local/bin/screenreel"
echo "Files are in $ROOT"
echo "Add ${HOME}/.local/bin to PATH if doctor is not found."
echo "CLI:  screenreel doctor"
echo "MCP:  python3 $ROOT/linux/mcp_server.py"
echo "API:  screenreel api    then curl -s localhost:8765/v1/doctor"
echo "Skill: $ROOT/skills/screenreel/SKILL.md"
