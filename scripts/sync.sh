#!/usr/bin/env bash
set -euo pipefail

# Shell wrapper for sync.py with pre-flight checks

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/sync.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TODO_FILE="${TODO_FILE:-TODO.md}"

# ─────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────

MODE="bidirectional"
DRY_RUN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)       MODE="$2";    shift 2 ;;
    --push)       MODE="push";  shift   ;;
    --pull)       MODE="pull";  shift   ;;
    --bidir)      MODE="bidirectional"; shift ;;
    --todo)       TODO_FILE="$2"; shift 2 ;;
    --dry-run)    DRY_RUN="--dry-run"; shift ;;
    -h|--help)    echo "Usage: sync.sh [--mode bidirectional|push|pull] [--todo PATH] [--dry-run]"; exit 0 ;;
    *)            echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────
# Pre-flight checks
# ─────────────────────────────────────────────────────────────────────────

# 1. Check gh CLI is installed
if ! command -v gh &>/dev/null; then
  echo "Error: gh CLI not found."
  echo "Install from: https://cli.github.com"
  exit 1
fi

# 2. Check gh is authenticated
if ! gh auth status &>/dev/null 2>&1; then
  echo "Error: Not authenticated with GitHub."
  echo "Run: gh auth login"
  exit 1
fi

# 3. Check python3 is available
if ! command -v "$PYTHON_BIN" &>/dev/null; then
  echo "Error: $PYTHON_BIN not found."
  exit 1
fi

# 4. Check we're inside a git repository
if ! git rev-parse --git-dir &>/dev/null 2>&1; then
  echo "Error: Not inside a git repository."
  exit 1
fi

# ─────────────────────────────────────────────────────────────────────────
# Execute sync.py
# ─────────────────────────────────────────────────────────────────────────

exec "$PYTHON_BIN" "$SYNC_SCRIPT" \
  --mode "$MODE" \
  --todo "$TODO_FILE" \
  $DRY_RUN
