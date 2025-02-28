import subprocess
import re
import argparse
import os
import logging
import time
import sys
from os import path


def getAbsPathFromScript(relPath):
    basepath = path.dirname(__file__)
    fullPath = path.abspath(path.join(basepath, relPath))

    return fullPath


# Configure root logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# File handler
file_handler = logging.FileHandler(getAbsPathFromScript("git_auto_commit.log"))
file_handler.setLevel(logging.INFO)

# Stream handler to output logs to console
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)

# Formatter
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(formatter)
stream_handler.setFormatter(formatter)

# Add handlers to root logger
logger.addHandler(file_handler)
logger.addHandler(stream_handler)


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


def check_git_process():
    result = subprocess.run(["ps", "-ef"], capture_output=True, text=True)
    lines = result.stdout.split("\n")
    for line in lines:
        if "git" in line.split():
            return True
    return False


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

    lock_file_path = os.path.join(repoAbsPath, ".git", "index.lock")

    if os.path.exists(lock_file_path):
        logger.warning(
            f"{lock_file_path} exists in repo {repoAbsPath}. Waiting for other git operations to finish."
        )
        time.sleep(10)
        if os.path.exists(lock_file_path):
            if check_git_process():
                logger.error(
                    f"Git process running in repo {repoAbsPath}. Waiting another 60 seconds"
                )
                time.sleep(60)
            logger.warning(f"Removing lock file in repo {repoAbsPath} after waiting.")
            os.remove(lock_file_path)

    try:
        subprocess.run(["git", "add", "."], check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Git add failed in repo {repoAbsPath}: {e}")
        subprocess.run(
            [
                "notify-send",
                "Git AutoCommit Error",
                f"Git add failed: {e}\nRepository: {repoAbsPath}",
                "--urgency=critical",
                "--icon=dialog-error",
            ]
        )
        sys.exit(1)

    changes_in_index = subprocess.run(
        ["git", "diff-index", "--quiet", "HEAD", "--"], capture_output=True, text=True
    )
    changes_not_staged = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    if changes_in_index.returncode == 0 and not changes_not_staged:
        logger.info(f"No changes to commit in repo {repoAbsPath}.")
        return

    custom_message = args.message if args.message else generate_commit_message()

    try:
        subprocess.run(["git", "commit", "-m", custom_message], check=True)
        logger.info(f"Commit successful in repo {repoAbsPath}. Pushing to remote.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Commit failed in repo {repoAbsPath}: {e}")
        subprocess.run(
            [
                "notify-send",
                "Git AutoCommit Error",
                f"Commit failed: {e}\nRepository: {repoAbsPath}",
                "--urgency=critical",
                "--icon=dialog-error",
            ]
        )
        sys.exit(1)

    try:
        subprocess.run(["git", "push"], check=True)
        logger.info(f"Push successful in repo {repoAbsPath}.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Push failed in repo {repoAbsPath}: {e}")
        subprocess.run(
            [
                "notify-send",
                "Git AutoCommit Error",
                f"Push failed: {e}\nRepository: {repoAbsPath}\nCheck internet connection",
                "--urgency=critical",
                "--icon=dialog-error",
            ]
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
