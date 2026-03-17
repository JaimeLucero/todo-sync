#!/usr/bin/env python3
"""
Bidirectional sync between TODO.md and GitHub Issues.
Uses 'gh' CLI for all GitHub operations.
"""

import argparse
import dataclasses
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


@dataclasses.dataclass
class TodoItem:
    """A single TODO item parsed from TODO.md."""
    text: str          # display text (no checkbox prefix)
    checked: bool      # True = - [x], False = - [ ]
    issue_id: int | None
    line_index: int    # 0-based line number for targeted rewrites
    section: str       # "open" or "done"


@dataclasses.dataclass
class IssueRecord:
    """A GitHub issue."""
    number: int
    title: str
    state: str  # "open" or "closed"


class TodoParser:
    """Parse and write TODO.md files with bidirectional sync support."""

    CHECKBOX_RE = re.compile(r'^- \[([ x])\] (.+?)(?:\s*<!--\s*issue:(\d+)\s*-->)?\s*$')
    SECTION_RE = re.compile(r'^##\s+(.+)$')

    def __init__(self, path: str):
        self.path = Path(path)
        self._lines: list[str] = []
        self._section_lines: dict[str, int] = {}  # section name -> line index of its heading

    def load(self) -> list[TodoItem]:
        """Parse TODO.md and return list of items."""
        if not self.path.exists():
            raise FileNotFoundError(f"TODO.md not found at {self.path}")

        with open(self.path, 'r', encoding='utf-8') as f:
            self._lines = [line.rstrip('\n') for line in f]

        items = []
        current_section = None

        for idx, line in enumerate(self._lines):
            # Check for section heading
            section_match = self.SECTION_RE.match(line)
            if section_match:
                heading = section_match.group(1).lower()
                if "open" in heading:
                    current_section = "open"
                    self._section_lines["open"] = idx
                elif "done" in heading:
                    current_section = "done"
                    self._section_lines["done"] = idx
                continue

            # Check for checkbox
            checkbox_match = self.CHECKBOX_RE.match(line)
            if checkbox_match:
                checked = checkbox_match.group(1) == 'x'
                text = checkbox_match.group(2)
                issue_id = int(checkbox_match.group(3)) if checkbox_match.group(3) else None

                items.append(TodoItem(
                    text=text,
                    checked=checked,
                    issue_id=issue_id,
                    line_index=idx,
                    section=current_section or "unknown"
                ))

        return items

    def write_back(self, items: list[TodoItem]) -> None:
        """Update TODO.md with modified items."""
        # Build a map from line_index to item for quick lookup
        item_map = {item.line_index: item for item in items}

        # Update existing lines
        for idx, line in enumerate(self._lines):
            if idx in item_map:
                item = item_map[idx]
                self._lines[idx] = self._format_line(item.checked, item.text, item.issue_id)

        # Write back to file
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self._lines))
            if self._lines:  # Add final newline if file is not empty
                f.write('\n')

    def append_item(self, section: str, text: str, issue_id: int) -> None:
        """Append a new item under the specified section (open/done)."""
        section_lower = section.lower()

        if section_lower not in self._section_lines:
            raise ValueError(f"Section '{section}' not found in TODO.md")

        section_idx = self._section_lines[section_lower]

        # Find the last checkbox line under this section
        insert_idx = section_idx + 1
        for i in range(section_idx + 1, len(self._lines)):
            line = self._lines[i]

            # If we hit another section, stop
            if self.SECTION_RE.match(line):
                break

            # If it's a checkbox, update insert position
            if self.CHECKBOX_RE.match(line):
                insert_idx = i + 1

        new_line = self._format_line(False, text, issue_id)
        self._lines.insert(insert_idx, new_line)

        # Write back immediately
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self._lines))
            if self._lines:
                f.write('\n')

    @staticmethod
    def _format_line(checked: bool, text: str, issue_id: int | None) -> str:
        """Format a checkbox line."""
        checkbox = '[x]' if checked else '[ ]'
        line = f"- {checkbox} {text}"
        if issue_id is not None:
            line += f" <!-- issue:{issue_id} -->"
        return line


class GitHubError(Exception):
    """GitHub API operation failed."""
    pass


