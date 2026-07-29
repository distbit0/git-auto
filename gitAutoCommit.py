import subprocess
import re
import argparse
import fcntl
import hashlib
import os
import shlex
import time
import sys
from os import path
from pathlib import Path

from loguru import logger

INDEX_LOCK_STALE_SECONDS = 60
INDEX_LOCK_POLL_SECONDS = 10
STAGED_TAKEOVER_WAIT_SECONDS = 30
STAGED_TAKEOVER_POLL_SECONDS = 5
AUTO_COMMIT_LOCK_FILENAME = "git_auto_commit.lock"
AUTO_COMMIT_STATE_FILENAME = "git_auto_commit.pending"
AUTO_COMMIT_PAUSE_FILENAME = "git_auto_commit.pause"
REMOTE_PERMISSION_CACHE_FILENAME = "git_auto_commit.remote_write"
AUTO_COMMIT_PAUSE_SECONDS = 14 * 24 * 60 * 60
PUSH_RECONCILE_ATTEMPTS = 2
COMMIT_ATTEMPTS = 3
COMMIT_RETRY_DELAY_SECONDS = 2
NETWORKMANAGER_CONNECTED_STATE = "full"
ERROR_INBOX_PATH = Path(
    os.environ.get(
        "GIT_AUTO_ERROR_INBOX_PATH",
        Path.home() / "notes/inbox-index.md",
    )
)
def getAbsPathFromScript(relPath):
    basepath = path.dirname(__file__)
    fullPath = path.abspath(path.join(basepath, relPath))

    return fullPath


logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss,SSS} - {level} - {message}",
)
logger.add(
    getAbsPathFromScript("git_auto_commit.log"),
    level="INFO",
    rotation="10 MB",
    retention=5,
    format="{time:YYYY-MM-DD HH:mm:ss,SSS} - {level} - {message}",
)


def getAbsPathFromPWD(relPath):
    # base path is pwd
    basepath = os.getcwd()
    fullPath = os.path.abspath(os.path.join(basepath, relPath))

    return fullPath


def git_dir_path(repoAbsPath):
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        capture_output=True,
        text=True,
        check=True,
    )
    git_dir = result.stdout.strip()
    if os.path.isabs(git_dir):
        return git_dir
    return os.path.abspath(os.path.join(repoAbsPath, git_dir))


def generate_commit_message():
    result = subprocess.run(
        ["git", "diff", "--name-only", "--cached"], capture_output=True, text=True
    )
    commit_message = result.stdout.strip()
    commit_message = "\n".join(
        [
            re.sub(r"^.*/", "", line)
            for line in commit_message.split("\n")
            if not re.search(r"(^\.)|(\/\.)", line)
        ]
    )
    # test
    if not commit_message:
        commit_message = "Commit involves changes in hidden files or directories only"

    return commit_message


def append_error_to_inbox(message, repoAbsPath):
    indented_message = "\n".join(f"    {line}" for line in message.splitlines())
    entry = (
        "\n\ngit auto-commit error:\n"
        f"repository: {repoAbsPath}\n\n"
        f"{indented_message}\n"
    )

    try:
        with ERROR_INBOX_PATH.open("a", encoding="utf-8") as inbox_file:
            inbox_file.write(entry)
    except OSError as exc:
        logger.warning(f"Failed to append auto-commit error to {ERROR_INBOX_PATH}: {exc}")


def notify_error(message, repoAbsPath):
    append_error_to_inbox(message, repoAbsPath)

    subprocess.run(
        [
            "notify-send",
            "Git AutoCommit Error",
            f"{message}\nRepository: {repoAbsPath}",
            "--urgency=critical",
            "--icon=dialog-error",
        ],
        check=False,
    )


def exit_with_error(message, repoAbsPath):
    logger.error(f"{message} in repo {repoAbsPath}.")
    notify_error(message, repoAbsPath)
    sys.exit(1)


def command_display(command):
    return " ".join(shlex.quote(str(part)) for part in command)


def command_failure_message(command, returncode, stdout="", stderr=""):
    message_parts = [f"{command_display(command)} exited with status {returncode}"]
    for stream_name, stream_output in (("stdout", stdout), ("stderr", stderr)):
        stream_output = stream_output.strip()
        if stream_output:
            message_parts.append(f"{stream_name}:\n{stream_output}")
    return "\n".join(message_parts)


