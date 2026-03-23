# todo-sync

A CLI tool for bidirectional syncing between `TODO.md`, GitHub Issues, and Notion. Install once via Homebrew, then use `todo-sync` commands in any repo to keep your local tasks in sync.

## Features

- **Bidirectional sync**: Keep TODO.md and GitHub Issues in perfect sync
- **Notion integration**: Sync with a Notion database for project management workflows
- **Rich ticket management**: Descriptions, subtasks, labels, assignees — all tracked in TODO.md
- **LLM-powered ticket generation**: Generate well-structured tickets from plain-language prompts
- **Global CLI**: Install once, use in any repo
- **No external Python dependencies**: Uses `gh` CLI and built-in Python
- **Flexible**: Push-only, pull-only, or bidirectional sync modes
- **Idempotent**: Safe to run multiple times

## Installation

### Via Homebrew (recommended)

```bash
# One-time setup
brew tap jaimelucero/todo-sync
brew install todo-sync

# Verify installation
todo-sync --version
```

### From source (development)

```bash
git clone https://github.com/jaimelucero/todo-sync.git
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

3. **Add a ticket and sync**:
   ```bash
   # Manually add a ticket
   todo-sync add "Fix login bug" --description "Session tokens expire too early"

   # Or generate one with AI
   todo-sync add --generate "users are getting logged out randomly"

   # Sync everything to GitHub
   todo-sync push
   ```

## CLI Commands

### `todo-sync init`

Initialize a TODO.md file in the current repo:

```bash
todo-sync init                 # Create TODO.md
todo-sync init --with-makefile # Also add Makefile targets for convenience
todo-sync init --force         # Overwrite existing TODO.md
```

---

### `todo-sync push`

Push TODO.md items → GitHub Issues:

```bash
todo-sync push
todo-sync push --dry-run      # Preview changes first
```

**What it does:**
- New items in TODO.md become issues on GitHub
- Issue numbers are written back to TODO.md automatically
- Checked items close their corresponding issues
- Unchecked items with closed issues reopen them
- Syncs title, description, and subtasks for existing issues

---

### `todo-sync pull`

Pull GitHub Issues → TODO.md:

```bash
todo-sync pull
todo-sync pull --dry-run      # Preview changes first
```

**What it does:**
- New issues appear in your TODO.md under "## Open"
- Closed issues are checked off automatically
- Open issues uncheck their items
- Syncs assignee information (who claimed the ticket on GitHub)

---

### `todo-sync sync`

Bidirectional sync (push then pull):

```bash
todo-sync sync
todo-sync sync --dry-run      # Preview changes first
```

Local checkbox state takes priority on conflicts.

---

### `todo-sync add`

Create a new ticket in both TODO.md and GitHub:

```bash
todo-sync add "Fix login bug"
todo-sync add "Fix login bug" --description "Session tokens expire too early"
todo-sync add "Fix login bug" --subtask "Reproduce locally" --subtask "Write regression test"

# Generate title, description, and subtasks using Claude AI
todo-sync add --generate "users are getting randomly logged out on mobile"
```

`--generate` requires the `ANTHROPIC_API_KEY` environment variable to be set.

---

### `todo-sync list`

List tickets with their issue numbers:

```bash
todo-sync list        # Show open tickets
todo-sync list --all  # Show open and done tickets
```

---

### `todo-sync update`

Update an existing ticket in both TODO.md and GitHub:

```bash
todo-sync update 42 --title "New title"
todo-sync update 42 --description "Updated description"
todo-sync update 42 --add-subtask "New subtask"
todo-sync update 42 --remove-subtask "Old subtask text"
```

---

### `todo-sync remove`

Remove a ticket from TODO.md:

```bash
todo-sync remove 42           # Remove from TODO.md only
todo-sync remove 42 --close   # Also close the GitHub issue
```

---

### `todo-sync label`

Set labels on a GitHub issue and sync them to TODO.md:

```bash
todo-sync label 42 "bug, urgent"
```

Missing labels are auto-created in the repo.

---

### `todo-sync labels`

List all available labels in the repository:

```bash
todo-sync labels
```

---

### `todo-sync comment`

Add a comment to a GitHub issue:

```bash
todo-sync comment 42 "Looking into this now"
```

---

### `todo-sync assign`

Assign a GitHub issue to yourself:

```bash
todo-sync assign 42
```

---

### Notion Commands

Sync your TODO.md with a Notion database (useful for PM workflows where a project manager manages ticket statuses in Notion).

#### `todo-sync notion-setup`

Configure Notion credentials (one-time per repo):

```bash
todo-sync notion-setup
```

Prompts for your Notion integration token and database ID. Saves to `.todo-sync/notion.json` (chmod 600, already gitignored).

#### `todo-sync notion-push`

Push TODO.md → Notion database:

```bash
todo-sync notion-push
todo-sync notion-push --dry-run
```

Creates Notion pages for new items and stamps them with a `<!-- notion:PAGE_ID -->` comment.

#### `todo-sync notion-pull`

Pull Notion database → TODO.md:

```bash
todo-sync notion-pull
todo-sync notion-pull --dry-run
```

Pulls status, title, description, and new items from Notion. **Notion is authoritative for ticket status** — `notion-pull` always accepts status from Notion.

#### `todo-sync notion-sync`

Bidirectional sync TODO.md ↔ Notion:

```bash
todo-sync notion-sync
todo-sync notion-sync --dry-run
```

---

### Help & Version

```bash
todo-sync help                # Show overview
todo-sync help <command>      # Show help for a specific command
todo-sync --version           # Show version
```

---

## TODO.md Format

```markdown
# TODO