class GitHubClient:
    """Interact with GitHub Issues using 'gh' CLI."""

    def __init__(self):
        """Initialize the client. Repo is auto-detected."""
        self.repo = self._detect_repo()

    def _gh(self, *args) -> str:
        """Run a gh command and return stdout."""
        try:
            result = subprocess.run(
                ["gh"] + list(args),
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise GitHubError(f"gh command failed: {' '.join(args)}\n{e.stderr}")

    def _detect_repo(self) -> str:
        """Get repo slug from 'gh repo view'."""
        try:
            output = self._gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
            return output.strip()
        except GitHubError as e:
            raise GitHubError(f"Could not auto-detect GitHub repo. Make sure you're in a git repository with an 'origin' remote.\n{e}")

    def fetch_all_issues(self) -> list[IssueRecord]:
        """Fetch all (open and closed) issues."""
        try:
            output = self._gh(
                "issue", "list",
                "--state", "all",
                "--limit", "500",
                "--json", "number,title,state"
            )
        except GitHubError as e:
            raise GitHubError(f"Failed to fetch issues: {e}")

        issues = []
        for line in output.split('\n'):
            if not line.strip():
                continue
            # Expected format: "123\tIssue Title\topen"
            parts = line.split('\t')
            if len(parts) >= 3:
                try:
                    number = int(parts[0])
                    title = parts[1]
                    state = parts[2]
                    issues.append(IssueRecord(number=number, title=title, state=state))
                except (ValueError, IndexError):
                    pass  # Skip malformed lines

        return issues

    def create_issue(self, title: str) -> IssueRecord:
        """Create a new issue and return the record."""
        try:
            output = self._gh(
                "issue", "create",
                "--title", title,
                "--body", ""
            )
        except GitHubError as e:
            raise GitHubError(f"Failed to create issue '{title}': {e}")

        # Output is typically: "https://github.com/owner/repo/issues/123"
        try:
            issue_number = int(output.rstrip('/').split('/')[-1])
            return IssueRecord(number=issue_number, title=title, state="open")
        except (ValueError, IndexError):
            raise GitHubError(f"Could not parse issue number from: {output}")

    def close_issue(self, number: int) -> None:
        """Close an issue."""
        try:
            self._gh("issue", "close", str(number))
        except GitHubError as e:
            raise GitHubError(f"Failed to close issue #{number}: {e}")

    def reopen_issue(self, number: int) -> None:
        """Reopen an issue."""
        try:
            self._gh("issue", "reopen", str(number))
        except GitHubError as e:
            raise GitHubError(f"Failed to reopen issue #{number}: {e}")


class SyncEngine:
    """Orchestrate bidirectional sync between TODO.md and GitHub Issues."""

    def __init__(self, todo_path: str, github: GitHubClient, dry_run: bool = False):
        self.parser = TodoParser(todo_path)
        self.github = github
        self.dry_run = dry_run
        self._changelog: list[str] = []

    def sync_bidirectional(self) -> None:
        """Full bidirectional sync: push then pull."""
        self._sync_push_internal()
        self._sync_pull_internal()
        self.print_summary()

    def sync_push_only(self) -> None:
        """Push only: TODO.md → GitHub."""
        self._sync_push_internal()
        self.print_summary()

    def sync_pull_only(self) -> None:
        """Pull only: GitHub → TODO.md."""
        self._sync_pull_internal()
        self.print_summary()

    def _sync_push_internal(self) -> None:
        """Apply TODO.md state to GitHub Issues."""
        items = self.parser.load()
        issues = self.github.fetch_all_issues()
        issue_map = {issue.number: issue for issue in issues}

        for item in items:
            try:
                # New item: create issue
                if item.issue_id is None:
                    if self.dry_run:
                        self._log("CREATE", f"'{item.text}'")
                    else:
                        issue = self.github.create_issue(item.text)
                        item.issue_id = issue.number
                        self._log("CREATED", f"Issue #{issue.number}: {item.text}")

                # Item is checked, issue is open: close it
                if item.issue_id in issue_map:
                    issue = issue_map[item.issue_id]
                    if item.checked and issue.state == "open":
                        if self.dry_run:
                            self._log("CLOSE", f"Issue #{item.issue_id}: {item.text}")
                        else:
                            self.github.close_issue(item.issue_id)
                            self._log("CLOSED", f"Issue #{item.issue_id}: {item.text}")

                    # Item is unchecked, issue is closed: reopen it
                    elif not item.checked and issue.state == "closed":
                        if self.dry_run:
                            self._log("REOPEN", f"Issue #{item.issue_id}: {item.text}")
                        else:
                            self.github.reopen_issue(item.issue_id)
                            self._log("REOPENED", f"Issue #{item.issue_id}: {item.text}")

            except GitHubError as e:
                self._log("ERROR", f"Failed to sync '{item.text}': {e}")

        # Write back any new issue IDs to the file
        if not self.dry_run:
            self.parser.write_back(items)

    def _sync_pull_internal(self) -> None:
        """Apply GitHub Issues state to TODO.md."""
        items = self.parser.load()
        issues = self.github.fetch_all_issues()

        # Build a map of issue_id -> item for quick lookup
        linked_issues = {item.issue_id for item in items if item.issue_id is not None}

        for issue in issues:
            try:
                # Issue not in TODO.md: append it
                if issue.number not in linked_issues:
                    if self.dry_run:
                        self._log("APPEND", f"Issue #{issue.number}: {issue.title}")
                    else:
                        section = "open" if issue.state == "open" else "done"
                        self.parser.append_item(section, issue.title, issue.number)
                        self._log("APPENDED", f"Issue #{issue.number} to {section.upper()}")
                else:
                    # Issue exists in TODO.md: sync state
                    item = next(i for i in items if i.issue_id == issue.number)

                    # Issue closed, item unchecked: check it
                    if issue.state == "closed" and not item.checked:
                        if self.dry_run:
                            self._log("CHECK", f"Issue #{issue.number}: {issue.title}")
                        else:
                            item.checked = True
                            self._log("CHECKED", f"Issue #{issue.number} is closed")

                    # Issue open, item checked (in pull-only mode, revert): uncheck it
                    elif issue.state == "open" and item.checked:
                        if self.dry_run:
                            self._log("UNCHECK", f"Issue #{issue.number}: {issue.title}")
                        else:
                            item.checked = False
                            self._log("UNCHECKED", f"Issue #{issue.number} is open")

            except (GitHubError, StopIteration) as e:
                self._log("ERROR", f"Failed to sync issue #{issue.number}: {e}")

        # Write back any state changes
        if not self.dry_run:
            self.parser.write_back(items)

    def _log(self, action: str, detail: str) -> None:
        """Log an action."""
        msg = f"[{action}] {detail}"
        self._changelog.append(msg)
        print(msg)

    def print_summary(self) -> None:
        """Print summary of all changes."""
        if not self._changelog:
            print("No changes.")


def _inject_makefile_targets() -> None:
    """Append todo-sync targets to Makefile in CWD."""
    guard = "# todo-sync-targets"
    makefile = Path("Makefile")

    # Check if targets already exist
    if makefile.exists():
        content = makefile.read_text()
        if guard in content:
            print("⊘ Makefile targets already present (skipped)")
            return

    # Load the snippet
    snippet_path = Path(__file__).parent.parent / "templates" / "Makefile.snippet"
    if not snippet_path.exists():
        print(f"Warning: Makefile.snippet not found at {snippet_path}")
        return

    snippet = snippet_path.read_text()

    # Append to Makefile
    with open(makefile, 'a', encoding='utf-8') as f:
        f.write(f"\n{guard}\n{snippet}\n")

    print("✓ Appended Makefile targets")


def cmd_init(args) -> None:
    """Initialize TODO.md in the current directory."""
    todo_path = Path(args.todo)

    if todo_path.exists():
        print(f"⊘ {todo_path} already exists (skipped)")
    else:
        # Copy template TODO.md
        template_path = Path(__file__).parent.parent / "templates" / "TODO.md"
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found at {template_path}")

        shutil.copy(template_path, todo_path)
        print(f"✓ Created {todo_path}")

    # Optionally inject Makefile targets
    if args.with_makefile:
        _inject_makefile_targets()


def cmd_push(args) -> None:
    """Push TODO.md items to GitHub Issues."""
    try:
        github = GitHubClient()
        engine = SyncEngine(args.todo, github, dry_run=args.dry_run)
        engine.sync_push_only()
    except (FileNotFoundError, GitHubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_pull(args) -> None:
    """Pull GitHub Issues to TODO.md."""
    try:
        github = GitHubClient()
        engine = SyncEngine(args.todo, github, dry_run=args.dry_run)
        engine.sync_pull_only()
    except (FileNotFoundError, GitHubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_sync(args) -> None:
    """Bidirectional sync: TODO.md <-> GitHub Issues."""
    try:
        github = GitHubClient()
        engine = SyncEngine(args.todo, github, dry_run=args.dry_run)
        engine.sync_bidirectional()
    except (FileNotFoundError, GitHubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_help(args) -> None:
    """Show help for a command."""
    commands_help = {
        "init": """
  init — Set up TODO.md in the current repository

  Initialize a TODO.md file in the current directory with a basic structure
  (Open and Done sections). Safe to run multiple times—won't overwrite an
  existing TODO.md.

  Usage:
    todo-sync init [options]

  Options:
    --with-makefile    Also inject Makefile targets (make todo-sync, etc.)
    --todo FILE        Path to TODO.md (default: TODO.md)

  Example:
    todo-sync init
    todo-sync init --with-makefile
    todo-sync init --todo tasks/TODO.md
""",
        "push": """
  push — Push TODO.md items to GitHub Issues

  Syncs TODO.md tasks to GitHub Issues in one direction:
  - Unchecked items without an issue ID create new GitHub Issues
  - Checked items close their linked issue
  - Unchecked items with a closed issue reopen it
  - Issue numbers are written back to TODO.md for future syncs

  Usage:
    todo-sync push [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)
    --dry-run      Preview changes without making them

  Example:
    todo-sync push
    todo-sync push --dry-run
    todo-sync push --todo tasks/TODO.md
""",
        "pull": """
  pull — Pull GitHub Issues to TODO.md

  Syncs GitHub Issues to TODO.md in one direction:
  - New issues are appended to the appropriate section
  - Closed issues check off their linked TODO.md items
  - Open issues uncheck their linked TODO.md items

  Usage:
    todo-sync pull [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)
    --dry-run      Preview changes without making them

  Example:
    todo-sync pull
    todo-sync pull --dry-run
    todo-sync pull --todo tasks/TODO.md
""",
        "sync": """
  sync — Bidirectional sync between TODO.md and GitHub Issues

  Two-way sync: performs a push first (TODO.md → GitHub), then a pull
  (GitHub → TODO.md). Local TODO.md state takes priority for conflicts.

  Usage:
    todo-sync sync [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)
    --dry-run      Preview changes without making them

  Example:
    todo-sync sync
    todo-sync sync --dry-run
    todo-sync sync --todo tasks/TODO.md
""",
    }

    command = args.command if hasattr(args, 'command') else None

    if command and command in commands_help:
        print(commands_help[command])
    else:
        print("""
  todo-sync — sync your TODO.md with GitHub Issues

  Usage:
    todo-sync <command> [options]

  Commands:
    init    Set up TODO.md in the current repo
    push    Push TODO.md tasks → GitHub Issues
    pull    Pull GitHub Issues → TODO.md
    sync    Bidirectional sync (push + pull)
    help    Show help for a command

  Run 'todo-sync help <command>' for details on a specific command.

  Examples:
    todo-sync init
    todo-sync sync
    todo-sync push --dry-run

  For more information, visit: https://github.com/user/todo-sync
""")


def main() -> None:
    """Main entry point with subcommand dispatch."""
    parser = argparse.ArgumentParser(
        prog="todo-sync",
        description="Sync TODO.md with GitHub Issues",
        add_help=True
    )

    # Global options
    parser.add_argument(
        "--version",
        action="version",
        version="todo-sync 1.0.0"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # init
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize TODO.md in the current repo",
        add_help=False
    )
    init_parser.add_argument(
        "--with-makefile",
        action="store_true",
        help="Also inject Makefile targets"
    )
    init_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    init_parser.add_argument("-h", "--help", action="store_true")

    # push
    push_parser = subparsers.add_parser(
        "push",
        help="Push TODO.md tasks → GitHub Issues",
        add_help=False
    )
    push_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    push_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    push_parser.add_argument("-h", "--help", action="store_true")

    # pull
    pull_parser = subparsers.add_parser(
        "pull",
        help="Pull GitHub Issues → TODO.md",
        add_help=False
    )
    pull_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    pull_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    pull_parser.add_argument("-h", "--help", action="store_true")

    # sync
    sync_parser = subparsers.add_parser(
        "sync",
        help="Bidirectional sync",
        add_help=False
    )
    sync_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    sync_parser.add_argument("-h", "--help", action="store_true")

    # help
    help_parser = subparsers.add_parser(
        "help",
        help="Show help for a command",
        add_help=False
    )
    help_parser.add_argument(
        "help_command",
        nargs="?",
        help="Command to get help for"
    )

    # Parse arguments
    args = parser.parse_args()

    # Handle no command
    if not args.command:
        cmd_help(args)
        sys.exit(0)

    # Handle per-command help
    if hasattr(args, 'help') and args.help:
        args.command = args.command
        cmd_help(args)
        sys.exit(0)

    # Dispatch to command handler
    command_map = {
        "init": cmd_init,
        "push": cmd_push,
        "pull": cmd_pull,
        "sync": cmd_sync,
        "help": lambda a: cmd_help(
            argparse.Namespace(command=getattr(a, 'help_command', None))
        ),
    }

    if args.command in command_map:
        command_map[args.command](args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
