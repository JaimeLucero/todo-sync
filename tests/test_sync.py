"""Unit tests for sync.py."""

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Import from sync.py
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from sync import TodoItem, TodoParser, GitHubError, GitHubClient, SyncEngine


class TestTodoParser:
    """Tests for TodoParser."""

    def test_parse_unchecked_item_no_issue(self):
        """Parse an unchecked item without an issue ID."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [ ] Write tests\n")
            f.flush()
            path = f.name

        try:
            parser = TodoParser(path)
            items = parser.load()

            assert len(items) == 1
            item = items[0]
            assert item.text == "Write tests"
            assert item.checked is False
            assert item.issue_id is None
            assert item.section == "open"
        finally:
            Path(path).unlink()

    def test_parse_checked_item_with_issue(self):
        """Parse a checked item with an issue ID."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Done\n- [x] Fix login bug <!-- issue:42 -->\n")
            f.flush()
            path = f.name

        try:
            parser = TodoParser(path)
            items = parser.load()

            assert len(items) == 1
            item = items[0]
            assert item.text == "Fix login bug"
            assert item.checked is True
            assert item.issue_id == 42
            assert item.section == "done"
        finally:
            Path(path).unlink()

    def test_parse_preserves_non_checkbox_lines(self):
        """Non-checkbox lines should be preserved."""
        content = """# TODO

## Open
Some prose here
- [ ] Task 1
More prose
- [ ] Task 2

## Done
- [x] Old task
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            parser = TodoParser(path)
            items = parser.load()

            # Check we parsed the items
            assert len(items) == 3
            assert items[0].text == "Task 1"
            assert items[1].text == "Task 2"
            assert items[2].text == "Old task"

            # Write back should preserve all non-checkbox lines
            parser.write_back(items)

            with open(path, 'r') as f:
                result = f.read()

            assert "Some prose here" in result
            assert "More prose" in result
            assert "# TODO" in result
            assert "## Open" in result
            assert "## Done" in result
        finally:
            Path(path).unlink()

    def test_write_back_adds_issue_tag(self):
        """Writing back should add issue tags."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [ ] Task 1\n")
            f.flush()
            path = f.name

        try:
            parser = TodoParser(path)
            items = parser.load()

            # Add an issue ID
            items[0].issue_id = 7
            parser.write_back(items)

            with open(path, 'r') as f:
                content = f.read()

            assert "<!-- issue:7 -->" in content
            assert "- [ ] Task 1 <!-- issue:7 -->" in content
        finally:
            Path(path).unlink()

    def test_write_back_updates_checkbox(self):
        """Writing back should update checkbox state."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [ ] Task 1 <!-- issue:5 -->\n")
            f.flush()
            path = f.name

        try:
            parser = TodoParser(path)
            items = parser.load()

            # Check it off
            items[0].checked = True
            parser.write_back(items)

            with open(path, 'r') as f:
                content = f.read()

            assert "- [x] Task 1 <!-- issue:5 -->" in content
        finally:
            Path(path).unlink()

    def test_append_item_to_open_section(self):
        """Append should insert under the correct section."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [ ] Task 1\n\n## Done\n- [x] Old task\n")
            f.flush()
            path = f.name

        try:
            parser = TodoParser(path)
            parser.load()  # Parse first to populate _section_lines
            parser.append_item("open", "New task", 99)

            with open(path, 'r') as f:
                content = f.read()

            lines = content.split('\n')
            # Find the new task
            new_task_idx = next(i for i, line in enumerate(lines) if "New task" in line)
            # Find the Done section
            done_idx = next(i for i, line in enumerate(lines) if "## Done" in line)

            # New task should come before Done section
            assert new_task_idx < done_idx
            assert "- [ ] New task <!-- issue:99 -->" in content
        finally:
            Path(path).unlink()

    def test_file_not_found(self):
        """Load should raise FileNotFoundError if file doesn't exist."""
        parser = TodoParser("/nonexistent/path/TODO.md")
        with pytest.raises(FileNotFoundError):
            parser.load()