## Open
- [ ] Fix login bug <!-- issue:42 --> <!-- notion:abc-def-123 -->
  > Investigate session token expiry on mobile devices
  status: ongoing
  assigned: jane.doe
  labels: bug, urgent
  - [ ] Reproduce locally
  - [x] Check token refresh logic

- [ ] Add dark mode
  > User request from Q1 feedback

## Done
- [x] Write tests <!-- issue:7 -->
  status: done
  assigned: john.smith
```

| Element | Description |
|---------|-------------|
| `- [ ]` / `- [x]` | Open/closed checkbox |
| `<!-- issue:N -->` | Links item to GitHub issue number (auto-generated on first push) |
| `<!-- notion:ID -->` | Links item to Notion page UUID (auto-generated on `notion-push`) |
| `> text` | Description (synced to GitHub issue body) |
| `status: <value>` | Notion workflow status (`todo`, `assigned`, `ongoing`, `PR`, `staging`, `merge`, `QA`, `done`) |
| `assigned: <login>` | GitHub/Notion assignee (synced from GitHub on `pull`) |
| `labels: a, b` | GitHub labels (synced on `push`/`pull`) |
| `  - [ ] subtask` | Subtask (synced to GitHub issue body) |

---

## Notion Integration

The Notion integration is designed for teams where a project manager tracks tickets in Notion and developers work from TODO.md.

### Required Notion Database Schema

Create a Notion database with these properties:

| Property | Type | Notes |
|----------|------|-------|
| Name | title | Ticket title |
| Status | select | `todo`, `assigned`, `ongoing`, `PR`, `staging`, `merge`, `QA`, `done` |
| Description | rich_text | Ticket description |
| GitHubIssue | number | Linked GitHub issue number |
| Subtasks | rich_text | Serialized as `- [ ] text\n- [x] text` |
| Labels | rich_text | Comma-separated |
| Checked | checkbox | Mirrors done state |

### Workflow

1. Run `todo-sync notion-setup` (one-time)
2. Run `todo-sync notion-push` to create Notion pages from your TODO.md
3. Share the Notion board with your PM
4. PM updates statuses in Notion
5. Run `todo-sync notion-pull` to update your TODO.md with the latest statuses

---

## LLM-Powered Ticket Generation

`todo-sync add --generate` uses the Claude API to expand a short prompt into a full ticket with title, description, and subtasks.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
todo-sync add --generate "the checkout flow crashes when a coupon code is applied"
```

Generated output example:
- **Title**: Fix coupon code crash in checkout flow
- **Description**: The checkout flow throws an unhandled exception when...
- **Subtasks**: Reproduce with expired coupon, Add error boundary, Write regression test

---

## Optional: Makefile Shortcuts

If you run `todo-sync init --with-makefile`, the following shortcuts become available:

| Command | Effect |
|---------|--------|
| `make todo-sync` | Shortcut for `todo-sync sync` |
| `make todo-push` | Shortcut for `todo-sync push` |
| `make todo-pull` | Shortcut for `todo-sync pull` |

---

## How It Works

### Sync Algorithm

**Push direction (TODO.md → GitHub)**:
1. Items with no issue ID → create new GitHub issue, write ID back to file
2. Checked items with open issue → close the issue
3. Unchecked items with closed issue → reopen the issue
4. Existing items → sync title, description, and subtasks to GitHub

**Pull direction (GitHub → TODO.md)**:
1. Issues not in TODO.md → append under appropriate section
2. Closed issues ↔ checked items
3. Open issues ↔ unchecked items
4. Syncs assignee login to `assigned:` metadata line

**Conflict resolution** (bidirectional mode):
- Push runs first, so **local checkbox state takes priority**
- No lock files or conflict markers needed

---

## Requirements

- **`gh` CLI** (install from https://cli.github.com)
- **Python 3.8+**
- **Git**
- **Authenticated GitHub account** (run `gh auth login`)
- Working in a git repo with the `origin` remote pointing to GitHub
- **`ANTHROPIC_API_KEY`** — only required for `todo-sync add --generate`

When installed via Homebrew, Python 3 is automatically installed as a dependency.

---

## Troubleshooting

**Error: "gh CLI not found"**
- Install from https://cli.github.com

**Error: "Not authenticated with GitHub"**
- Run `gh auth login` and follow the prompts

**Error: "Not a git repository"**
- Make sure you're inside a git repo with a GitHub remote

**Error: "ANTHROPIC_API_KEY environment variable not set"**
- Set the variable: `export ANTHROPIC_API_KEY=sk-ant-...`

**Error: "Notion not configured"**
- Run `todo-sync notion-setup` first

**Issues not appearing in TODO.md after pull**
- Make sure the GitHub issues were created in the same repo
- Run `gh issue list` to verify issues exist

**Issue numbers not being written to TODO.md**
- Check `gh auth status` — you may need to re-authenticate

---

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

---

## Limitations

- Works only with GitHub (not GitLab, Bitbucket, etc.)
- Does not sync issue labels on `push`/`pull` automatically — use `todo-sync label` to set them explicitly, or they are preserved from the last sync
- Does not handle pull requests (they're filtered out)

## License

MIT

## Contributing

Pull requests welcome! Please ensure tests pass and code is linted before submitting.