def called_process_error_message(error):
    return command_failure_message(
        error.cmd,
        error.returncode,
        error.stdout or "",
        error.stderr or "",
    )


def run_checked(command, failure_message, repoAbsPath):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        failure_details = command_failure_message(
            command, result.returncode, result.stdout, result.stderr
        )
        exit_with_error(
            f"{failure_message}: {failure_details}",
            repoAbsPath,
        )
    return result


def is_gitguardian_dns_failure(result):
    output = f"{result.stdout}\n{result.stderr}"
    return "Failed to connect to GitGuardian server" in output and (
        "NameResolutionError" in output
        or "Temporary failure in name resolution" in output
    )


def commit_with_dns_retry(
    commit_message,
    repoAbsPath,
    attempts=COMMIT_ATTEMPTS,
    retry_delay_seconds=COMMIT_RETRY_DELAY_SECONDS,
):
    command = ["git", "commit", "-m", commit_message]
    for attempt in range(1, attempts + 1):
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return result
        if not is_gitguardian_dns_failure(result) or attempt == attempts:
            failure_details = command_failure_message(
                command, result.returncode, result.stdout, result.stderr
            )
            exit_with_error(f"Commit failed: {failure_details}", repoAbsPath)

        logger.warning(
            "GitGuardian DNS resolution failed during commit "
            f"(attempt {attempt}/{attempts}); retrying in {retry_delay_seconds:g}s."
        )
        time.sleep(retry_delay_seconds)

    raise AssertionError("Commit retry loop exited unexpectedly")


def acquire_auto_commit_lock(repoAbsPath):
    lock_file = open(
        os.path.join(git_dir_path(repoAbsPath), AUTO_COMMIT_LOCK_FILENAME),
        "w",
        encoding="utf-8",
    )
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.warning(
            f"Another git auto-commit is running in repo {repoAbsPath}. Waiting."
        )
        fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def auto_commit_state_path(repoAbsPath):
    return os.path.join(git_dir_path(repoAbsPath), AUTO_COMMIT_STATE_FILENAME)


def auto_commit_pause_path(repoAbsPath):
    return os.path.join(git_dir_path(repoAbsPath), AUTO_COMMIT_PAUSE_FILENAME)


def auto_commit_pause_remaining_seconds(repoAbsPath):
    try:
        created_at = os.path.getmtime(auto_commit_pause_path(repoAbsPath))
    except FileNotFoundError:
        return None
    return max(0, AUTO_COMMIT_PAUSE_SECONDS - (time.time() - created_at))


def clear_auto_commit_pause(repoAbsPath):
    try:
        os.remove(auto_commit_pause_path(repoAbsPath))
    except FileNotFoundError:
        return
    logger.info(f"Removed expired auto-commit pause in repo {repoAbsPath}.")


def mark_auto_commit_started(repoAbsPath):
    state_path = auto_commit_state_path(repoAbsPath)
    with open(state_path, "w", encoding="utf-8") as state_file:
        state_file.write(f"pid={os.getpid()}\nstarted_at={time.time()}\n")


def clear_auto_commit_state(repoAbsPath):
    try:
        os.remove(auto_commit_state_path(repoAbsPath))
    except FileNotFoundError:
        pass


def has_staged_changes():
    result = subprocess.run(["git", "diff", "--cached", "--quiet", "--"])
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    raise subprocess.CalledProcessError(result.returncode, result.args)


def staged_diff_snapshot():
    result = subprocess.run(
        ["git", "diff", "--cached", "--binary"],
        capture_output=True,
        check=True,
    )
    return result.stdout


