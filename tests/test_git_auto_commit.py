import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


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


@contextmanager
def working_directory(path):
    previous_directory = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous_directory)


class GitAutoCommitTests(unittest.TestCase):
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

    def test_has_staged_changes_detects_existing_index_work(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repo_path = Path(temporary_directory)
            git(repo_path, "init", "-b", "master")
            git(repo_path, "config", "user.email", "test@example.com")
            git(repo_path, "config", "user.name", "Git Auto Commit Test")

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
            git(repo_path, "config", "user.email", "test@example.com")
            git(repo_path, "config", "user.name", "Git Auto Commit Test")

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
                git(repo_path, "config", "user.email", "test@example.com")
                git(repo_path, "config", "user.name", "Git Auto Commit Test")

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
            git(local_path, "config", "user.email", "test@example.com")
            git(local_path, "config", "user.name", "Git Auto Commit Test")

            tracked_file = local_path / "tracked.txt"
            tracked_file.write_text(command_output("git", "--version"), encoding="utf-8")
            git(local_path, "add", "tracked.txt")
            git(local_path, "commit", "-m", "initial")
            git(local_path, "push", "-u", "origin", "master")

            tracked_file.write_text(git(local_path, "rev-parse", "HEAD").stdout, encoding="utf-8")
            git(local_path, "add", "tracked.txt")

            subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "-p",
                    str(local_path),
                    "--staged-wait-seconds",
                    "0",
                ],
                capture_output=True,
                check=True,
                text=True,
            )

            self.assertEqual(git(local_path, "status", "--short").stdout, "")
            self.assertEqual(
                git(local_path, "rev-parse", "HEAD").stdout,
                git(local_path, "rev-parse", "origin/master").stdout,
            )


if __name__ == "__main__":
    unittest.main()