class TestGitHubClient:
    """Tests for GitHubClient (mocked)."""

    @mock.patch('subprocess.run')
    def test_detect_repo(self, mock_run):
        """detect_repo should parse the nameWithOwner output."""
        mock_run.return_value = mock.Mock(
            stdout="owner/repo\n",
            stderr=""
        )

        client = GitHubClient()
        assert client.repo == "owner/repo"

    @mock.patch('subprocess.run')
    def test_fetch_all_issues(self, mock_run):
        """fetch_all_issues should parse gh issue list output."""
        output = "123\tFix bug\topen\n456\tAdd feature\tclosed\n"
        mock_run.return_value = mock.Mock(
            stdout=output,
            stderr=""
        )

        client = GitHubClient()
        with mock.patch.object(client, '_detect_repo', return_value="owner/repo"):
            issues = client.fetch_all_issues()

        assert len(issues) == 2
        assert issues[0].number == 123
        assert issues[0].title == "Fix bug"
        assert issues[0].state == "open"
        assert issues[1].number == 456
        assert issues[1].state == "closed"

    @mock.patch('subprocess.run')
    def test_create_issue(self, mock_run):
        """create_issue should parse the URL and extract issue number."""
        mock_run.return_value = mock.Mock(
            stdout="https://github.com/owner/repo/issues/789\n",
            stderr=""
        )

        client = GitHubClient()
        with mock.patch.object(client, '_detect_repo', return_value="owner/repo"):
            issue = client.create_issue("Test issue")

        assert issue.number == 789
        assert issue.title == "Test issue"
        assert issue.state == "open"

    @mock.patch('subprocess.run')
    def test_close_issue(self, mock_run):
        """close_issue should call gh issue close."""
        mock_run.return_value = mock.Mock(stdout="", stderr="")

        client = GitHubClient()
        with mock.patch.object(client, '_detect_repo', return_value="owner/repo"):
            client.close_issue(42)

        # Verify the command was called
        calls = [c for c in mock_run.call_args_list if 'close' in str(c)]
        assert len(calls) > 0

    @mock.patch('subprocess.run')
    def test_reopen_issue(self, mock_run):
        """reopen_issue should call gh issue reopen."""
        mock_run.return_value = mock.Mock(stdout="", stderr="")

        client = GitHubClient()
        with mock.patch.object(client, '_detect_repo', return_value="owner/repo"):
            client.reopen_issue(42)

        # Verify the command was called
        calls = [c for c in mock_run.call_args_list if 'reopen' in str(c)]
        assert len(calls) > 0


class TestInitCommand:
    """Tests for init command."""

    def test_init_creates_todo_md(self, tmp_path, monkeypatch):
        """init should create TODO.md in current directory."""
        monkeypatch.chdir(tmp_path)

        import subprocess as sp
        result = sp.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "sync.py"), "init"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "Created TODO.md" in result.stdout
        assert (tmp_path / "TODO.md").exists()

        content = (tmp_path / "TODO.md").read_text()
        assert "## Open" in content
        assert "## Done" in content

    def test_init_skips_existing_todo_md(self, tmp_path, monkeypatch):
        """init should skip if TODO.md already exists."""
        monkeypatch.chdir(tmp_path)

        # Create TODO.md with specific content
        (tmp_path / "TODO.md").write_text("# Custom TODO\n\n## Tasks\n")

        import subprocess as sp
        result = sp.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "sync.py"), "init"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "already exists" in result.stdout

        # Content should be unchanged
        content = (tmp_path / "TODO.md").read_text()
        assert content == "# Custom TODO\n\n## Tasks\n"

    def test_init_with_makefile_injects_targets(self, tmp_path, monkeypatch):
        """init --with-makefile should inject Makefile targets."""
        monkeypatch.chdir(tmp_path)

        import subprocess as sp
        result = sp.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "sync.py"),
             "init", "--with-makefile"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert "Appended Makefile targets" in result.stdout

        makefile = tmp_path / "Makefile"
        assert makefile.exists()

        content = makefile.read_text()
        assert "# todo-sync-targets" in content
        assert "todo-sync" in content
        assert "todo-pull" in content
        assert "todo-push" in content

    def test_init_makefile_idempotent(self, tmp_path, monkeypatch):
        """Running init --with-makefile twice should not duplicate targets."""
        monkeypatch.chdir(tmp_path)

        import subprocess as sp

        # First run
        result1 = sp.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "sync.py"),
             "init", "--with-makefile"],
            capture_output=True,
            text=True
        )
        assert result1.returncode == 0

        # Second run
        result2 = sp.run(
            [sys.executable, str(Path(__file__).parent.parent / "scripts" / "sync.py"),
             "init", "--with-makefile"],
            capture_output=True,
            text=True
        )
        assert result2.returncode == 0
        assert "already present" in result2.stdout

        # Check Makefile only has one guard
        makefile = tmp_path / "Makefile"
        content = makefile.read_text()
        guard_count = content.count("# todo-sync-targets")
        assert guard_count == 1