def process_is_git_in_repo(pid, repoAbsPath):
    if pid == os.getpid():
        return False

    proc_path = f"/proc/{pid}"
    try:
        with open(os.path.join(proc_path, "cmdline"), "rb") as cmdline_file:
            command_parts = [
                part.decode(errors="replace")
                for part in cmdline_file.read().split(b"\0")
                if part
            ]
        process_cwd = os.path.realpath(os.readlink(os.path.join(proc_path, "cwd")))
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False

    if not command_parts:
        return False

    executable_name = os.path.basename(command_parts[0])
    if executable_name != "git":
        return False

    repo_real_path = os.path.realpath(repoAbsPath)
    git_real_path = os.path.realpath(git_dir_path(repoAbsPath))
    return process_cwd == repo_real_path or process_cwd.startswith(
        repo_real_path + os.sep
    ) or process_cwd == git_real_path or process_cwd.startswith(git_real_path + os.sep)


def git_processes_in_repo(repoAbsPath):
    pids = []
    for pid_name in os.listdir("/proc"):
        if not pid_name.isdigit():
            continue
        pid = int(pid_name)
        if process_is_git_in_repo(pid, repoAbsPath):
            pids.append(pid)
    return pids


def upstream_name():
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def push_remote_name(repoAbsPath):
    push_ref_result = subprocess.run(
        [
            "git",
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{push}",
        ],
        capture_output=True,
        text=True,
    )
    if push_ref_result.returncode == 0:
        return push_ref_result.stdout.strip().split("/", 1)[0]

    push_default_result = subprocess.run(
        ["git", "config", "--get", "remote.pushDefault"],
        capture_output=True,
        text=True,
    )
    return push_default_result.stdout.strip() or "origin"


def remote_push_url(remote_name, repoAbsPath):
    if remote_name == ".":
        return repoAbsPath
    return run_checked(
        ["git", "remote", "get-url", "--push", remote_name],
        f"Could not resolve push URL for remote {remote_name}",
        repoAbsPath,
    ).stdout.strip()


def remote_requires_internet(push_url):
    return not (
        push_url.startswith(("file://", "/", "./", "../")) or ":" not in push_url
    )


def remote_has_internet_connectivity(push_url, repoAbsPath):
    if not remote_requires_internet(push_url):
        return True

    command = ["nmcli", "-g", "CONNECTIVITY", "general"]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as error:
        logger.warning(
            "Skipping network Git operations because NetworkManager connectivity "
            f"could not be checked: {error} in repo {repoAbsPath}."
        )
        return False

    connectivity_state = result.stdout.strip()
    if result.returncode != 0 or not connectivity_state:
        logger.warning(
            "Skipping network Git operations because NetworkManager connectivity "
            f"could not be determined: {command_failure_message(command, result.returncode, result.stdout, result.stderr)} "
            f"in repo {repoAbsPath}."
        )
        return False
    if connectivity_state != NETWORKMANAGER_CONNECTED_STATE:
        logger.info(
            "Skipping network Git operations because NetworkManager reports "
            f"{connectivity_state} internet connectivity in repo {repoAbsPath}."
        )
        return False
    return True


def remote_permission_cache_path(repoAbsPath):
    return Path(git_dir_path(repoAbsPath)) / REMOTE_PERMISSION_CACHE_FILENAME


def cached_remote_write_permission(push_url, repoAbsPath):
    cache_path = remote_permission_cache_path(repoAbsPath)
    try:
        cached_url_hash, cached_permission = cache_path.read_text(
            encoding="utf-8"
        ).split()
    except FileNotFoundError:
        return None
    except ValueError as error:
        raise OSError(f"Invalid remote permission cache entry: {cache_path}") from error

    if cached_url_hash != hashlib.sha256(push_url.encode()).hexdigest():
        return None
    if cached_permission == "writable":
        return True
    if cached_permission == "read-only":
        return False
    raise OSError(f"Invalid remote permission cache entry: {cache_path}")


def cache_remote_write_permission(push_url, repoAbsPath, writable):
    cache_path = remote_permission_cache_path(repoAbsPath)
    push_url_hash = hashlib.sha256(push_url.encode()).hexdigest()
    cache_path.write_text(
        f"{push_url_hash} {'writable' if writable else 'read-only'}\n",
        encoding="utf-8",
    )


def push_permission_was_denied(result):
    output = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        denial_message in output
        for denial_message in (
            "write access to repository not granted",
            "you are not allowed to push code to this project",
            "you are not allowed to upload code",
            "permission denied for writing",
        )
    ) or ("permission to " in output and " denied to " in output)


