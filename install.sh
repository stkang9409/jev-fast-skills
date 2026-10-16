#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# TypeSafe Jev Fast Skills — Universal Installer (Antigravity + Claude Code)
# Supports: macOS & Linux
# ==============================================================================

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGY_PLUGIN_DIR="$HOME/.gemini/config/plugins/jev-fast-skills"
AGY_SKILLS_DIR="$HOME/.gemini/config/skills"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
CLAUDE_PLUGIN_DIR="$HOME/.claude/plugins/jev-fast-skills"

echo "⚡ Installing [jev-fast-skills] for Antigravity & Claude Code..."

# 1. Place or link plugin into ~/.gemini/config/plugins/jev-fast-skills
mkdir -p "$HOME/.gemini/config/plugins" "$AGY_SKILLS_DIR"
if [ "$SRC_DIR" != "$AGY_PLUGIN_DIR" ]; then
  rm -rf "$AGY_PLUGIN_DIR"
  cp -R "$SRC_DIR" "$AGY_PLUGIN_DIR"
fi

# 2. Setup Python Virtual Environment (.venv) inside the plugin
if [ ! -x "$AGY_PLUGIN_DIR/.venv/bin/python" ]; then
  echo "🐍 Creating Python virtual environment in $AGY_PLUGIN_DIR/.venv ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv "$AGY_PLUGIN_DIR/.venv"
    uv pip install --python "$AGY_PLUGIN_DIR/.venv/bin/python" -r "$AGY_PLUGIN_DIR/requirements.txt"
  else
    python3 -m venv "$AGY_PLUGIN_DIR/.venv"
    "$AGY_PLUGIN_DIR/.venv/bin/pip" install --upgrade pip -q
    "$AGY_PLUGIN_DIR/.venv/bin/pip" install -r "$AGY_PLUGIN_DIR/requirements.txt" -q
  fi
  "$AGY_PLUGIN_DIR/.venv/bin/playwright" install chromium || true
fi

# 3. Clean up any legacy loose symlinks in Antigravity (~/.gemini/config/skills) so plugin owns them without duplication
rm -f "$AGY_SKILLS_DIR/fast-context" "$AGY_SKILLS_DIR/fast-memory" "$AGY_SKILLS_DIR/fast-browser" "$AGY_SKILLS_DIR/prompt-engineer"
rm -f "$CLAUDE_SKILLS_DIR/fast-context" "$CLAUDE_SKILLS_DIR/fc-hint.sh" "$CLAUDE_SKILLS_DIR/fast-memory"
cp "$AGY_PLUGIN_DIR/hooks.json" "$HOME/.gemini/config/hooks.json"

# 4. Link skills & rules for Claude Code (~/.claude/skills & ~/.claude/plugins)
mkdir -p "$CLAUDE_SKILLS_DIR" "$HOME/.claude/plugins"
ln -sfn "$AGY_PLUGIN_DIR" "$CLAUDE_PLUGIN_DIR"
for skill in fast-browser prompt-engineer; do
  ln -sfn "$AGY_PLUGIN_DIR/skills/$skill" "$CLAUDE_SKILLS_DIR/$skill"
done
ln -sfn "$AGY_PLUGIN_DIR/rules/AGENTS.md" "$HOME/.claude/CLAUDE.md"

# 5. Check TYPESAFE_API_KEY in ~/.gemini/.env
mkdir -p "$HOME/.gemini"
if [ ! -f "$HOME/.gemini/.env" ] || ! grep -q "TYPESAFE_API_KEY=" "$HOME/.gemini/.env"; then
  if [ -n "${TYPESAFE_API_KEY:-}" ]; then
    echo "TYPESAFE_API_KEY=$TYPESAFE_API_KEY" >> "$HOME/.gemini/.env"
    echo "🔑 Saved TYPESAFE_API_KEY to ~/.gemini/.env"
  else
    echo "⚠️  Note: Add 'TYPESAFE_API_KEY=your_key_here' to ~/.gemini/.env to enable Jev API calls."
  fi
fi

echo "✅ Installation complete!"
echo "   - Antigravity Plugin : $AGY_PLUGIN_DIR"
echo "   - Claude Code Skills : $CLAUDE_SKILLS_DIR/{fast-browser,prompt-engineer}"
echo "   - Python Interpreter : $AGY_PLUGIN_DIR/.venv/bin/python"
