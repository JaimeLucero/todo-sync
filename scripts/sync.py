#!/usr/bin/env python3
"""
Bidirectional sync between TODO.md and GitHub Issues.
Uses 'gh' CLI for all GitHub operations.
"""

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path


@dataclasses.dataclass
class TodoItem:
    """A single TODO item parsed from TODO.md."""
    text: str          # display text (no checkbox prefix)
    checked: bool      # True = - [x], False = - [ ]
    issue_id: int | None
    line_index: int    # 0-based line number for targeted rewrites
    section: str       # "open" or "done"
    description: str = ""  # optional description (multiline context)
    subtasks: list['Subtask'] = dataclasses.field(default_factory=list)  # optional subtasks


@dataclasses.dataclass
class Subtask:
    """A subtask within a TODO item."""
    text: str
    checked: bool


@dataclasses.dataclass
class IssueRecord:
    """A GitHub issue."""
    number: int
    title: str
    state: str  # "open" or "closed"
    body: str = ""  # issue body/description


class ClaudeClient:
    """Call Claude API to generate ticket content from plain-language prompts."""

    def __init__(self):
        """Initialize with API key from environment."""
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Set it to use LLM-powered ticket generation."
            )

    def generate_ticket(self, prompt: str) -> dict:
        """Generate ticket title, description, and subtasks from a prompt.

        Returns: {"title": str, "description": str, "subtasks": [str, ...]}
        Raises: ValueError if API call fails or response is invalid.
        """
        system_message = """You are a helpful assistant that generates well-structured GitHub issues.
When given a brief description or request, expand it into:
1. A concise, actionable title (short phrase)
2. A description with context and details
3. 2-4 subtasks or acceptance criteria

Return ONLY valid JSON with this structure (no markdown, no code blocks):
{
  "title": "Actionable title",
  "description": "Full context and requirements",
  "subtasks": ["Subtask 1", "Subtask 2", "Subtask 3"]
}"""

        request_body = {
            "model": "claude-opus-4-6",
            "max_tokens": 1024,
            "system": system_message,
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }

        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(request_body).encode('utf-8'),
                headers=headers,
                method="POST"
            )

            with urllib.request.urlopen(req) as response:
                response_data = json.loads(response.read().decode('utf-8'))

            # Extract text from response
            content = response_data.get("content", [])
            if not content:
                raise ValueError("Empty response from API")

            text = content[0].get("text", "")
            if not text:
                raise ValueError("No text in API response")

            # Parse JSON from response
            ticket_data = json.loads(text)

            # Validate required fields
            if "title" not in ticket_data or "description" not in ticket_data:
                raise ValueError("Response missing 'title' or 'description'")

            # Ensure subtasks is a list
            if "subtasks" not in ticket_data:
                ticket_data["subtasks"] = []
            elif not isinstance(ticket_data["subtasks"], list):
                ticket_data["subtasks"] = [ticket_data["subtasks"]]

            return ticket_data

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise ValueError(f"API request failed: {e.code} {error_body}")
        except urllib.error.URLError as e:
            raise ValueError(f"Network error: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse API response as JSON: {e}")


