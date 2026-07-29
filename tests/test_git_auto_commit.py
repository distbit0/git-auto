import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "gitAutoCommit.py"
spec = importlib.util.spec_from_file_location("gitAutoCommit", MODULE_PATH)
git_auto_commit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(git_auto_commit)


def git(repo_path, *args):
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        check=True,
        text=True,
    )


def command_output(*args):
    return subprocess.run(args, capture_output=True, check=True, text=True).stdout


def configure_test_repo(repo_path):
    git(repo_path, "config", "user.email", "test@example.com")
    git(repo_path, "config", "user.name", "Git Auto Commit Test")
    hooks_path = Path(repo_path) / ".git" / "test-hooks"
    hooks_path.mkdir()
    git(repo_path, "config", "core.hooksPath", str(hooks_path))


def run_auto_commit(repo_path, *args, check=True):
    environment = {
        **os.environ,
        "GIT_AUTO_ERROR_INBOX_PATH": "/dev/null",
    }
    return subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "-p",
            str(repo_path),
            *args,
        ],
        capture_output=True,
        check=check,
        env=environment,
        text=True,
    )


@contextmanager
def working_directory(path):
    previous_directory = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous_directory)


class GitAutoCommitTests(unittest.TestCase):
    def test_error_is_appended_to_inbox(self):
        failed_command = subprocess.run(
            ["git", "definitely-not-a-git-command"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(failed_command.returncode, 0)
        failure_message = git_auto_commit.command_failure_message(
            failed_command.args,
            failed_command.returncode,
            failed_command.stdout,
            failed_command.stderr,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            inbox_path = Path(temporary_directory) / "inbox-index.md"
            existing_content = command_output("git", "--version").strip()
            inbox_path.write_text(existing_content, encoding="utf-8")

            with mock.patch.object(git_auto_commit, "ERROR_INBOX_PATH", inbox_path):
                git_auto_commit.append_error_to_inbox(
                    failure_message,
                    "/home/pimania/notes",
                )

            inbox_content = inbox_path.read_text(encoding="utf-8")

        self.assertTrue(inbox_content.startswith(existing_content))
        self.assertIn("git auto-commit error:", inbox_content)
        self.assertIn("repository: /home/pimania/notes", inbox_content)
        for failure_line in failure_message.splitlines():
            self.assertIn(f"    {failure_line}", inbox_content)

    def test_commit_retries_captured_gitguardian_dns_failure(self):
        captured_failure_path = (
            Path(__file__).parent / "fixtures" / "gitguardian_dns_failure.txt"
        )
        captured_failure = captured_failure_path.read_text(encoding="utf-8")
        failed_commit = subprocess.CompletedProcess(
            ["git", "commit", "-m", "inbox-index.md"],
            1,
            stdout="",
            stderr=captured_failure,
        )
        successful_commit = subprocess.CompletedProcess(
            failed_commit.args,
            0,
            stdout="[master captured] inbox-index.md\n",
            stderr="",
        )

        with mock.patch.object(
            git_auto_commit.subprocess,
            "run",
            side_effect=[failed_commit, failed_commit, successful_commit],
        ) as run_command, mock.patch.object(git_auto_commit.time, "sleep") as sleep:
            result = git_auto_commit.commit_with_dns_retry(
                "inbox-index.md",
                "/home/pimania/notes",
                retry_delay_seconds=0,
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(run_command.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_command_failure_message_includes_git_stderr(self):
        result = subprocess.run(
            ["git", "definitely-not-a-git-command"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)

        message = git_auto_commit.command_failure_message(
            result.args,
            result.returncode,
            result.stdout,
            result.stderr,
        )

        self.assertIn("git definitely-not-a-git-command exited with status", message)
        self.assertIn("stderr:", message)
        self.assertIn(result.stderr.strip().splitlines()[0], message)

    def test_captured_github_permission_denial_is_recognized(self):
        captured_failure = (
            Path(__file__).parent / "fixtures" / "github_push_permission_denied.txt"
        ).read_text(encoding="utf-8")
        denied_push = subprocess.CompletedProcess(
            ["git", "push", "--dry-run", "--no-verify", "origin"],
            128,
            stdout="",
            stderr=captured_failure,
        )

        self.assertTrue(git_auto_commit.push_permission_was_denied(denied_push))

    def test_offline_network_remote_leaves_dirty_work_untouched(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_path = Path(temporary_directory)
            git(repo_path, "init", "-b", "master")
            configure_test_repo(repo_path)

            tracked_file = repo_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(repo_path, "add", "tracked.txt")
            git(repo_path, "commit", "-m", "initial")
            git(
                repo_path,
                "remote",
                "add",
                "origin",
                "git@github.com:distbit0/git-auto.git",
            )
            original_head = git(repo_path, "rev-parse", "HEAD").stdout
            tracked_file.write_text(original_head, encoding="utf-8")

            with (
                working_directory(repo_path),
                mock.patch.object(
                    sys,
                    "argv",
                    [str(MODULE_PATH), "-p", str(repo_path)],
                ),
                mock.patch.object(
                    git_auto_commit,
                    "remote_has_internet_connectivity",
                    return_value=False,
                ),
                mock.patch.object(
                    git_auto_commit,
                    "remote_allows_writes",
                ) as check_write_permission,
            ):
                git_auto_commit.main()

            check_write_permission.assert_not_called()
            self.assertEqual(git(repo_path, "rev-parse", "HEAD").stdout, original_head)
            self.assertIn(" M tracked.txt", git(repo_path, "status", "--short").stdout)

    def test_local_remote_does_not_require_networkmanager(self):
        with mock.patch.object(
            git_auto_commit.subprocess,
            "run",
        ) as run_command:
            self.assertTrue(
                git_auto_commit.remote_has_internet_connectivity(
                    "/tmp/repository.git",
                    "/tmp/local",
                )
            )

        run_command.assert_not_called()

    def test_network_remote_is_skipped_without_full_connectivity(self):
        connectivity_command = ["nmcli", "-g", "CONNECTIVITY", "general"]
        disconnected_result = subprocess.CompletedProcess(
            connectivity_command,
            0,
            stdout="none\n",
            stderr="",
        )

        with mock.patch.object(
            git_auto_commit.subprocess,
            "run",
            return_value=disconnected_result,
        ) as run_command:
            self.assertFalse(
                git_auto_commit.remote_has_internet_connectivity(
                    "git@github.com:distbit0/git-auto.git",
                    "/home/pimania/dev/git-auto",
                )
            )

        run_command.assert_called_once_with(
            connectivity_command,
            capture_output=True,
            text=True,
        )

    def test_has_staged_changes_detects_existing_index_work(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_path = Path(temporary_directory)
            git(repo_path, "init", "-b", "master")
            configure_test_repo(repo_path)

            tracked_file = repo_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(repo_path, "add", "tracked.txt")
            git(repo_path, "commit", "-m", "initial")

            with working_directory(repo_path):
                self.assertFalse(git_auto_commit.has_staged_changes())

            tracked_file.write_text(git(repo_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(repo_path, "add", "tracked.txt")

            with working_directory(repo_path):
                self.assertTrue(git_auto_commit.has_staged_changes())

    def test_stale_index_lock_is_removed_after_waiting(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_path = Path(temporary_directory)
            git(repo_path, "init", "-b", "master")

            lock_path = repo_path / ".git" / "index.lock"
            lock_path.write_text("", encoding="utf-8")
            stale_timestamp = 1
            os.utime(lock_path, (stale_timestamp, stale_timestamp))

            with working_directory(repo_path):
                git_auto_commit.wait_for_index_lock(str(repo_path))

            self.assertFalse(lock_path.exists())

    def test_auto_commit_state_marks_owned_staged_changes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_path = Path(temporary_directory)
            git(repo_path, "init", "-b", "master")
            configure_test_repo(repo_path)

            tracked_file = repo_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(repo_path, "add", "tracked.txt")
            git(repo_path, "commit", "-m", "initial")

            with working_directory(repo_path):
                state_path = Path(git_auto_commit.auto_commit_state_path(str(repo_path)))
                self.assertFalse(state_path.exists())
                git_auto_commit.mark_auto_commit_started(str(repo_path))
                self.assertTrue(state_path.exists())
                git_auto_commit.clear_auto_commit_state(str(repo_path))
                self.assertFalse(state_path.exists())

    def test_active_pause_leaves_dirty_work_and_local_commits_unpushed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            pause_path = local_path / ".git" / git_auto_commit.AUTO_COMMIT_PAUSE_FILENAME
            pause_path.touch()
            tracked_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(local_path, "commit", "-am", "protected commit")
            uncommitted_file = local_path / "uncommitted.txt"
            uncommitted_file.write_text(git(local_path, "status", "--short").stdout, encoding="utf-8")

            result = run_auto_commit(local_path)

            self.assertIn("Auto-commit paused", result.stderr)
            self.assertTrue(pause_path.exists())
            self.assertIn("?? uncommitted.txt", git(local_path, "status", "--short").stdout)
            self.assertNotEqual(
                git(local_path, "rev-parse", "HEAD").stdout,
                git(local_path, "rev-parse", "origin/master").stdout,
            )

    def test_expired_pause_resumes_commit_and_push_then_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            pause_path = local_path / ".git" / git_auto_commit.AUTO_COMMIT_PAUSE_FILENAME
            pause_path.touch()
            expired_at = time.time() - git_auto_commit.AUTO_COMMIT_PAUSE_SECONDS - 1
            os.utime(pause_path, (expired_at, expired_at))
            tracked_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")

            result = run_auto_commit(local_path)

            self.assertIn("Auto-commit pause expired", result.stderr)
            self.assertFalse(pause_path.exists())
            self.assertEqual(git(local_path, "status", "--short").stdout, "")
            self.assertEqual(
                git(local_path, "rev-parse", "HEAD").stdout,
                git(local_path, "rev-parse", "origin/master").stdout,
            )

    def test_expired_pause_is_retained_when_push_fails(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            pause_path = local_path / ".git" / git_auto_commit.AUTO_COMMIT_PAUSE_FILENAME
            pause_path.touch()
            expired_at = time.time() - git_auto_commit.AUTO_COMMIT_PAUSE_SECONDS - 1
            os.utime(pause_path, (expired_at, expired_at))
            tracked_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(local_path, "config", "remote.origin.pushurl", str(base_path / "missing.git"))

            result = run_auto_commit(local_path, check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(pause_path.exists())

    def test_upstream_ahead_count_reads_fetched_remote_state(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"
            other_path = base_path / "other"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            git(base_path, "clone", remote_path, other_path)

            for repo_path in (local_path, other_path):
                configure_test_repo(repo_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            git(other_path, "pull", "--ff-only")
            remote_only_file = other_path / "remote-only.txt"
            remote_only_file.write_text(
                git(other_path, "rev-parse", "HEAD").stdout,
                encoding="utf-8",
            )
            git(other_path, "add", "remote-only.txt")
            git(other_path, "commit", "-m", "remote update")
            git(other_path, "push")

            git(local_path, "fetch", "--quiet")
            with working_directory(local_path):
                self.assertEqual(git_auto_commit.upstream_ahead_count("origin/master"), 1)

    def test_pre_existing_staged_changes_are_committed_after_stable_wait(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            tracked_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(local_path, "add", "tracked.txt")

            run_auto_commit(local_path, "--staged-wait-seconds", "0")

            self.assertEqual(git(local_path, "status", "--short").stdout, "")
            self.assertEqual(
                git(local_path, "rev-parse", "HEAD").stdout,
                git(local_path, "rev-parse", "origin/master").stdout,
            )

    def test_clean_synced_repo_skips_push(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")
            git(local_path, "config", "remote.origin.pushurl", str(base_path / "missing.git"))

            result = run_auto_commit(local_path)

            self.assertIn("No changes or local commits to push", result.stderr)

    def test_clean_local_ahead_repo_pushes_existing_commits(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            tracked_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(local_path, "commit", "-am", "local update")

            result = run_auto_commit(local_path)
            git(local_path, "fetch", "--quiet")

            permission_cache_path = (
                local_path
                / ".git"
                / git_auto_commit.REMOTE_PERMISSION_CACHE_FILENAME
            )
            self.assertIn("Verified and cached write permission", result.stderr)
            self.assertTrue(
                permission_cache_path.read_text(encoding="utf-8").endswith(
                    " writable\n"
                )
            )
            self.assertEqual(
                git(local_path, "rev-parse", "HEAD").stdout,
                git(local_path, "rev-parse", "origin/master").stdout,
            )

    def test_cached_read_only_remote_leaves_dirty_work_untouched(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            configure_test_repo(local_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            original_head = git(local_path, "rev-parse", "HEAD").stdout
            tracked_file.write_text(original_head, encoding="utf-8")
            push_url = git(local_path, "remote", "get-url", "--push", "origin").stdout.strip()
            with working_directory(local_path):
                git_auto_commit.cache_remote_write_permission(
                    push_url,
                    str(local_path),
                    False,
                )

            result = run_auto_commit(local_path)

            self.assertIn("cached as read-only", result.stderr)
            self.assertEqual(git(local_path, "rev-parse", "HEAD").stdout, original_head)
            self.assertIn(" M tracked.txt", git(local_path, "status", "--short").stdout)

    def test_remote_update_is_rebased_and_pushed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"
            other_path = base_path / "other"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            git(base_path, "clone", remote_path, other_path)

            for repo_path in (local_path, other_path):
                configure_test_repo(repo_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            git(other_path, "pull", "--ff-only")
            remote_file = other_path / "remote.txt"
            remote_file.write_text(git(other_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(other_path, "add", "remote.txt")
            git(other_path, "commit", "-m", "remote update")
            git(other_path, "push")

            local_file = local_path / "local.txt"
            local_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")

            result = run_auto_commit(local_path)
            git(local_path, "fetch", "--quiet")

            self.assertIn("Fetching and rebasing local commits", result.stderr)
            self.assertEqual(
                git(local_path, "rev-parse", "HEAD").stdout,
                git(local_path, "rev-parse", "origin/master").stdout,
            )

    def test_rebase_conflict_aborts_and_reports_manual_resolution(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            remote_path = base_path / "remote.git"
            local_path = base_path / "local"
            other_path = base_path / "other"

            git(base_path, "init", "--bare", remote_path)
            git(base_path, "clone", remote_path, local_path)
            git(base_path, "clone", remote_path, other_path)

            for repo_path in (local_path, other_path):
                configure_test_repo(repo_path)

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            git(other_path, "pull", "--ff-only")
            other_tracked_file = other_path / "tracked.txt"
            other_tracked_file.write_text(
                git(other_path, "rev-parse", "--show-toplevel").stdout,
                encoding="utf-8",
            )
            git(other_path, "commit", "-am", "remote update")
            git(other_path, "push")

            tracked_file.write_text(
                git(local_path, "rev-parse", "--show-toplevel").stdout,
                encoding="utf-8",
            )

            result = run_auto_commit(local_path, check=False)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("manual resolution required", result.stderr)
            self.assertEqual(git(local_path, "status", "--short").stdout, "")
            self.assertFalse((local_path / ".git" / "rebase-merge").exists())


if __name__ == "__main__":
    unittest.main()
