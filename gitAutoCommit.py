import subprocess
import re
import argparse
import os
import logging
import time
from os import path


def getAbsPathFromScript(relPath):
    basepath = path.dirname(__file__)
    fullPath = path.abspath(path.join(basepath, relPath))

    return fullPath


logging.basicConfig(
    filename=getAbsPathFromScript("git_auto_commit.log"), level=logging.INFO
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
    if not commit_message:
        commit_message = "Commit involves changes in hidden files or directories only"

    return commit_message


def check_git_process():
    result = subprocess.run(["ps", "-ef"], capture_output=True, text=True)
    return "git" in result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p", "--path", help="Path to apply the git operations to", default="."
    )
    parser.add_argument("-m", "--message", help="Custom commit message", default=None)
    args = parser.parse_args()

    repoAbsPath = getAbsPathFromPWD(args.path)

    os.chdir(repoAbsPath)

    lock_file_path = os.path.join(repoAbsPath, ".git", "index.lock")

    if os.path.exists(lock_file_path):
        logging.warning(
            f"{lock_file_path} exists in repo {repoAbsPath}. Waiting for other git operations to finish."
        )
        time.sleep(10)
        if os.path.exists(lock_file_path):
            if check_git_process():
                logging.error(f"Git process running in repo {repoAbsPath}. Exiting.")
                exit(1)
            else:
                logging.warning(
                    f"Removing lock file in repo {repoAbsPath} after waiting."
                )
                os.remove(lock_file_path)

    subprocess.run(["git", "add", "."], check=True)

    changes_in_index = subprocess.run(
        ["git", "diff-index", "--quiet", "HEAD", "--"], capture_output=True, text=True
    )
    changes_not_staged = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    if changes_in_index.returncode == 0 and not changes_not_staged:
        logging.info(f"No changes to commit in repo {repoAbsPath}.")
        return

    custom_message = args.message if args.message else generate_commit_message()

    commit_status = subprocess.run(["git", "commit", "-m", custom_message], check=True)

    if commit_status.returncode == 0:
        logging.info(f"Commit successful in repo {repoAbsPath}. Pushing to remote.")
        push_status = subprocess.run(["git", "push"], check=True)
        if push_status.returncode == 0:
            logging.info(f"Push successful in repo {repoAbsPath}.")
        else:
            logging.error(f"Push failed in repo {repoAbsPath}.")
    else:
        logging.error(f"Commit failed in repo {repoAbsPath}.")


if __name__ == "__main__":
    main()