def remote_allows_writes(remote_name, push_url, repoAbsPath):
    try:
        cached_permission = cached_remote_write_permission(push_url, repoAbsPath)
    except OSError as error:
        exit_with_error(f"Could not read remote permission cache: {error}", repoAbsPath)

    if cached_permission is not None:
        if not cached_permission:
            logger.info(
                f"Skipping auto-commit and push because remote {remote_name} is cached as read-only."
            )
        return cached_permission

    probe_ref = (
        "refs/heads/git-auto-permission-check/"
        f"{hashlib.sha256(push_url.encode()).hexdigest()[:12]}"
    )
    result = subprocess.run(
        [
            "git",
            "push",
            "--dry-run",
            "--no-verify",
            remote_name,
            f"HEAD:{probe_ref}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        writable = True
    elif push_permission_was_denied(result):
        writable = False
    else:
        exit_with_error(
            f"Could not verify write permission for remote {remote_name}: {push_failure_message(result)}",
            repoAbsPath,
        )

    try:
        cache_remote_write_permission(push_url, repoAbsPath, writable)
    except OSError as error:
        exit_with_error(f"Could not update remote permission cache: {error}", repoAbsPath)

    if writable:
        logger.info(f"Verified and cached write permission for remote {remote_name}.")
    else:
        logger.info(
            f"Remote {remote_name} denied write permission; cached as read-only and skipping auto-commit and push."
        )
    return writable


def upstream_ahead_count(upstream):
    _local_ahead, upstream_ahead = branch_divergence(upstream)
    return upstream_ahead


def branch_divergence(upstream):
    result = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"],
        capture_output=True,
        text=True,
        check=True,
    )
    local_ahead, upstream_ahead = result.stdout.split()
    return int(local_ahead), int(upstream_ahead)


def has_local_commits_to_push(repoAbsPath):
    upstream = upstream_name()
    if not upstream:
        return True

    try:
        local_ahead, _upstream_ahead = branch_divergence(upstream)
    except subprocess.CalledProcessError as e:
        exit_with_error(
            f"Could not inspect local branch state: {called_process_error_message(e)}",
            repoAbsPath,
        )
    return local_ahead > 0


def push_was_rejected_for_remote_updates(result):
    output = f"{result.stdout}\n{result.stderr}".lower()
    return (
        "[rejected]" in output
        and (
            "fetch first" in output
            or "non-fast-forward" in output
            or "stale info" in output
        )
    )


def push_failure_message(result):
    return command_failure_message(
        result.args, result.returncode, result.stdout, result.stderr
    )


def abort_rebase_details():
    result = subprocess.run(
        ["git", "rebase", "--abort"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return "Rebase was aborted; local commits were left unapplied to the fetched upstream."
    return "Could not abort failed rebase:\n" + push_failure_message(result)


def rebase_onto_upstream(repoAbsPath, upstream):
    result = subprocess.run(
        ["git", "rebase", upstream],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        logger.info(f"Rebased local commits onto {upstream} in repo {repoAbsPath}.")
        return

    rebase_failure = push_failure_message(result)
    abort_details = abort_rebase_details()
    exit_with_error(
        f"Could not automatically rebase onto {upstream}; manual resolution required.\n{rebase_failure}\n{abort_details}",
        repoAbsPath,
    )


def reconcile_remote_updates(repoAbsPath, push_result):
    upstream = upstream_name()
    if not upstream:
        exit_with_error(
            f"Push failed and no upstream branch is configured: {push_failure_message(push_result)}",
            repoAbsPath,
        )

    logger.warning(
        f"Push was rejected because {upstream} changed. Fetching and rebasing local commits."
    )
    run_checked(
        ["git", "fetch", "--quiet"],
        "Could not fetch upstream state after push rejection",
        repoAbsPath,
    )

    try:
        local_ahead, upstream_ahead = branch_divergence(upstream)
    except subprocess.CalledProcessError as e:
        exit_with_error(
            f"Could not inspect fetched upstream state: {called_process_error_message(e)}",
            repoAbsPath,
        )

    if upstream_ahead == 0:
        exit_with_error(
            f"Push was rejected, but fetched {upstream} is not ahead: {push_failure_message(push_result)}",
            repoAbsPath,
        )
    if local_ahead == 0:
        logger.info(
            f"Fetched {upstream}; no local commits remain to push in repo {repoAbsPath}."
        )
        return

    rebase_onto_upstream(repoAbsPath, upstream)


def push_with_auto_reconcile(repoAbsPath, remote_name, push_url):
    for reconcile_attempt in range(PUSH_RECONCILE_ATTEMPTS + 1):
        result = subprocess.run(["git", "push"], capture_output=True, text=True)
        if result.returncode == 0:
            logger.info(f"Push successful in repo {repoAbsPath}.")
            return True

        if push_permission_was_denied(result):
            try:
                cache_remote_write_permission(push_url, repoAbsPath, False)
            except OSError as error:
                exit_with_error(
                    f"Push permission was denied and the remote permission cache could not be updated: {error}",
                    repoAbsPath,
                )
            logger.info(
                f"Remote {remote_name} denied write permission; cached as read-only and leaving local commits unpushed."
            )
            return False

        if not push_was_rejected_for_remote_updates(result):
            exit_with_error(f"Push failed: {push_failure_message(result)}", repoAbsPath)

        if reconcile_attempt == PUSH_RECONCILE_ATTEMPTS:
            exit_with_error(
                f"Push was repeatedly rejected after automatic reconciliation: {push_failure_message(result)}",
                repoAbsPath,
            )

        reconcile_remote_updates(repoAbsPath, result)

    raise AssertionError("Push reconciliation loop exited unexpectedly")


def working_tree_has_changes(repoAbsPath):
    result = run_checked(
        ["git", "status", "--porcelain"],
        "Could not inspect working tree",
        repoAbsPath,
    )
    return bool(result.stdout)


def wait_for_index_lock(repoAbsPath):
    lock_file_path = os.path.join(git_dir_path(repoAbsPath), "index.lock")

    while os.path.exists(lock_file_path):
        active_git_pids = git_processes_in_repo(repoAbsPath)
        if active_git_pids:
            logger.warning(
                f"{lock_file_path} exists and git process(es) {active_git_pids} are active in repo {repoAbsPath}. Waiting."
            )
            time.sleep(INDEX_LOCK_POLL_SECONDS)
            continue

        lock_age_seconds = time.time() - os.path.getmtime(lock_file_path)
        if lock_age_seconds < INDEX_LOCK_STALE_SECONDS:
            wait_seconds = min(
                INDEX_LOCK_POLL_SECONDS, INDEX_LOCK_STALE_SECONDS - lock_age_seconds
            )
            logger.warning(
                f"{lock_file_path} exists in repo {repoAbsPath}. Waiting before treating it as stale."
            )
            time.sleep(wait_seconds)
            continue

        logger.warning(f"Removing stale git index lock {lock_file_path}.")
        try:
            os.remove(lock_file_path)
        except FileNotFoundError:
            return


def wait_for_staged_changes_to_settle(repoAbsPath, wait_seconds, poll_seconds):
    logger.warning(
        f"Pre-existing staged changes found in repo {repoAbsPath}. Waiting up to {wait_seconds:g}s before auto-committing them."
    )
    previous_snapshot = staged_diff_snapshot()
    deadline = time.monotonic() + wait_seconds

    while time.monotonic() < deadline:
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            break
        time.sleep(min(max(poll_seconds, 0.1), remaining_seconds))
        wait_for_index_lock(repoAbsPath)

        if not has_staged_changes():
            logger.info(
                f"Pre-existing staged changes were cleared while waiting in repo {repoAbsPath}."
            )
            return

        current_snapshot = staged_diff_snapshot()
        if current_snapshot == previous_snapshot:
            continue

        logger.warning(
            f"Staged changes changed while waiting in repo {repoAbsPath}. Restarting staged-change wait."
        )
        previous_snapshot = current_snapshot
        deadline = time.monotonic() + wait_seconds

    logger.warning(
        f"Taking over stable pre-existing staged changes in repo {repoAbsPath}."
    )


def claim_staging_window(repoAbsPath, wait_seconds, poll_seconds):
    if has_staged_changes():
        if os.path.exists(auto_commit_state_path(repoAbsPath)):
            logger.warning(
                f"Resuming staged changes from an earlier git auto-commit in repo {repoAbsPath}."
            )
        else:
            wait_for_staged_changes_to_settle(repoAbsPath, wait_seconds, poll_seconds)
    else:
        clear_auto_commit_state(repoAbsPath)

    mark_auto_commit_started(repoAbsPath)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "message", help="Custom commit message", nargs="?", default=None
    )
    parser.add_argument(
        "-p", "--path", help="Path to apply the git operations to", default="."
    )
    parser.add_argument(
        "--staged-wait-seconds",
        type=float,
        default=STAGED_TAKEOVER_WAIT_SECONDS,
        help="Seconds a pre-existing staged diff must stay stable before auto-commit takes it over",
    )
    parser.add_argument(
        "--staged-poll-seconds",
        type=float,
        default=STAGED_TAKEOVER_POLL_SECONDS,
        help="Seconds between staged-diff stability checks",
    )
    args = parser.parse_args()

    repoAbsPath = getAbsPathFromPWD(args.path)

    os.chdir(repoAbsPath)

    auto_commit_lock = acquire_auto_commit_lock(repoAbsPath)
    wait_for_index_lock(repoAbsPath)

    try:
        pause_remaining_seconds = auto_commit_pause_remaining_seconds(repoAbsPath)
    except OSError as error:
        exit_with_error(f"Could not inspect auto-commit pause: {error}", repoAbsPath)

    if pause_remaining_seconds is not None and pause_remaining_seconds > 0:
        logger.info(
            "Auto-commit paused for "
            f"{pause_remaining_seconds / (24 * 60 * 60):.2f} more days in repo {repoAbsPath}."
        )
        auto_commit_lock.close()
        return

    pause_expired = pause_remaining_seconds == 0
    if pause_expired:
        logger.info(f"Auto-commit pause expired in repo {repoAbsPath}; resuming.")

    if not working_tree_has_changes(repoAbsPath) and not has_local_commits_to_push(
        repoAbsPath
    ):
        logger.info(f"No changes or local commits to push in repo {repoAbsPath}.")
        if pause_expired:
            clear_auto_commit_pause(repoAbsPath)
        auto_commit_lock.close()
        return

    remote_name = push_remote_name(repoAbsPath)
    push_url = remote_push_url(remote_name, repoAbsPath)
    if not remote_has_internet_connectivity(push_url, repoAbsPath):
        auto_commit_lock.close()
        return
    if not remote_allows_writes(remote_name, push_url, repoAbsPath):
        if pause_expired:
            clear_auto_commit_pause(repoAbsPath)
        auto_commit_lock.close()
        return

    try:
        claim_staging_window(
            repoAbsPath, args.staged_wait_seconds, args.staged_poll_seconds
        )
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Could not inspect staged changes: {e}", repoAbsPath)

    run_checked(["git", "add", "."], "Git add failed", repoAbsPath)

    try:
        has_changes_to_commit = has_staged_changes()
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Could not inspect staged changes: {e}", repoAbsPath)

    if has_changes_to_commit:
        custom_message = args.message if args.message else generate_commit_message()
        commit_with_dns_retry(custom_message, repoAbsPath)
        clear_auto_commit_state(repoAbsPath)
        logger.info(f"Commit successful in repo {repoAbsPath}. Pushing to remote.")
    else:
        clear_auto_commit_state(repoAbsPath)
        if not has_local_commits_to_push(repoAbsPath):
            logger.info(f"No changes or local commits to push in repo {repoAbsPath}.")
            if pause_expired:
                clear_auto_commit_pause(repoAbsPath)
            auto_commit_lock.close()
            return
        logger.info(f"No changes to commit in repo {repoAbsPath}. Pushing local commits.")

    if not remote_has_internet_connectivity(push_url, repoAbsPath):
        auto_commit_lock.close()
        return
    push_with_auto_reconcile(repoAbsPath, remote_name, push_url)

    if pause_expired:
        clear_auto_commit_pause(repoAbsPath)

    auto_commit_lock.close()


if __name__ == "__main__":
    main()