class TodoParser:
    """Parse and write TODO.md files with bidirectional sync support."""

    CHECKBOX_RE = re.compile(r'^- \[([ x])\] (.+?)(?:\s*<!--\s*issue:(\d+)\s*-->)?\s*$')
    SECTION_RE = re.compile(r'^##\s+(.+)$')
    DESCRIPTION_RE = re.compile(r'^\s+>\s(.*)$')  # indented > for description
    SUBTASK_RE = re.compile(r'^\s+- \[([ x])\] (.+)$')  # indented subtask

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
        idx = 0

        while idx < len(self._lines):
            line = self._lines[idx]

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
                idx += 1
                continue

            # Check for checkbox
            checkbox_match = self.CHECKBOX_RE.match(line)
            if checkbox_match:
                checked = checkbox_match.group(1) == 'x'
                text = checkbox_match.group(2)
                issue_id = int(checkbox_match.group(3)) if checkbox_match.group(3) else None
                item_line_index = idx

                # Collect description and subtasks
                description_lines = []
                subtasks = []
                idx += 1

                while idx < len(self._lines):
                    next_line = self._lines[idx]

                    # Check for description line
                    desc_match = self.DESCRIPTION_RE.match(next_line)
                    if desc_match:
                        description_lines.append(desc_match.group(1))
                        idx += 1
                        continue

                    # Check for subtask line
                    subtask_match = self.SUBTASK_RE.match(next_line)
                    if subtask_match:
                        subtask_checked = subtask_match.group(1) == 'x'
                        subtask_text = subtask_match.group(2)
                        subtasks.append(Subtask(text=subtask_text, checked=subtask_checked))
                        idx += 1
                        continue

                    # Not a description or subtask, break out
                    break

                items.append(TodoItem(
                    text=text,
                    checked=checked,
                    issue_id=issue_id,
                    line_index=item_line_index,
                    section=current_section or "unknown",
                    description='\n'.join(description_lines),
                    subtasks=subtasks
                ))
            else:
                idx += 1

        return items

    def write_back(self, items: list[TodoItem]) -> None:
        """Update TODO.md with modified items."""
        # Rebuild lines from items
        new_lines = []
        current_section = None
        item_idx = 0

        for idx, line in enumerate(self._lines):
            # Preserve section headings and non-item lines
            if self.SECTION_RE.match(line):
                new_lines.append(line)
                section_match = self.SECTION_RE.match(line)
                heading = section_match.group(1).lower()
                if "open" in heading:
                    current_section = "open"
                elif "done" in heading:
                    current_section = "done"
            elif self.CHECKBOX_RE.match(line):
                # This is a checkbox line; find its matching item
                if item_idx < len(items):
                    item = items[item_idx]
                    # Write the checkbox line
                    new_lines.append(self._format_item_lines(item)[0])
                    # Write description and subtasks
                    new_lines.extend(self._format_item_lines(item)[1:])
                    item_idx += 1
                else:
                    new_lines.append(line)
            elif self.DESCRIPTION_RE.match(line) or self.SUBTASK_RE.match(line):
                # Skip old description/subtask lines; they'll be rewritten with the item
                pass
            else:
                # Keep other lines as-is
                new_lines.append(line)

        self._lines = new_lines

        # Write back to file
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self._lines))
            if self._lines:  # Add final newline if file is not empty
                f.write('\n')

    def append_item(self, section: str, text: str, issue_id: int, description: str = "", subtasks: list[Subtask] | None = None) -> None:
        """Append a new item under the specified section (open/done)."""
        section_lower = section.lower()

        if section_lower not in self._section_lines:
            raise ValueError(f"Section '{section}' not found in TODO.md")

        section_idx = self._section_lines[section_lower]
        if subtasks is None:
            subtasks = []

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
                # Skip past any description/subtask lines
                while insert_idx < len(self._lines) and (
                    self.DESCRIPTION_RE.match(self._lines[insert_idx]) or
                    self.SUBTASK_RE.match(self._lines[insert_idx])
                ):
                    insert_idx += 1

        item = TodoItem(text=text, checked=False, issue_id=issue_id, line_index=-1,
                       section=section_lower, description=description, subtasks=subtasks)
        item_lines = self._format_item_lines(item)

        for i, line in enumerate(item_lines):
            self._lines.insert(insert_idx + i, line)

        # Write back immediately
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self._lines))
            if self._lines:
                f.write('\n')

    def remove_item(self, issue_id: int) -> bool:
        """Remove an item by issue ID. Removes checkbox + description/subtask lines. Returns True if found and removed."""
        items = self.load()
        item_to_remove = next((item for item in items if item.issue_id == issue_id), None)

        if item_to_remove is None:
            return False

        # Find the line index of the checkbox and remove it plus all following description/subtask lines
        checkbox_line = item_to_remove.line_index
        remove_count = 1  # Count the checkbox line itself

        # Count how many description and subtask lines follow
        if checkbox_line + 1 < len(self._lines):
            for i in range(checkbox_line + 1, len(self._lines)):
                if self.DESCRIPTION_RE.match(self._lines[i]) or self.SUBTASK_RE.match(self._lines[i]):
                    remove_count += 1
                else:
                    break

        # Remove the lines
        for _ in range(remove_count):
            if checkbox_line < len(self._lines):
                self._lines.pop(checkbox_line)

        # Write back
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(self._lines))
            if self._lines:
                f.write('\n')

        return True

    def update_item(self, issue_id: int, title: str | None = None, description: str | None = None,
                   add_subtask: str | None = None, remove_subtask: str | None = None) -> TodoItem | None:
        """Update an existing item. Returns the updated item or None if not found."""
        items = self.load()
        item = next((i for i in items if i.issue_id == issue_id), None)

        if item is None:
            return None

        # Apply mutations
        if title is not None:
            item.text = title
        if description is not None:
            item.description = description
        if add_subtask is not None:
            item.subtasks.append(Subtask(text=add_subtask, checked=False))
        if remove_subtask is not None:
            item.subtasks = [s for s in item.subtasks if s.text != remove_subtask]

        # Write back all items
        self.write_back(items)
        return item

    @staticmethod
    def _format_line(checked: bool, text: str, issue_id: int | None) -> str:
        """Format a checkbox line."""
        checkbox = '[x]' if checked else '[ ]'
        line = f"- {checkbox} {text}"
        if issue_id is not None:
            line += f" <!-- issue:{issue_id} -->"
        return line

    @staticmethod
    def _format_item_lines(item: TodoItem) -> list[str]:
        """Format a TodoItem as multiple lines (checkbox, description, subtasks)."""
        lines = []
        # Format checkbox line
        lines.append(TodoParser._format_line(item.checked, item.text, item.issue_id))

        # Format description lines
        if item.description:
            for desc_line in item.description.split('\n'):
                lines.append(f"  > {desc_line}")

        # Format subtask lines
        for subtask in item.subtasks:
            checkbox = '[x]' if subtask.checked else '[ ]'
            lines.append(f"  - {checkbox} {subtask.text}")

        return lines


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

    def create_issue(self, title: str, body: str = "") -> IssueRecord:
        """Create a new issue and return the record."""
        try:
            output = self._gh(
                "issue", "create",
                "--title", title,
                "--body", body
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

    def add_comment(self, number: int, body: str) -> None:
        """Add a comment to an issue."""
        try:
            self._gh("issue", "comment", str(number), "--body", body)
        except GitHubError as e:
            raise GitHubError(f"Failed to add comment to issue #{number}: {e}")

    def assign_issue(self, number: int, assignee: str) -> None:
        """Assign an issue to a user."""
        try:
            self._gh("issue", "edit", str(number), "--add-assignee", assignee)
        except GitHubError as e:
            raise GitHubError(f"Failed to assign issue #{number} to {assignee}: {e}")

    def get_current_user(self) -> str:
        """Get the login of the currently authenticated GitHub user."""
        try:
            output = self._gh("api", "user", "--jq", ".login")
            return output.strip()
        except GitHubError as e:
            raise GitHubError(f"Failed to get current user: {e}")

    def edit_issue(self, number: int, title: str | None = None, body: str | None = None) -> None:
        """Edit an issue's title and/or body."""
        args = ["issue", "edit", str(number)]
        if title is not None:
            args.extend(["--title", title])
        if body is not None:
            args.extend(["--body", body])

        try:
            self._gh(*args)
        except GitHubError as e:
            raise GitHubError(f"Failed to edit issue #{number}: {e}")

    def fetch_issue(self, number: int) -> IssueRecord:
        """Fetch a single issue with full details including body."""
        try:
            output = self._gh(
                "issue", "view",
                str(number),
                "--json", "number,title,state,body"
            )
        except GitHubError as e:
            raise GitHubError(f"Failed to fetch issue #{number}: {e}")

        # Expected format: "number\ttitle\tstate\tbody"
        # But body can contain tabs, so we split carefully
        parts = output.split('\t', 3)  # Split on first 3 tabs only
        if len(parts) < 3:
            raise GitHubError(f"Could not parse issue #{number} response")

        try:
            number = int(parts[0])
            title = parts[1]
            state = parts[2]
            body = parts[3] if len(parts) > 3 else ""
            return IssueRecord(number=number, title=title, state=state, body=body)
        except (ValueError, IndexError) as e:
            raise GitHubError(f"Could not parse issue #{number} response: {e}")


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
                        body = self._build_issue_body(item)
                        issue = self.github.create_issue(item.text, body)
                        item.issue_id = issue.number
                        self._log("CREATED", f"Issue #{issue.number}: {item.text}")

                # Existing item: sync title/body changes and manage state
                if item.issue_id in issue_map:
                    issue = issue_map[item.issue_id]

                    # Check if title or body changed (only if item has description or subtasks)
                    if item.description or item.subtasks:
                        if self.dry_run:
                            pass  # Log changes below
                        else:
                            full_issue = self.github.fetch_issue(item.issue_id)
                            new_body = self._build_issue_body(item)

                            title_changed = item.text != full_issue.title
                            body_changed = new_body.strip() != (full_issue.body or "").strip()

                            if title_changed or body_changed:
                                self.github.edit_issue(
                                    item.issue_id,
                                    title=item.text if title_changed else None,
                                    body=new_body if body_changed else None
                                )
                                self._log("UPDATED", f"Issue #{item.issue_id}: content synced")
                    else:
                        # Simple item without description/subtasks: check title anyway
                        if item.text != issue.title:
                            if not self.dry_run:
                                self.github.edit_issue(item.issue_id, title=item.text)
                                self._log("UPDATED", f"Issue #{item.issue_id}: title synced")

                    # Handle open/close state
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
                    # Issue exists in TODO.md: sync state and content
                    item = next(i for i in items if i.issue_id == issue.number)

                    # Sync title and body changes from GitHub
                    title_changed = issue.title != item.text
                    if title_changed:
                        if self.dry_run:
                            self._log("UPDATE", f"Issue #{issue.number} title from GitHub")
                        else:
                            item.text = issue.title
                            self._log("UPDATED", f"Issue #{issue.number}: title from GitHub")

                    # Sync body (description + subtasks) from GitHub
                    if not self.dry_run:
                        full_issue = self.github.fetch_issue(issue.number)
                        gh_description, gh_subtasks = self._parse_issue_body(full_issue.body or "")

                        body_changed = (gh_description != item.description or
                                       gh_subtasks != item.subtasks)
                        if body_changed:
                            item.description = gh_description
                            item.subtasks = gh_subtasks
                            self._log("UPDATED", f"Issue #{issue.number}: body from GitHub")

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

    def _build_issue_body(self, item: TodoItem) -> str:
        """Build a GitHub issue body from description and subtasks."""
        body_lines = []

        if item.description:
            body_lines.append(item.description)
            body_lines.append("")

        if item.subtasks:
            for subtask in item.subtasks:
                checkbox = "[x]" if subtask.checked else "[ ]"
                body_lines.append(f"- {checkbox} {subtask.text}")

        return '\n'.join(body_lines)

    def _parse_issue_body(self, body: str) -> tuple[str, list[Subtask]]:
        """Parse GitHub issue body into description and subtasks.

        Returns: (description, subtasks)
        """
        if not body:
            return "", []

        lines = body.split('\n')
        description_lines = []
        subtasks = []
        in_description = True

        for line in lines:
            # Check if this line is a subtask checkbox
            match = re.match(r'^- \[([ x])\] (.+)$', line)
            if match:
                in_description = False
                checked = match.group(1) == 'x'
                text = match.group(2)
                subtasks.append(Subtask(text=text, checked=checked))
            elif not in_description or line.strip():  # Still in description or non-empty line
                if in_description:
                    description_lines.append(line)

        # Clean up trailing empty lines from description
        while description_lines and not description_lines[-1].strip():
            description_lines.pop()

        description = '\n'.join(description_lines)
        return description, subtasks

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


def cmd_comment(args) -> None:
    """Add a comment to a GitHub issue."""
    try:
        github = GitHubClient()
        issue_id = args.issue_id
        message = args.message
        github.add_comment(issue_id, message)
        print(f"✓ Comment added to issue #{issue_id}")
    except GitHubError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_assign(args) -> None:
    """Assign a GitHub issue to the current user."""
    try:
        github = GitHubClient()
        issue_id = args.issue_id
        user = github.get_current_user()
        github.assign_issue(issue_id, user)
        print(f"✓ Issue #{issue_id} assigned to {user}")
    except GitHubError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_update(args) -> None:
    """Update an existing GitHub issue."""
    try:
        parser = TodoParser(args.todo)
        github = GitHubClient()
        issue_id = args.issue_id

        # Update the local TODO.md
        updated_item = parser.update_item(
            issue_id,
            title=args.title,
            description=args.description,
            add_subtask=args.add_subtask[0] if args.add_subtask else None,
            remove_subtask=args.remove_subtask[0] if args.remove_subtask else None
        )

        if updated_item is None:
            print(f"Error: Issue #{issue_id} not found in {args.todo}", file=sys.stderr)
            sys.exit(1)

        # Build the issue body and update GitHub
        engine = SyncEngine(args.todo, github)
        body = engine._build_issue_body(updated_item)
        github.edit_issue(issue_id, title=args.title, body=body if (args.description or args.add_subtask or args.remove_subtask) else None)

        print(f"✓ Issue #{issue_id} updated")
        if args.title:
            print(f"  Title: {args.title}")
        if args.description:
            print(f"  Description: {args.description}")
        if args.add_subtask:
            print(f"  Added subtask: {args.add_subtask[0]}")
        if args.remove_subtask:
            print(f"  Removed subtask: {args.remove_subtask[0]}")

    except (FileNotFoundError, GitHubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_remove(args) -> None:
    """Remove a GitHub issue from TODO.md."""
    try:
        parser = TodoParser(args.todo)
        github = GitHubClient()
        issue_id = args.issue_id

        # Remove from TODO.md
        removed = parser.remove_item(issue_id)

        if not removed:
            print(f"Error: Issue #{issue_id} not found in {args.todo}", file=sys.stderr)
            sys.exit(1)

        # Close on GitHub if requested
        if args.close:
            github.close_issue(issue_id)
            print(f"✓ Issue #{issue_id} removed from {args.todo} and closed on GitHub")
        else:
            print(f"✓ Issue #{issue_id} removed from {args.todo}")

    except (FileNotFoundError, GitHubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_add(args) -> None:
    """Add a new ticket to GitHub and TODO.md."""
    try:
        parser = TodoParser(args.todo)
        github = GitHubClient()
        engine = SyncEngine(args.todo, github)

        # Determine title, description, and subtasks
        if args.generate:
            # Generate from LLM
            try:
                claude = ClaudeClient()
                ticket_data = claude.generate_ticket(args.generate)
                title = ticket_data.get("title", "")
                description = ticket_data.get("description", "")
                subtasks = [Subtask(text=s, checked=False) for s in ticket_data.get("subtasks", [])]

                if not title:
                    print("Error: LLM did not generate a title", file=sys.stderr)
                    sys.exit(1)

                print(f"Generated ticket from prompt:\n  Title: {title}")
                if description:
                    print(f"  Description: {description[:100]}...")

            except ValueError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            # Manual entry
            if not args.title:
                print("Error: Must provide either a title or --generate", file=sys.stderr)
                sys.exit(1)

            title = args.title
            description = args.description or ""
            subtasks = [Subtask(text=s, checked=False) for s in (args.subtask or [])]

        # Create the GitHub issue
        body = engine._build_issue_body(
            TodoItem(
                text=title,
                checked=False,
                issue_id=None,
                line_index=-1,
                section="open",
                description=description,
                subtasks=subtasks
            )
        )
        issue = github.create_issue(title, body)

        # Append to TODO.md
        parser.load()  # load the file to populate section lines
        parser.append_item("open", title, issue.number, description, subtasks)

        print(f"✓ Created issue #{issue.number}: {title}")

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
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
        "comment": """
  comment — Add a comment to a GitHub issue

  Adds a comment to an existing GitHub issue.

  Usage:
    todo-sync comment <issue-id> "<message>"

  Example:
    todo-sync comment 42 "This is now ready for review"
""",
        "assign": """
  assign — Assign a GitHub issue to yourself

  Assigns an issue to the currently authenticated GitHub user.

  Usage:
    todo-sync assign <issue-id>

  Example:
    todo-sync assign 42
""",
        "update": """
  update — Update an existing GitHub issue

  Modifies an issue's title, description, or subtasks in both TODO.md and GitHub.

  Usage:
    todo-sync update <issue-id> [options]

  Options:
    --title "New title"              Change the issue title
    --description "New description"  Change the issue description
    --add-subtask "text"             Add a new unchecked subtask
    --remove-subtask "text"          Remove a subtask (matches text exactly)
    --todo FILE                      Path to TODO.md (default: TODO.md)

  Example:
    todo-sync update 42 --title "Updated task name"
    todo-sync update 42 --add-subtask "New step needed"
    todo-sync update 42 --description "More context here"
""",
        "remove": """
  remove — Remove an issue from TODO.md

  Deletes an issue from TODO.md. Optionally closes the GitHub issue.

  Usage:
    todo-sync remove <issue-id> [options]

  Options:
    --close    Also close the issue on GitHub (default: keep it open)
    --todo FILE    Path to TODO.md (default: TODO.md)

  Example:
    todo-sync remove 42
    todo-sync remove 42 --close
""",
        "add": """
  add — Create a new GitHub issue and add it to TODO.md

  Adds a new ticket directly via the CLI. Supports both manual entry and
  LLM-powered generation from a plain-language prompt.

  Usage:
    todo-sync add "Title" [options]
    todo-sync add --generate "Brief description" [options]

  Options:
    title (positional)           Ticket title (not needed if using --generate)
    --description "..."          Ticket description/context
    --subtask "..."              Add a subtask (repeatable)
    --generate "..."             Generate ticket from prompt using Claude API
    --todo FILE                  Path to TODO.md (default: TODO.md)

  Examples:
    todo-sync add "Fix login page bug"
    todo-sync add "Update API docs" --description "Add new endpoints" --subtask "Document auth" --subtask "Add examples"
    todo-sync add --generate "Build a user dashboard with real-time stats and export to PDF"
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
    init     Set up TODO.md in the current repo
    push     Push TODO.md tasks → GitHub Issues
    pull     Pull GitHub Issues → TODO.md
    sync     Bidirectional sync (push + pull)
    add      Create a new ticket
    comment  Add a comment to an issue
    assign   Assign an issue to yourself
    update   Update an existing issue
    remove   Remove an issue from TODO.md
    help     Show help for a command

  Run 'todo-sync help <command>' for details on a specific command.

  Examples:
    todo-sync init
    todo-sync add "New feature"
    todo-sync add --generate "Implement OAuth2 login"
    todo-sync sync

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
        version="todo-sync 1.1.1"
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

    # add
    add_parser = subparsers.add_parser(
        "add",
        help="Create a new ticket",
        add_help=False
    )
    add_parser.add_argument(
        "title",
        nargs="?",
        help="Ticket title (not needed if using --generate)"
    )
    add_parser.add_argument(
        "--description",
        help="Ticket description"
    )
    add_parser.add_argument(
        "--subtask",
        action="append",
        help="Add a subtask (repeatable)"
    )
    add_parser.add_argument(
        "--generate",
        help="Generate ticket from prompt using Claude API"
    )
    add_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    add_parser.add_argument("-h", "--help", action="store_true")

    # comment
    comment_parser = subparsers.add_parser(
        "comment",
        help="Add a comment to an issue",
        add_help=False
    )
    comment_parser.add_argument(
        "issue_id",
        type=int,
        help="Issue number"
    )
    comment_parser.add_argument(
        "message",
        help="Comment message"
    )
    comment_parser.add_argument("-h", "--help", action="store_true")

    # assign
    assign_parser = subparsers.add_parser(
        "assign",
        help="Assign an issue to yourself",
        add_help=False
    )
    assign_parser.add_argument(
        "issue_id",
        type=int,
        help="Issue number"
    )
    assign_parser.add_argument("-h", "--help", action="store_true")

    # update
    update_parser = subparsers.add_parser(
        "update",
        help="Update an existing issue",
        add_help=False
    )
    update_parser.add_argument(
        "issue_id",
        type=int,
        help="Issue number"
    )
    update_parser.add_argument(
        "--title",
        help="New issue title"
    )
    update_parser.add_argument(
        "--description",
        help="New issue description"
    )
    update_parser.add_argument(
        "--add-subtask",
        nargs=1,
        help="Add a new subtask"
    )
    update_parser.add_argument(
        "--remove-subtask",
        nargs=1,
        help="Remove a subtask by text"
    )
    update_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    update_parser.add_argument("-h", "--help", action="store_true")

    # remove
    remove_parser = subparsers.add_parser(
        "remove",
        help="Remove an issue from TODO.md",
        add_help=False
    )
    remove_parser.add_argument(
        "issue_id",
        type=int,
        help="Issue number"
    )
    remove_parser.add_argument(
        "--close",
        action="store_true",
        help="Also close the GitHub issue"
    )
    remove_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    remove_parser.add_argument("-h", "--help", action="store_true")

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
        "add": cmd_add,
        "comment": cmd_comment,
        "assign": cmd_assign,
        "update": cmd_update,
        "remove": cmd_remove,
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
