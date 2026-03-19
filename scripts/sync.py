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
    labels: list[str] = dataclasses.field(default_factory=list)  # optional labels for GitHub issues
    notion_id: str | None = None  # Notion page UUID
    status: str | None = None  # Notion status: "todo", "assigned", "ongoing", "PR", "staging", "merge", "QA", "done"
    assigned: str | None = None  # GitHub/Notion assignee (username or name)


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
    assignee: str | None = None  # login of assigned user


# --- Notion Configuration ---

NOTION_CONFIG_FILE = ".todo-sync/notion.json"


def load_notion_config() -> dict:
    """Load Notion credentials from .todo-sync/notion.json.
    Raises FileNotFoundError if not configured yet.
    """
    path = Path(NOTION_CONFIG_FILE)
    if not path.exists():
        raise FileNotFoundError(
            "Notion not configured. Run 'todo-sync notion-setup' first."
        )
    with open(path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    if 'token' not in config or 'database_id' not in config:
        raise ValueError(
            f"Invalid notion config at {path}. "
            "Expected keys: 'token', 'database_id'."
        )
    return config


def save_notion_config(token: str, database_id: str) -> None:
    """Write Notion credentials to .todo-sync/notion.json."""
    path = Path(NOTION_CONFIG_FILE)
    path.parent.mkdir(exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({"token": token, "database_id": database_id}, f, indent=2)
    # chmod 600 — token is sensitive
    path.chmod(0o600)


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

    CHECKBOX_RE = re.compile(
        r'^- \[([ x])\] (.+?)'
        r'((?:\s*<!--\s*(?:issue:\d+|notion:[A-Za-z0-9_-]+)\s*-->)*)'
        r'\s*$'
    )
    SECTION_RE = re.compile(r'^##\s+(.+)$')
    DESCRIPTION_RE = re.compile(r'^\s+>\s(.*)$')  # indented > for description
    LABELS_RE = re.compile(r'^\s+labels:\s*(.+?)\s*$')  # indented labels metadata
    STATUS_RE = re.compile(r'^\s+status:\s*(.+?)\s*$')  # indented status metadata (Notion)
    ASSIGNED_RE = re.compile(r'^\s+assigned:\s*(.+?)\s*$')  # indented assigned metadata (GitHub/Notion assignee)
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
        in_code_fence = False
        idx = 0

        while idx < len(self._lines):
            line = self._lines[idx]

            # Check for code fence (triple backticks)
            if line.strip().startswith('```'):
                in_code_fence = not in_code_fence
                idx += 1
                continue

            # Skip parsing while inside code fence
            if in_code_fence:
                idx += 1
                continue

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
                else:
                    # Reset section for unknown headings (e.g., ## Reference)
                    current_section = None
                idx += 1
                continue

            # Check for checkbox (only if current_section is set)
            checkbox_match = self.CHECKBOX_RE.match(line) if current_section else None
            if checkbox_match:
                checked = checkbox_match.group(1) == 'x'
                text = checkbox_match.group(2)
                # Extract issue_id and notion_id from the suffix block (group 3)
                suffix = checkbox_match.group(3)
                issue_match = re.search(r'<!--\s*issue:(\d+)\s*-->', suffix)
                notion_match = re.search(r'<!--\s*notion:([A-Za-z0-9_-]+)\s*-->', suffix)
                issue_id = int(issue_match.group(1)) if issue_match else None
                notion_id = notion_match.group(1) if notion_match else None
                item_line_index = idx

                # Collect description, labels, status, assigned, and subtasks
                description_lines = []
                labels = []
                status = None
                assigned = None
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

                    # Check for labels line (must come after description, before subtasks)
                    labels_match = self.LABELS_RE.match(next_line)
                    if labels_match:
                        labels_str = labels_match.group(1)
                        # Parse comma-separated labels, strip whitespace
                        labels = [label.strip() for label in labels_str.split(',') if label.strip()]
                        idx += 1
                        continue

                    # Check for status line (Notion status metadata)
                    status_match = self.STATUS_RE.match(next_line)
                    if status_match:
                        status = status_match.group(1).strip()
                        idx += 1
                        continue

                    # Check for assigned line (GitHub/Notion assignee metadata)
                    assigned_match = self.ASSIGNED_RE.match(next_line)
                    if assigned_match:
                        assigned = assigned_match.group(1).strip()
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

                    # Not a description, labels, status, assigned, or subtask, break out
                    break

                items.append(TodoItem(
                    text=text,
                    checked=checked,
                    issue_id=issue_id,
                    line_index=item_line_index,
                    section=current_section or "unknown",
                    description='\n'.join(description_lines),
                    subtasks=subtasks,
                    labels=labels,
                    notion_id=notion_id,
                    status=status,
                    assigned=assigned
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
                    # Write description, labels, and subtasks
                    new_lines.extend(self._format_item_lines(item)[1:])
                    item_idx += 1
                else:
                    new_lines.append(line)
            elif self.DESCRIPTION_RE.match(line) or self.LABELS_RE.match(line) or self.STATUS_RE.match(line) or self.ASSIGNED_RE.match(line) or self.SUBTASK_RE.match(line):
                # Skip old description/labels/status/assigned/subtask lines; they'll be rewritten with the item
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
                # Skip past any description/labels/status/assigned/subtask lines
                while insert_idx < len(self._lines) and (
                    self.DESCRIPTION_RE.match(self._lines[insert_idx]) or
                    self.LABELS_RE.match(self._lines[insert_idx]) or
                    self.STATUS_RE.match(self._lines[insert_idx]) or
                    self.ASSIGNED_RE.match(self._lines[insert_idx]) or
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

        # Count how many description, labels, status, assigned, and subtask lines follow
        if checkbox_line + 1 < len(self._lines):
            for i in range(checkbox_line + 1, len(self._lines)):
                if self.DESCRIPTION_RE.match(self._lines[i]) or self.LABELS_RE.match(self._lines[i]) or self.STATUS_RE.match(self._lines[i]) or self.ASSIGNED_RE.match(self._lines[i]) or self.SUBTASK_RE.match(self._lines[i]):
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
                   add_subtask: str | None = None, remove_subtask: str | None = None,
                   labels: list[str] | None = None) -> TodoItem | None:
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
        if labels is not None:
            item.labels = labels

        # Write back all items
        self.write_back(items)
        return item

    @staticmethod
    def _format_line(checked: bool, text: str, issue_id: int | None, notion_id: str | None = None) -> str:
        """Format a checkbox line."""
        checkbox = '[x]' if checked else '[ ]'
        line = f"- {checkbox} {text}"
        if issue_id is not None:
            line += f" <!-- issue:{issue_id} -->"
        if notion_id is not None:
            line += f" <!-- notion:{notion_id} -->"
        return line

    @staticmethod
    def _format_item_lines(item: TodoItem) -> list[str]:
        """Format a TodoItem as multiple lines (checkbox, description, status, assigned, labels, subtasks)."""
        lines = []
        # Format checkbox line
        lines.append(TodoParser._format_line(item.checked, item.text, item.issue_id, item.notion_id))

        # Format description lines
        if item.description:
            for desc_line in item.description.split('\n'):
                lines.append(f"  > {desc_line}")

        # Format status line (Notion)
        if item.status:
            lines.append(f"  status: {item.status}")

        # Format assigned line (GitHub/Notion)
        if item.assigned:
            lines.append(f"  assigned: {item.assigned}")

        # Format labels line
        if item.labels:
            labels_str = ", ".join(item.labels)
            lines.append(f"  labels: {labels_str}")

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
                "--json", "number,title,state,assignees"
            )
        except GitHubError as e:
            raise GitHubError(f"Failed to fetch issues: {e}")

        try:
            data = json.loads(output)
            issues = []
            for item in data:
                try:
                    # Extract assignee login if assigned (GitHub returns assignees array)
                    assignee = None
                    assignees = item.get("assignees", [])
                    if assignees and len(assignees) > 0:
                        assignee = assignees[0].get("login")

                    issues.append(IssueRecord(
                        number=item["number"],
                        title=item["title"],
                        state=item["state"].lower(),
                        assignee=assignee
                    ))
                except (KeyError, ValueError):
                    pass  # Skip malformed items
            return issues
        except json.JSONDecodeError as e:
            raise GitHubError(f"Failed to parse issues JSON: {e}")

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

        try:
            data = json.loads(output)
            return IssueRecord(
                number=data["number"],
                title=data["title"],
                state=data["state"].lower(),
                body=data.get("body") or ""
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise GitHubError(f"Could not parse issue #{number} response: {e}")

    def get_issue_labels(self, number: int) -> list[str]:
        """Fetch labels for an issue."""
        try:
            output = self._gh(
                "issue", "view",
                str(number),
                "--json", "labels",
                "--jq", ".labels[].name"
            )
        except GitHubError:
            return []  # Return empty list if fetch fails

        # Output is one label per line
        if not output.strip():
            return []
        return output.strip().split('\n')

    def add_labels(self, number: int, labels: list[str]) -> None:
        """Add labels to an issue."""
        if not labels:
            return

        args = ["issue", "edit", str(number)]
        for label in labels:
            args.extend(["--add-label", label])

        try:
            self._gh(*args)
        except GitHubError as e:
            raise GitHubError(f"Failed to add labels to issue #{number}: {e}")

    def remove_labels(self, number: int, labels: list[str]) -> None:
        """Remove labels from an issue."""
        if not labels:
            return

        args = ["issue", "edit", str(number)]
        for label in labels:
            args.extend(["--remove-label", label])

        try:
            self._gh(*args)
        except GitHubError as e:
            raise GitHubError(f"Failed to remove labels from issue #{number}: {e}")

    def get_available_labels(self) -> list[str]:
        """Fetch all available labels in the repository."""
        try:
            output = self._gh(
                "label", "list",
                "--json", "name",
                "--jq", ".[] | .name"
            )
        except GitHubError:
            return []

        if not output.strip():
            return []
        return output.strip().split('\n')

    def create_label(self, name: str, color: str = "cccccc", description: str = "") -> None:
        """Create a new label in the repository."""
        args = ["label", "create", name, "--color", color]
        if description:
            args.extend(["--description", description])

        try:
            self._gh(*args)
        except GitHubError as e:
            # Label might already exist, which is fine
            if "already exists" not in str(e).lower():
                raise GitHubError(f"Failed to create label '{name}': {e}")


class NotionError(Exception):
    """Notion API operation failed."""
    pass


class NotionClient:
    """Interact with Notion API using stdlib urllib (no external dependencies)."""

    BASE_URL = "https://api.notion.com/v1"
    NOTION_VERSION = "2022-06-28"
    VALID_STATUSES = {"todo", "assigned", "ongoing", "PR", "staging", "merge", "QA", "done"}

    def __init__(self, token: str):
        self.token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": self.NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        """Make an HTTP request to the Notion API."""
        url = f"{self.BASE_URL}/{path}"
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise NotionError(f"Notion API {method} {path} failed: {e.code} {error_body}")
        except urllib.error.URLError as e:
            raise NotionError(f"Network error contacting Notion: {e}")
        except json.JSONDecodeError as e:
            raise NotionError(f"Failed to parse Notion API response: {e}")

    @staticmethod
    def _rich_text(value: str) -> list[dict]:
        """Build a Notion rich_text array from a plain string."""
        # Notion rich_text blocks cap at 2000 chars each
        return [{"type": "text", "text": {"content": value[:2000]}}]

    @staticmethod
    def _extract_plain_text(rich_text_array: list[dict]) -> str:
        """Extract plain text from a Notion rich_text array."""
        return "".join(
            block.get("plain_text", "") for block in rich_text_array
        )

    @staticmethod
    def _serialize_subtasks(subtasks: list[Subtask]) -> str:
        """Serialize subtasks to plain-text format for Notion storage."""
        lines = []
        for s in subtasks:
            mark = "[x]" if s.checked else "[ ]"
            lines.append(f"- {mark} {s.text}")
        return "\n".join(lines)

    @staticmethod
    def _deserialize_subtasks(text: str) -> list[Subtask]:
        """Deserialize subtasks from plain-text format stored in Notion."""
        subtasks = []
        pattern = re.compile(r'^- \[([ x])\] (.+)$')
        for line in text.split('\n'):
            m = pattern.match(line.strip())
            if m:
                subtasks.append(Subtask(text=m.group(2), checked=m.group(1) == 'x'))
        return subtasks

    def _build_properties(
        self, title: str, status: str | None, description: str,
        subtasks: list[Subtask], github_issue: int | None,
        labels: list[str] | None = None, checked: bool = False
    ) -> dict:
        """Build the Notion page properties payload."""
        props: dict = {
            "Name": {"title": self._rich_text(title)},
            "Description": {"rich_text": self._rich_text(description)},
            "Subtasks": {"rich_text": self._rich_text(self._serialize_subtasks(subtasks))},
            "Checked": {"checkbox": checked},
        }
        if status and status in self.VALID_STATUSES:
            props["Status"] = {"select": {"name": status}}
        if github_issue is not None:
            props["GitHubIssue"] = {"number": github_issue}
        if labels is not None:
            props["Labels"] = {"rich_text": self._rich_text(", ".join(labels))}
        return props

    def create_page(
        self, database_id: str, title: str, status: str | None,
        description: str, subtasks: list[Subtask],
        github_issue: int | None, labels: list[str] | None = None,
        checked: bool = False
    ) -> str:
        """Create a new page in the Notion database. Returns the new page's ID."""
        body = {
            "parent": {"database_id": database_id},
            "properties": self._build_properties(
                title, status, description, subtasks,
                github_issue, labels, checked
            )
        }
        result = self._request("POST", "pages", body)
        return result["id"]

    def update_page(
        self, page_id: str, title: str | None = None,
        status: str | None = None, description: str | None = None,
        subtasks: list[Subtask] | None = None,
        labels: list[str] | None = None, checked: bool | None = None
    ) -> None:
        """Update an existing Notion page's properties."""
        props: dict = {}
        if title is not None:
            props["Name"] = {"title": self._rich_text(title)}
        if status is not None and status in self.VALID_STATUSES:
            props["Status"] = {"select": {"name": status}}
        if description is not None:
            props["Description"] = {"rich_text": self._rich_text(description)}
        if subtasks is not None:
            props["Subtasks"] = {"rich_text": self._rich_text(
                self._serialize_subtasks(subtasks)
            )}
        if labels is not None:
            props["Labels"] = {"rich_text": self._rich_text(", ".join(labels))}
        if checked is not None:
            props["Checked"] = {"checkbox": checked}
        if props:
            self._request("PATCH", f"pages/{page_id}", {"properties": props})

    def query_database(self, database_id: str) -> list[dict]:
        """Return all pages from the Notion database. Handles pagination."""
        pages = []
        body: dict = {"page_size": 100}
        while True:
            result = self._request("POST", f"databases/{database_id}/query", body)
            pages.extend(result.get("results", []))
            if not result.get("has_more"):
                break
            body["start_cursor"] = result["next_cursor"]
        return pages

    def fetch_page(self, page_id: str) -> dict:
        """Fetch a single Notion page's full properties."""
        return self._request("GET", f"pages/{page_id}")

    def extract_item_from_page(self, page: dict) -> dict:
        """Extract structured dict from a raw Notion page object."""
        props = page.get("properties", {})

        title_blocks = props.get("Name", {}).get("title", [])
        title = self._extract_plain_text(title_blocks)

        status_select = props.get("Status", {}).get("select")
        status = status_select.get("name") if status_select else None

        desc_blocks = props.get("Description", {}).get("rich_text", [])
        description = self._extract_plain_text(desc_blocks)

        subtask_blocks = props.get("Subtasks", {}).get("rich_text", [])
        subtask_text = self._extract_plain_text(subtask_blocks)
        subtasks = self._deserialize_subtasks(subtask_text)

        github_issue_raw = props.get("GitHubIssue", {}).get("number")
        github_issue = int(github_issue_raw) if github_issue_raw is not None else None

        label_blocks = props.get("Labels", {}).get("rich_text", [])
        label_text = self._extract_plain_text(label_blocks)
        labels = [l.strip() for l in label_text.split(",") if l.strip()]

        checked = props.get("Checked", {}).get("checkbox", False)

        return {
            "notion_id": page["id"],
            "title": title,
            "status": status,
            "description": description,
            "subtasks": subtasks,
            "github_issue": github_issue,
            "labels": labels,
            "checked": checked,
        }


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

                        # Add labels if present
                        if item.labels:
                            self.github.add_labels(item.issue_id, item.labels)
                            self._log("UPDATED", f"Issue #{item.issue_id}: labels added ({', '.join(item.labels)})")

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

                    # Sync labels: only when TODO.md item explicitly declares labels
                    if not self.dry_run and item.labels:
                        current_labels = self.github.get_issue_labels(item.issue_id)
                        labels_to_add = [l for l in item.labels if l not in current_labels]
                        labels_to_remove = [l for l in current_labels if l not in item.labels]

                        if labels_to_add:
                            self.github.add_labels(item.issue_id, labels_to_add)
                            self._log("UPDATED", f"Issue #{item.issue_id}: labels added ({', '.join(labels_to_add)})")

                        if labels_to_remove:
                            self.github.remove_labels(item.issue_id, labels_to_remove)
                            self._log("UPDATED", f"Issue #{item.issue_id}: labels removed ({', '.join(labels_to_remove)})")

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

                    # Sync labels from GitHub
                    if not self.dry_run:
                        gh_labels = self.github.get_issue_labels(issue.number)
                        if gh_labels != item.labels:
                            item.labels = gh_labels
                            self._log("UPDATED", f"Issue #{issue.number}: labels synced from GitHub")

                    # Sync assignee from GitHub
                    if not self.dry_run:
                        if issue.assignee != item.assigned:
                            item.assigned = issue.assignee
                            if issue.assignee:
                                self._log("UPDATED", f"Issue #{issue.number}: assigned to {issue.assignee}")
                            else:
                                self._log("UPDATED", f"Issue #{issue.number}: assignee cleared")

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


class NotionSyncEngine:
    """Orchestrate sync between TODO.md and a Notion database."""

    def __init__(self, todo_path: str, notion: NotionClient,
                 database_id: str, dry_run: bool = False):
        self.parser = TodoParser(todo_path)
        self.notion = notion
        self.database_id = database_id
        self.dry_run = dry_run
        self._changelog: list[str] = []

    def push(self) -> None:
        """Push TODO.md items → Notion database."""
        items = self.parser.load()
        changed = False

        for item in items:
            try:
                if item.notion_id is None:
                    # Determine initial status from item
                    initial_status = self._infer_status(item)
                    if self.dry_run:
                        self._log("CREATE", f"Notion page for '{item.text}'")
                    else:
                        page_id = self.notion.create_page(
                            self.database_id,
                            title=item.text,
                            status=initial_status,
                            description=item.description,
                            subtasks=item.subtasks,
                            github_issue=item.issue_id,
                            labels=item.labels,
                            checked=item.checked,
                        )
                        item.notion_id = page_id
                        item.status = initial_status
                        changed = True
                        self._log("CREATED", f"Notion page {page_id[:8]}... for '{item.text}'")
                else:
                    if self.dry_run:
                        self._log("UPDATE", f"Notion page for '{item.text}'")
                    else:
                        self.notion.update_page(
                            item.notion_id,
                            title=item.text,
                            description=item.description,
                            subtasks=item.subtasks,
                            labels=item.labels,
                            checked=item.checked,
                            # Do NOT overwrite status on push — Notion is authoritative for status
                        )
                        self._log("UPDATED", f"Notion page {item.notion_id[:8]}... for '{item.text}'")
            except NotionError as e:
                self._log("ERROR", f"Failed to push '{item.text}': {e}")

        if not self.dry_run and changed:
            self.parser.write_back(items)
        self.print_summary()

    def pull(self) -> None:
        """Pull Notion database → TODO.md."""
        items = self.parser.load()
        pages = self.notion.query_database(self.database_id)

        notion_id_map = {item.notion_id: item for item in items if item.notion_id}
        issue_id_map = {item.issue_id: item for item in items if item.issue_id}
        changed = False

        for page in pages:
            try:
                pd = self.notion.extract_item_from_page(page)
                notion_id = pd["notion_id"]

                # Try to match by notion_id first, then github_issue
                matched_item = notion_id_map.get(notion_id)
                if matched_item is None and pd["github_issue"] is not None:
                    matched_item = issue_id_map.get(pd["github_issue"])

                if matched_item is not None:
                    # Update local item from Notion
                    if self.dry_run:
                        self._log("UPDATE", f"'{matched_item.text}' from Notion")
                    else:
                        # Status is always pulled from Notion (Notion is authoritative)
                        if pd["status"] and pd["status"] != matched_item.status:
                            matched_item.status = pd["status"]
                            changed = True
                            self._log("UPDATED", f"'{matched_item.text}': status → {pd['status']}")
                        # Title sync
                        if pd["title"] and pd["title"] != matched_item.text:
                            matched_item.text = pd["title"]
                            changed = True
                            self._log("UPDATED", f"Title → '{pd['title']}'")
                        # Description sync
                        if pd["description"] != matched_item.description:
                            matched_item.description = pd["description"]
                            changed = True
                        # Stamp notion_id if matched via github_issue
                        if matched_item.notion_id is None:
                            matched_item.notion_id = notion_id
                            changed = True
                else:
                    # New page from Notion — append to TODO.md
                    if self.dry_run:
                        self._log("APPEND", f"New Notion item: '{pd['title']}'")
                    else:
                        new_item = TodoItem(
                            text=pd["title"],
                            checked=pd["checked"],
                            issue_id=pd["github_issue"],
                            line_index=-1,
                            section="open" if not pd["checked"] else "done",
                            description=pd["description"],
                            subtasks=pd["subtasks"],
                            labels=pd["labels"],
                            notion_id=notion_id,
                            status=pd["status"],
                        )
                        # Append the item
                        self.parser.append_item(
                            new_item.section, new_item.text,
                            new_item.issue_id if new_item.issue_id is not None else 0,
                            new_item.description, new_item.subtasks
                        )
                        # Reload and patch notion_id/status via write_back
                        items = self.parser.load()
                        for i in items:
                            if i.text == new_item.text and i.notion_id is None:
                                i.notion_id = notion_id
                                i.status = pd["status"]
                        changed = True
                        self._log("APPENDED", f"'{pd['title']}' from Notion")
            except NotionError as e:
                self._log("ERROR", f"Failed to pull Notion page {page.get('id', '?')}: {e}")

        if not self.dry_run and changed:
            self.parser.write_back(items)
        self.print_summary()

    def sync(self) -> None:
        """Bidirectional sync: push then pull."""
        self.push()
        self.pull()

    @staticmethod
    def _infer_status(item: TodoItem) -> str:
        """Infer initial Notion status from a TODO.md item that has no status yet."""
        if item.status:
            return item.status
        if item.checked:
            return "done"
        return "todo"

    def _log(self, action: str, detail: str) -> None:
        msg = f"[{action}] {detail}"
        self._changelog.append(msg)
        print(msg)

    def print_summary(self) -> None:
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

    if todo_path.exists() and not args.force:
        print(f"⊘ {todo_path} already exists (skipped, use --force to overwrite)")
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


def cmd_label(args) -> None:
    """Set labels for a GitHub issue."""
    try:
        parser = TodoParser(args.todo)
        github = GitHubClient()
        issue_id = args.issue_id
        labels_str = args.labels

        # Parse labels from comma-separated string
        new_labels = [l.strip() for l in labels_str.split(',') if l.strip()]

        # Get available labels on GitHub
        available_labels = github.get_available_labels()

        # Auto-create missing labels
        missing_labels = [l for l in new_labels if l not in available_labels]
        if missing_labels:
            print(f"Creating {len(missing_labels)} new label(s)...")
            for label in missing_labels:
                github.create_label(label)
                print(f"  Created: {label}")

        # Update TODO.md
        updated_item = parser.update_item(issue_id, labels=new_labels)

        if updated_item is None:
            print(f"Error: Issue #{issue_id} not found in {args.todo}", file=sys.stderr)
            sys.exit(1)

        # Get current labels on GitHub and sync
        current_labels = github.get_issue_labels(issue_id)
        labels_to_add = [l for l in new_labels if l not in current_labels]
        labels_to_remove = [l for l in current_labels if l not in new_labels]

        if labels_to_add:
            github.add_labels(issue_id, labels_to_add)
        if labels_to_remove:
            github.remove_labels(issue_id, labels_to_remove)

        print(f"✓ Issue #{issue_id} labels updated")
        if new_labels:
            print(f"  Labels: {', '.join(new_labels)}")
        else:
            print(f"  Labels: (none)")

    except (FileNotFoundError, GitHubError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_labels(args) -> None:
    """List all available labels in the repository."""
    try:
        github = GitHubClient()
        labels = github.get_available_labels()

        if not labels:
            print("No labels found in this repository")
            return

        print(f"Available labels ({len(labels)}):")
        for label in sorted(labels):
            print(f"  - {label}")

    except GitHubError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_list(args) -> None:
    """List tickets with their issue numbers."""
    try:
        parser = TodoParser(args.todo)
        items = parser.load()

        # Separate by section and checked status
        open_items = [i for i in items if i.section == "open" and not i.checked]
        done_items = [i for i in items if i.section == "done" or i.checked]

        # Filter based on --all flag
        if not args.all:
            done_items = []

        def print_section(title, section_items):
            if not section_items:
                return
            print(f"{title} ({len(section_items)})")
            for item in section_items:
                num = f"#{item.issue_id}" if item.issue_id else "(unsynced)"
                # Pad number column to keep titles aligned
                print(f"  {num:<12} {item.text}")
            print()

        print_section("Open", open_items)
        if done_items:
            print_section("Done", done_items)

        if not open_items and not done_items:
            print("No tickets found.")

    except FileNotFoundError as e:
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


def cmd_notion_setup(args) -> None:
    """Prompt for Notion credentials and save to .todo-sync/notion.json."""
    print("Notion Setup")
    print("You need a Notion integration token and a database ID.")
    print("1. Create an integration at https://www.notion.so/my-integrations")
    print("2. Share your database with the integration.")
    print()

    token = input("Notion integration token (secret_...): ").strip()
    if not token:
        print("Error: Token cannot be empty", file=sys.stderr)
        sys.exit(1)

    database_id = input("Notion database ID (32-char hex or full URL): ").strip()
    # Accept full URL and extract the ID
    if database_id.startswith("https://"):
        # URL format: https://www.notion.so/.../<id>?v=...
        parts = database_id.split("/")
        raw_id = parts[-1].split("?")[0]
        # Remove dashes if present, then reformat
        database_id = raw_id.replace("-", "")
    if not database_id:
        print("Error: Database ID cannot be empty", file=sys.stderr)
        sys.exit(1)

    try:
        save_notion_config(token, database_id)
        print(f"Notion config saved to {NOTION_CONFIG_FILE}")
        print("Run 'todo-sync notion-push' to push your TODO.md to Notion.")
    except OSError as e:
        print(f"Error saving config: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_notion_push(args) -> None:
    """Push TODO.md items → Notion database."""
    try:
        config = load_notion_config()
        notion = NotionClient(config["token"])
        engine = NotionSyncEngine(
            args.todo, notion, config["database_id"], dry_run=args.dry_run
        )
        engine.push()
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except NotionError as e:
        print(f"Notion error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_notion_pull(args) -> None:
    """Pull Notion database → TODO.md."""
    try:
        config = load_notion_config()
        notion = NotionClient(config["token"])
        engine = NotionSyncEngine(
            args.todo, notion, config["database_id"], dry_run=args.dry_run
        )
        engine.pull()
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except NotionError as e:
        print(f"Notion error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_notion_sync(args) -> None:
    """Bidirectional sync: TODO.md <-> Notion."""
    try:
        config = load_notion_config()
        notion = NotionClient(config["token"])
        engine = NotionSyncEngine(
            args.todo, notion, config["database_id"], dry_run=args.dry_run
        )
        engine.sync()
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except NotionError as e:
        print(f"Notion error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_help(args) -> None:
    """Show help for a command."""
    commands_help = {
        "init": """
  init — Set up TODO.md in the current repository

  Initialize a TODO.md file in the current directory with a basic structure
  (Open and Done sections). Safe to run multiple times—won't overwrite an
  existing TODO.md unless --force is specified.

  Usage:
    todo-sync init [options]

  Options:
    --with-makefile    Also inject Makefile targets (make todo-sync, etc.)
    --force            Overwrite existing TODO.md (reinitialize with template)
    --todo FILE        Path to TODO.md (default: TODO.md)

  Example:
    todo-sync init
    todo-sync init --with-makefile
    todo-sync init --force --todo tasks/TODO.md
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
        "label": """
  label — Set labels for a GitHub issue

  Sets labels for an issue in both TODO.md and GitHub. Replaces any existing labels.
  Automatically creates missing labels and syncs with GitHub.

  Usage:
    todo-sync label <issue-id> "label1, label2, label3" [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)

  Example:
    todo-sync label 42 "bug, urgent"
    todo-sync label 42 "feature, documentation, backend"
    todo-sync label 42 ""    # Clear all labels
""",
        "labels": """
  labels — List all available labels in the repository

  Shows all labels available in the GitHub repository. Useful for checking what
  labels exist before assigning them to issues.

  Usage:
    todo-sync labels

  Example:
    todo-sync labels
""",
        "list": """
  list — List open tickets with their issue numbers

  Show all open tickets so you can find issue numbers for other commands
  (comment, assign, label, etc.).

  Usage:
    todo-sync list [options]

  Options:
    --all       Also show completed (done) tickets
    --todo FILE Path to TODO.md (default: TODO.md)

  Example:
    todo-sync list
    todo-sync list --all
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
        "notion-setup": """
  notion-setup — Configure Notion integration

  Sets up the Notion API token and database ID for syncing. Stores credentials
  securely in .todo-sync/notion.json (with restricted permissions).

  Usage:
    todo-sync notion-setup

  Instructions:
    1. Create an integration at https://www.notion.so/my-integrations
    2. Share your Notion database with the integration
    3. Run this command and enter your token and database ID

  Example:
    todo-sync notion-setup
""",
        "notion-push": """
  notion-push — Push TODO.md items to Notion database

  Syncs TODO.md tasks to a Notion database in one direction:
  - Unchecked items without a Notion page ID create new Notion pages
  - Updates existing pages with latest title, description, and subtasks
  - Does NOT overwrite the Status field in Notion (PM controls status)

  Usage:
    todo-sync notion-push [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)
    --dry-run      Preview changes without making them

  Example:
    todo-sync notion-push
    todo-sync notion-push --dry-run
""",
        "notion-pull": """
  notion-pull — Pull Notion database to TODO.md

  Syncs a Notion database to TODO.md in one direction:
  - Updates TODO.md items with status, title, and description from Notion
  - New Notion pages are appended to TODO.md
  - Notion is authoritative for the Status field

  Usage:
    todo-sync notion-pull [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)
    --dry-run      Preview changes without making them

  Example:
    todo-sync notion-pull
    todo-sync notion-pull --dry-run
""",
        "notion-sync": """
  notion-sync — Bidirectional sync between TODO.md and Notion

  Two-way sync: performs a push first (TODO.md → Notion), then a pull
  (Notion → TODO.md). Local TODO.md content wins; Notion status wins.

  Usage:
    todo-sync notion-sync [options]

  Options:
    --todo FILE    Path to TODO.md (default: TODO.md)
    --dry-run      Preview changes without making them

  Example:
    todo-sync notion-sync
    todo-sync notion-sync --dry-run
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
    init          Set up TODO.md in the current repo
    push          Push TODO.md tasks → GitHub Issues
    pull          Pull GitHub Issues → TODO.md
    sync          Bidirectional sync (push + pull)
    add           Create a new ticket
    list          List open tickets with numbers
    comment       Add a comment to an issue
    assign        Assign an issue to yourself
    update        Update an existing issue
    label         Set labels for an issue
    labels        List available labels in the repo
    remove        Remove an issue from TODO.md
    notion-setup  Configure Notion integration
    notion-push   Push TODO.md → Notion database
    notion-pull   Pull Notion database → TODO.md
    notion-sync   Bidirectional sync TODO.md ↔ Notion
    help          Show help for a command

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
        version="todo-sync 1.3.0"
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
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing TODO.md"
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

    # label
    label_parser = subparsers.add_parser(
        "label",
        help="Set labels for an issue",
        add_help=False
    )
    label_parser.add_argument(
        "issue_id",
        type=int,
        help="Issue number"
    )
    label_parser.add_argument(
        "labels",
        help="Comma-separated list of labels (e.g. 'bug, urgent, feature')"
    )
    label_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    label_parser.add_argument("-h", "--help", action="store_true")

    # labels
    labels_parser = subparsers.add_parser(
        "labels",
        help="List available labels in the repo",
        add_help=False
    )
    labels_parser.add_argument("-h", "--help", action="store_true")

    # list
    list_parser = subparsers.add_parser(
        "list",
        help="List open tickets with numbers",
        add_help=False
    )
    list_parser.add_argument(
        "--all",
        action="store_true",
        help="Also show completed (done) tickets"
    )
    list_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    list_parser.add_argument("-h", "--help", action="store_true")

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

    # notion-setup
    notion_setup_parser = subparsers.add_parser(
        "notion-setup",
        help="Configure Notion integration",
        add_help=False
    )
    notion_setup_parser.add_argument("-h", "--help", action="store_true")

    # notion-push
    notion_push_parser = subparsers.add_parser(
        "notion-push",
        help="Push TODO.md → Notion database",
        add_help=False
    )
    notion_push_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    notion_push_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    notion_push_parser.add_argument("-h", "--help", action="store_true")

    # notion-pull
    notion_pull_parser = subparsers.add_parser(
        "notion-pull",
        help="Pull Notion database → TODO.md",
        add_help=False
    )
    notion_pull_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    notion_pull_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    notion_pull_parser.add_argument("-h", "--help", action="store_true")

    # notion-sync
    notion_sync_parser = subparsers.add_parser(
        "notion-sync",
        help="Bidirectional sync TODO.md ↔ Notion",
        add_help=False
    )
    notion_sync_parser.add_argument(
        "--todo",
        default="TODO.md",
        help="Path to TODO.md (default: TODO.md)"
    )
    notion_sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without making them"
    )
    notion_sync_parser.add_argument("-h", "--help", action="store_true")

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
        "list": cmd_list,
        "comment": cmd_comment,
        "assign": cmd_assign,
        "update": cmd_update,
        "label": cmd_label,
        "labels": cmd_labels,
        "remove": cmd_remove,
        "notion-setup": cmd_notion_setup,
        "notion-push": cmd_notion_push,
        "notion-pull": cmd_notion_pull,
        "notion-sync": cmd_notion_sync,
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
