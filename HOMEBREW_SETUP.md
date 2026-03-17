# Homebrew Setup Guide for todo-sync

This guide walks you through setting up `todo-sync` for distribution via Homebrew.

## Overview

You need **2 GitHub repositories**:

1. **`todo-sync`** (main tool) — Already created ✓
   - Contains the actual code, tests, and build artifacts
   - Releases are tagged here (v1.0.0, v1.1.0, etc.)

2. **`homebrew-todo-sync`** (tap formula) — You need to create this
   - Contains only the Homebrew formula
   - Users tap this to install: `brew tap jaimelucero/todo-sync`

---

## Step 1: Create the Homebrew Tap Repository

### On GitHub:

1. Go to https://github.com/new
2. Create a new repository named **`homebrew-todo-sync`**
3. Set it to public (required for Homebrew)
4. Skip templates, just initialize it empty

### Locally:

```bash
# Clone the new repo
git clone https://github.com/jaimelucero/homebrew-todo-sync.git
cd homebrew-todo-sync

# Create the Formula directory
mkdir -p Formula

# Copy the formula from todo-sync
cp /Users/jaimeemanuellucero/Projects/todo-sync/formula/todo-sync.rb Formula/

# Create a README
cat > README.md << 'EOF'
# Homebrew tap for todo-sync

Homebrew tap for the todo-sync CLI tool.

## Installation

```bash
brew tap jaimelucero/todo-sync
brew install todo-sync
```

## Usage

```bash
todo-sync init
todo-sync push
todo-sync pull
todo-sync sync
todo-sync help
```

For more information, visit: https://github.com/jaimelucero/todo-sync
EOF

# Commit and push
git add .
git commit -m "Initial commit: add todo-sync formula"
git push -u origin main
```

---

## Step 2: Create Your First Release

In the **main `todo-sync` repo**:

```bash
cd /Users/jaimeemanuellucero/Projects/todo-sync

# Create a git tag
git tag v1.0.0
git push origin main --tags
```

This triggers `.github/workflows/release.yml` which automatically creates a GitHub Release with a source tarball.

**Verify the release was created:**
- Go to https://github.com/jaimelucero/todo-sync/releases
- You should see v1.0.0 with a tarball

---

## Step 3: Get the SHA256 Hash

After the release is created, get the tarball SHA256:

```bash
# Download and compute SHA256
curl -sL "https://github.com/jaimelucero/todo-sync/archive/refs/tags/v1.0.0.tar.gz" | sha256sum

# Output will look like:
# abc123def456789...  -
```

Copy just the hash part (the first 64 characters).

---

## Step 4: Update the Formula

In your **`homebrew-todo-sync` repo**, update `Formula/todo-sync.rb`:

```ruby
class TodoSync < Formula
  desc "Bidirectional sync between TODO.md and GitHub Issues"
  homepage "https://github.com/jaimelucero/todo-sync"
  url "https://github.com/jaimelucero/todo-sync/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "abc123def456789..."  # ← Paste your actual SHA256 here
  version "1.0.0"
  license "MIT"

  depends_on "python3"

  def install
    libexec.install "scripts/sync.py"
    libexec.install "templates"
    bin.install "bin/todo-sync"
  end

  test do
    system "#{bin}/todo-sync", "--version"
    assert_match "todo-sync", shell_output("#{bin}/todo-sync --help")
  end
end
```

Commit and push:

```bash
cd homebrew-todo-sync
git add Formula/todo-sync.rb
git commit -m "Update formula for v1.0.0: add correct SHA256"
git push
```

---

## Step 5: Test the Installation

```bash
# Add the tap
brew tap jaimelucero/todo-sync

# Install
brew install todo-sync

# Verify it works
todo-sync --version
todo-sync help
```

If you want to test a local version first:

```bash
# Test from the local tap repo
brew tap jaimelucero/todo-sync /path/to/homebrew-todo-sync
brew install todo-sync  # Will use your local version
```

---

## Step 6: Publish Updates

Each time you want to release a new version:

### 1. In `todo-sync` repo:

```bash
# Update version in scripts/sync.py (line with version string)
# Let's say you're releasing v1.1.0

git add scripts/sync.py
git commit -m "Bump version to 1.1.0"
git tag v1.1.0
git push origin main --tags
# → GitHub Actions auto-creates release
```

### 2. Get new SHA256:

```bash
curl -sL "https://github.com/jaimelucero/todo-sync/archive/refs/tags/v1.1.0.tar.gz" | sha256sum
```

### 3. In `homebrew-todo-sync` repo:

```bash
# Edit Formula/todo-sync.rb
# - Change url tag from v1.0.0 to v1.1.0
# - Change version from 1.0.0 to 1.1.0
# - Update sha256 hash

git add Formula/todo-sync.rb
git commit -m "Update formula for v1.1.0"
git push
```

---

## Quick Reference

| Step | Command | Notes |
|------|---------|-------|
| Create tap | `git clone https://github.com/jaimelucero/homebrew-todo-sync` | Do once |
| Add formula | Copy `formula/todo-sync.rb` to `Formula/` | In tap repo |
| Release | `git tag v1.0.0 && git push --tags` | In main repo |
| Get SHA256 | `curl ... \| sha256sum` | After release created |
| Update formula | Edit `Formula/todo-sync.rb` | In tap repo |
| Test install | `brew tap jaimelucero/todo-sync && brew install todo-sync` | Local machine |

---

## Users' Installation Command

Once everything is set up:

```bash
brew tap jaimelucero/todo-sync
brew install todo-sync
```

---

## Troubleshooting

**"Formula not found"**
- Make sure the tap repo is public
- Make sure the formula file is at `Formula/todo-sync.rb`
- Run `brew tap-new` diagnostics: `brew tap jaimelucero/todo-sync --full`

**"SHA256 mismatch"**
- Recalculate: `curl -sL "https://github.com/jaimelucero/todo-sync/archive/refs/tags/vX.X.X.tar.gz" | sha256sum`
- Make sure the tag exists: `git tag -l` in main repo

**"Python not found after install"**
- Homebrew should auto-install Python 3 as a dependency
- Verify: `brew list | grep python`
- If missing: `brew install python3`

**Testing locally**

```bash
# Uninstall if already installed
brew uninstall todo-sync

# Tap the local repo
brew tap jaimelucero/todo-sync /path/to/homebrew-todo-sync

# Install from local tap
brew install todo-sync

# Verify
which todo-sync
todo-sync --version
```

---

## Next Steps

1. ✅ Update `todo-sync` repo with `jaimelucero/` URLs
2. Create `homebrew-todo-sync` repo on GitHub
3. Copy formula and commit
4. Create v1.0.0 release tag
5. Get SHA256 and update formula
6. Test installation locally
7. Announce: `brew tap jaimelucero/todo-sync && brew install todo-sync`
