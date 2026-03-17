#!/usr/bin/env bash
set -euo pipefail

# Idempotent installer for todo-sync plugin

# ─────────────────────────────────────────────────────────────────────────
# Resolve source directory
# ─────────────────────────────────────────────────────────────────────────

SCRIPT_URL_BASE="https://raw.githubusercontent.com/user/todo-sync/main"
REMOTE_INSTALL=false
SOURCE_DIR=""

# Try to determine if this is a remote or local install
if [[ ! -f "${BASH_SOURCE[0]%/*}/templates/TODO.md" ]]; then
  REMOTE_INSTALL=true
else
  SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# ─────────────────────────────────────────────────────────────────────────
# Resolve target directory (where to install)
# ─────────────────────────────────────────────────────────────────────────

TARGET="${TARGET:-$(pwd)}"

if [[ ! -d "$TARGET/.git" ]]; then
  echo "Error: $TARGET is not a git repository (missing .git directory)"
  exit 1
fi

echo "Installing todo-sync into: $TARGET"

# ─────────────────────────────────────────────────────────────────────────
# Helper: fetch or copy a file
# ─────────────────────────────────────────────────────────────────────────

fetch_file() {
  local src="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"

  if [[ "$REMOTE_INSTALL" == "true" ]]; then
    curl -fsSL "$SCRIPT_URL_BASE/$src" -o "$dest" || {
      echo "Error: Failed to fetch $src from remote"
      exit 1
    }
  else
    cp "$SOURCE_DIR/$src" "$dest"
  fi
}

# ─────────────────────────────────────────────────────────────────────────
# 1. Copy scripts into .todo-sync/
# ─────────────────────────────────────────────────────────────────────────

mkdir -p "$TARGET/.todo-sync"
fetch_file "scripts/sync.py" "$TARGET/.todo-sync/sync.py"
fetch_file "scripts/sync.sh" "$TARGET/.todo-sync/sync.sh"
fetch_file "requirements.txt" "$TARGET/.todo-sync/requirements.txt"
chmod +x "$TARGET/.todo-sync/sync.sh"
echo "✓ Installed scripts to .todo-sync/"

# ─────────────────────────────────────────────────────────────────────────
# 2. Copy TODO.md (only if it doesn't exist)
# ─────────────────────────────────────────────────────────────────────────

if [[ ! -f "$TARGET/TODO.md" ]]; then
  fetch_file "templates/TODO.md" "$TARGET/TODO.md"
  echo "✓ Created TODO.md"
else
  echo "⊘ TODO.md already exists (skipped)"
fi

# ─────────────────────────────────────────────────────────────────────────
# 3. Append Makefile targets (idempotent guard)
# ─────────────────────────────────────────────────────────────────────────

MAKEFILE="$TARGET/Makefile"
GUARD="# todo-sync-targets"

if [[ -f "$MAKEFILE" ]] && grep -qF "$GUARD" "$MAKEFILE" 2>/dev/null; then
  echo "⊘ Makefile targets already present (skipped)"
else
  if [[ ! -f "$MAKEFILE" ]]; then
    printf ".PHONY:\n\n" > "$MAKEFILE"
  fi

  # Fetch or read the snippet and append
  if [[ "$REMOTE_INSTALL" == "true" ]]; then
    SNIPPET=$(curl -fsSL "$SCRIPT_URL_BASE/templates/Makefile.snippet") || {
      echo "Error: Failed to fetch Makefile.snippet from remote"
      exit 1
    }
  else
    SNIPPET=$(cat "$SOURCE_DIR/templates/Makefile.snippet")
  fi

  printf "\n%s\n%s\n" "$GUARD" "$SNIPPET" >> "$MAKEFILE"
  echo "✓ Appended Makefile targets"
fi

# ─────────────────────────────────────────────────────────────────────────
# 4. Update .gitignore (optional, but recommended)
# ─────────────────────────────────────────────────────────────────────────

GITIGNORE="$TARGET/.gitignore"
IGNORE_ENTRY=".todo-sync/"

if ! grep -qF "$IGNORE_ENTRY" "$GITIGNORE" 2>/dev/null; then
  {
    echo ""
    echo "# todo-sync plugin internals"
    echo "$IGNORE_ENTRY"
  } >> "$GITIGNORE"
  echo "✓ Added .todo-sync/ to .gitignore"
else
  echo "⊘ .gitignore already has .todo-sync/ (skipped)"
fi

# ─────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. cd $TARGET"
echo "  2. make todo-init"
echo "  3. edit TODO.md and add tasks"
echo "  4. make todo-push  (to create issues on GitHub)"
echo ""
