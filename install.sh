#!/usr/bin/env bash
set -e

REPO_RAW_URL="https://raw.githubusercontent.com/DOXX-A/agy-token-hud/master"
INSTALL_DIR="$HOME/.antigravity-tokens-hud"

echo "=========================================="
echo "  🚀 Antigravity Tokens HUD Installer     "
echo "  Real-time Token & Quota Visualizer      "
echo "=========================================="
echo ""

# 1. Check Antigravity 2.0
echo "🔍 Checking for Google Antigravity 2.0..."
IS_INSTALLED=0

if [[ "$OSTYPE" == "darwin"* ]]; then
  if [ -d "/Applications/Antigravity.app" ] || [ -d "$HOME/Applications/Antigravity.app" ] || [ -d "$HOME/Library/Application Support/Antigravity" ]; then
    IS_INSTALLED=1
  fi
else
  if command -v antigravity >/dev/null 2>&1 || [ -d "$HOME/.config/Antigravity" ] || [ -d "/opt/Antigravity" ] || [ -d "/mnt/c/Users" ]; then
    IS_INSTALLED=1
  fi
fi

if [ $IS_INSTALLED -eq 0 ]; then
  echo ""
  echo "❌ Google Antigravity 2.0 is not installed!"
  echo "👉 Please install Antigravity 2.0 first: https://antigravity.google/download"
  echo ""
  exit 1
fi
echo "✅ Antigravity 2.0 detected."

# 2. Check Node.js
echo "🔍 Checking Node.js..."
if ! command -v node >/dev/null 2>&1; then
  echo "❌ Node.js is not installed. Please install Node.js (v18+): https://nodejs.org/"
  exit 1
fi
echo "✅ Node.js ($(node -v)) ready."

# 3. Check Python 3
echo "🔍 Checking Python 3..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ Python 3 is not installed. Please install Python 3 (3.8+): https://python.org/"
  exit 1
fi
echo "✅ Python 3 ($(python3 --version | cut -d' ' -f2)) ready."

# 4. Install files
echo "📦 Installing files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
if [ -f "$SCRIPT_DIR/index.js" ] && [ -f "$SCRIPT_DIR/client_widget.js" ] && [ -f "$SCRIPT_DIR/token_stats.py" ]; then
  cp "$SCRIPT_DIR/index.js" "$INSTALL_DIR/"
  cp "$SCRIPT_DIR/client_widget.js" "$INSTALL_DIR/"
  cp "$SCRIPT_DIR/token_stats.py" "$INSTALL_DIR/"
  cp "$SCRIPT_DIR/package.json" "$INSTALL_DIR/" 2>/dev/null || true
  cp "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/" 2>/dev/null || true
else
  echo "⬇️ Downloading latest release files..."
  curl -fsSL "$REPO_RAW_URL/index.js" -o "$INSTALL_DIR/index.js"
  curl -fsSL "$REPO_RAW_URL/client_widget.js" -o "$INSTALL_DIR/client_widget.js"
  curl -fsSL "$REPO_RAW_URL/token_stats.py" -o "$INSTALL_DIR/token_stats.py"
  curl -fsSL "$REPO_RAW_URL/package.json" -o "$INSTALL_DIR/package.json" || true
  curl -fsSL "$REPO_RAW_URL/uninstall.sh" -o "$INSTALL_DIR/uninstall.sh" || true
fi

chmod +x "$INSTALL_DIR/index.js"
chmod +x "$INSTALL_DIR/token_stats.py"
[ -f "$INSTALL_DIR/uninstall.sh" ] && chmod +x "$INSTALL_DIR/uninstall.sh"
[ ! -f "$INSTALL_DIR/config.json" ] && echo '{"lang":"zh"}' > "$INSTALL_DIR/config.json"

# 5. Service setup
NODE_PATH="$(command -v node)"

if [[ "$OSTYPE" == "darwin"* ]]; then
  PLIST_DIR="$HOME/Library/LaunchAgents"
  PLIST_FILE="$PLIST_DIR/com.antigravity.tokens-hud.plist"
  mkdir -p "$PLIST_DIR"

  launchctl unload "$PLIST_FILE" 2>/dev/null || true
  pkill -f "antigravity-tokens-hud" 2>/dev/null || true
  pkill -f "agy-token-hud" 2>/dev/null || true

  cat <<EOF > "$PLIST_FILE"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.antigravity.tokens-hud</string>
    <key>ProgramArguments</key>
    <array>
        <string>$NODE_PATH</string>
        <string>$INSTALL_DIR/index.js</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/antigravity-tokens-hud.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/antigravity-tokens-hud.err.log</string>
</dict>
</plist>
EOF

  launchctl load -w "$PLIST_FILE"
  echo "✅ macOS LaunchAgent registered and started."

elif [[ "$OSTYPE" == "linux"* ]] && command -v systemctl >/dev/null 2>&1; then
  SERVICE_DIR="$HOME/.config/systemd/user"
  SERVICE_FILE="$SERVICE_DIR/antigravity-tokens-hud.service"
  mkdir -p "$SERVICE_DIR"

  cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Antigravity Tokens HUD Daemon
After=network.target

[Service]
Type=simple
ExecStart=$NODE_PATH $INSTALL_DIR/index.js
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload 2>/dev/null || true
  systemctl --user enable antigravity-tokens-hud.service 2>/dev/null || true
  systemctl --user restart antigravity-tokens-hud.service 2>/dev/null || true
  echo "✅ Linux systemd service registered and started."
else
  # Background nohup fallback (e.g. WSL without systemd)
  nohup "$NODE_PATH" "$INSTALL_DIR/index.js" >/tmp/antigravity-tokens-hud.log 2>&1 &
  echo "✅ Started in background via nohup."
fi

echo ""
echo "=========================================="
echo "🎉 SUCCESS! Antigravity Tokens HUD is ready!"
echo "✨ Open Antigravity 2.0 — HUD is active above Settings."
echo "=========================================="
