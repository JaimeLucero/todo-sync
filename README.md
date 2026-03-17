# todo-sync

A CLI tool for bidirectional syncing between `TODO.md` and GitHub Issues. Install once via Homebrew, then use `todo-sync` commands in any repo to keep your local tasks in sync with your GitHub issues.

## Features

- **Bidirectional sync**: Keep TODO.md and GitHub Issues in perfect sync
- **Global CLI**: Install once, use in any repo
- **No external Python dependencies**: Uses `gh` CLI (which you probably already have) and built-in Python
- **Flexible**: Push-only, pull-only, or bidirectional sync modes
- **Idempotent**: Safe to run multiple times; automatically creates issues on first push

## Installation

### Via Homebrew (recommended)

```bash
# One-time setup
brew tap yourname/todo-sync
brew install todo-sync

# Verify installation
todo-sync --version
```

### From source (development)

```bash
git clone https://github.com/yourname/todo-sync.git
cd todo-sync
chmod +x bin/todo-sync
export PATH="$(pwd)/bin:$PATH"

# Or symlink to your local bin
ln -s "$(pwd)/bin/todo-sync" /usr/local/bin/todo-sync
```

## Quick Start

1. **Authenticate with GitHub** (one-time):
   ```bash
   gh auth login
   ```

2. **Initialize TODO.md in your repo**:
   ```bash
   cd /your/repo
   todo-sync init
   ```

3. **Add tasks to TODO.md** and sync:
   ```bash
   # Edit TODO.md and add tasks under "## Open"
   # Example:
   # - [ ] Fix login bug
   # - [ ] Add dark mode

   # Sync to GitHub
   todo-sync push
   ```

## CLI Commands

### `todo-sync init`

Initialize a TODO.md file in the current repo:

```bash
todo-sync init                 # Create TODO.md
todo-sync init --with-makefile # Also add Makefile targets for convenience
```

**What it does:**
- Creates a basic TODO.md with "Open" and "Done" sections (if not already present)
- Optionally injects Makefile targets (`make todo-push`, etc.) for convenience

### `todo-sync push`

Push TODO.md items → GitHub Issues:

```bash
todo-sync push        # Sync TODO.md to GitHub
todo-sync push --dry-run      # Preview changes first
```

**What it does:**
- New items in TODO.md become issues on GitHub
- Issue numbers are written back to TODO.md automatically
- Checked items close their corresponding issues
- Unchecked items with closed issues reopen them

### `todo-sync pull`

Pull GitHub Issues → TODO.md:

```bash
todo-sync pull        # Sync GitHub to TODO.md
todo-sync pull --dry-run      # Preview changes first
```

**What it does:**
- New issues appear in your TODO.md under "## Open"
- Closed issues are checked off automatically
- Open issues uncheck their items

### `todo-sync sync`

Bidirectional sync (push then pull):

```bash
todo-sync sync        # Full two-way sync
todo-sync sync --dry-run      # Preview changes first
```

**What it does:**
- Runs push first (local TODO.md → GitHub)
- Then runs pull (GitHub → local TODO.md)
- Local checkbox state takes priority on conflicts

### Help & Version

```bash
todo-sync help                # Show overview
todo-sync help <command>      # Show help for a specific command
todo-sync --version          # Show version
```

## TODO.md Format

```markdown
# TODO

## Open
- [ ] Fix login bug <!-- issue:42 -->
- [ ] Add dark mode

## Done
- [x] Write tests <!-- issue:7 -->
```

- **Sections**: `## Open` and `## Done` track issue states
- **Checkboxes**: `- [ ]` (open) and `- [x]` (closed)
- **Issue links**: `<!-- issue:N -->` tags link items to GitHub issue numbers
  - Invisible in rendered markdown
  - Auto-generated on first push
  - Required for two-way sync

## Optional: Makefile Shortcuts

If you run `todo-sync init --with-makefile`, the following shortcuts become available in your repo:

| Command | Effect |
|---------|--------|
| `make todo-sync` | Shortcut for `todo-sync sync` |
| `make todo-push` | Shortcut for `todo-sync push` |
| `make todo-pull` | Shortcut for `todo-sync pull` |

These are entirely optional — you can always use the `todo-sync` CLI commands directly.

## How It Works

### Sync Algorithm

**Push direction (TODO.md → GitHub)**:
1. Items with no issue ID → create new GitHub issue, write ID back to file
2. Checked items with open issue → close the issue
3. Unchecked items with closed issue → reopen the issue

**Pull direction (GitHub → TODO.md)**:
1. Issues not in TODO.md → append under appropriate section
2. Closed issues ↔ checked items
3. Open issues ↔ unchecked items

**Conflict resolution** (bidirectional mode):
- Push runs first, so **local checkbox state takes priority**
- No lock files or conflict markers needed

## Requirements

- **`gh` CLI** (install from https://cli.github.com)
- **Python 3.8+**
- **Git**
- **Authenticated GitHub account** (run `gh auth login`)
- Working in a git repo with the `origin` remote pointing to GitHub

When installed via Homebrew, Python 3 is automatically installed as a dependency.

## Troubleshooting

**Error: "gh CLI not found"**
- Install from https://cli.github.com

**Error: "Not authenticated with GitHub"**
- Run `gh auth login` and follow the prompts

**Error: "Not a git repository"**
- Make sure you're inside a git repo with a GitHub remote

**Issues not appearing in TODO.md after `make todo-pull`**
- Make sure the GitHub issues were created in the same repo
- Run `gh issue list` to verify issues exist

**Issue numbers not being written to TODO.md**
- Check `gh auth status` — you may need to re-authenticate

## Development

### Running tests

```bash
make test        # Run all tests
make coverage    # Run with coverage report
make lint        # Lint Python code
```

### Installing into a local test repo

```bash
make install TARGET=/path/to/test/repo
```

## Limitations

- Works only with GitHub (not GitLab, Bitbucket, etc.)
- Does not sync issue descriptions, labels, or assignees (only title and state)
- Does not handle pull requests (they're filtered out)

## License

MIT

## Contributing

Pull requests welcome! Please ensure tests pass and code is linted before submitting.