class TestSyncEngine:
    """Tests for SyncEngine."""

    def test_sync_push_creates_issue_for_unlinked_item(self):
        """Push sync should create GitHub issues for items without issue_id."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [ ] Fix login\n\n## Done\n")
            f.flush()
            path = f.name

        try:
            # Mock GitHub client
            mock_github = mock.Mock()
            mock_github.fetch_all_issues.return_value = []
            mock_github.create_issue.return_value = mock.Mock(
                number=100,
                title="Fix login",
                state="open"
            )

            engine = SyncEngine(path, mock_github)
            engine.sync_push_only()

            # Verify create_issue was called
            mock_github.create_issue.assert_called_once_with("Fix login")

            # Verify the file was updated with the issue ID
            with open(path, 'r') as f:
                content = f.read()
            assert "<!-- issue:100 -->" in content
        finally:
            Path(path).unlink()

    def test_sync_push_closes_open_issue(self):
        """Push sync should close issues when the item is checked."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [x] Fix login <!-- issue:50 -->\n\n## Done\n")
            f.flush()
            path = f.name

        try:
            mock_github = mock.Mock()
            mock_github.fetch_all_issues.return_value = [
                mock.Mock(number=50, title="Fix login", state="open")
            ]

            engine = SyncEngine(path, mock_github)
            engine.sync_push_only()

            # Verify close_issue was called
            mock_github.close_issue.assert_called_once_with(50)
        finally:
            Path(path).unlink()

    def test_sync_pull_appends_new_issue(self):
        """Pull sync should add new GitHub issues to TODO.md."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n\n## Done\n")
            f.flush()
            path = f.name

        try:
            mock_github = mock.Mock()
            mock_github.fetch_all_issues.return_value = [
                mock.Mock(number=77, title="New issue", state="open")
            ]

            engine = SyncEngine(path, mock_github)
            engine.sync_pull_only()

            # Verify the issue was appended
            with open(path, 'r') as f:
                content = f.read()
            assert "New issue" in content
            assert "<!-- issue:77 -->" in content
        finally:
            Path(path).unlink()

    def test_sync_dry_run_makes_no_changes(self):
        """Dry run should not make any API calls or file changes."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# TODO\n\n## Open\n- [ ] Task 1\n\n## Done\n")
            f.flush()
            path = f.name

        try:
            original_content = Path(path).read_text()

            mock_github = mock.Mock()
            mock_github.fetch_all_issues.return_value = []

            engine = SyncEngine(path, mock_github, dry_run=True)
            engine.sync_push_only()

            # File should be unchanged
            assert Path(path).read_text() == original_content

            # No API calls should have been made
            mock_github.create_issue.assert_not_called()
            mock_github.close_issue.assert_not_called()
        finally:
            Path(path).unlink()
