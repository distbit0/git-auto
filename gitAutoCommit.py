import subprocess
import re
import argparse
import os
import time
import sys
from os import path

from loguru import logger


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


def notify_error(message, repoAbsPath):
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


def has_staged_changes():
    result = subprocess.run(["git", "diff", "--cached", "--quiet", "--"])
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    raise subprocess.CalledProcessError(result.returncode, result.args)


def upstream_name():
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def upstream_ahead_count(upstream):
    result = subprocess.run(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"],
        capture_output=True,
        text=True,
        check=True,
    )
    _local_ahead, upstream_ahead = result.stdout.split()
    return int(upstream_ahead)


def ensure_push_can_fast_forward(repoAbsPath):
    upstream = upstream_name()
    if not upstream:
        exit_with_error("No upstream branch configured", repoAbsPath)

    try:
        subprocess.run(["git", "fetch", "--quiet"], check=True)
        upstream_ahead = upstream_ahead_count(upstream)
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Could not check upstream state: {e}", repoAbsPath)

    if upstream_ahead:
        exit_with_error(
            f"Upstream {upstream} is ahead by {upstream_ahead} commit(s); pull or rebase before auto-commit",
            repoAbsPath,
        )


def wait_for_index_lock(repoAbsPath):
    lock_file_path = os.path.join(repoAbsPath, ".git", "index.lock")
    if not os.path.exists(lock_file_path):
        return

    logger.warning(
        f"{lock_file_path} exists in repo {repoAbsPath}. Waiting for other git operations to finish."
    )
    time.sleep(10)
    if os.path.exists(lock_file_path):
        exit_with_error("Git index lock still exists; leaving it untouched", repoAbsPath)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "message", help="Custom commit message", nargs="?", default=None
    )
    parser.add_argument(
        "-p", "--path", help="Path to apply the git operations to", default="."
    )
    args = parser.parse_args()

    repoAbsPath = getAbsPathFromPWD(args.path)

    os.chdir(repoAbsPath)

    wait_for_index_lock(repoAbsPath)
    ensure_push_can_fast_forward(repoAbsPath)

    try:
        if has_staged_changes():
            exit_with_error(
                "Refusing to auto-commit pre-existing staged changes", repoAbsPath
            )
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Could not inspect staged changes: {e}", repoAbsPath)

    try:
        subprocess.run(["git", "add", "."], check=True)
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Git add failed: {e}", repoAbsPath)

    try:
        has_changes_to_commit = has_staged_changes()
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Could not inspect staged changes: {e}", repoAbsPath)

    if has_changes_to_commit:
        custom_message = args.message if args.message else generate_commit_message()
        try:
            subprocess.run(["git", "commit", "-m", custom_message], check=True)
            logger.info(f"Commit successful in repo {repoAbsPath}. Pushing to remote.")
        except subprocess.CalledProcessError as e:
            exit_with_error(f"Commit failed: {e}", repoAbsPath)
    else:
        logger.info(f"No changes to commit in repo {repoAbsPath}. Pushing anyway.")

    try:
        subprocess.run(["git", "push"], check=True)
        logger.info(f"Push successful in repo {repoAbsPath}.")
    except subprocess.CalledProcessError as e:
        exit_with_error(f"Push failed: {e}", repoAbsPath)


if __name__ == "__main__":
    main()
