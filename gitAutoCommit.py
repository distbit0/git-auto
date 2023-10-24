import subprocess
import re
import argparse
import os

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
        commit_message = "Commit involves changes in hidden files or directories only."

    return commit_message

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--path", help="Path to apply the git operations to", default=".")
    parser.add_argument("-m", "--message", help="Custom commit message", default=None)
    args = parser.parse_args()

    if args.path != ".":
        os.chdir(args.path)
    
    subprocess.run(["git", "add", "."])

    changes_in_index = subprocess.run(
        ["git", "diff-index", "--quiet", "HEAD", "--"], capture_output=True, text=True
    )
    changes_not_staged = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture_output=True,
        text=True,
    ).stdout.strip()

    if changes_in_index.returncode == 0 and not changes_not_staged:
        print("No changes to commit.")
        return

    custom_message = args.message if args.message else generate_commit_message()

    commit_status = subprocess.run(["git", "commit", "-m", custom_message])

    if commit_status.returncode == 0:
        print("Commit successful. Pushing to remote.")
        push_status = subprocess.run(["git", "push"])
        if push_status.returncode == 0:
            print("Push successful.")
        else:
            print("Push failed.")
    else:
        print("Commit failed.")

if __name__ == "__main__":
    main()
