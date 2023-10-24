import subprocess
import sys
import re
import argparse
import os

def generate_commit_message():
    # Run git command to get names of files staged for the current commit
    result = subprocess.run(
        ["git", "diff", "--name-only", "--cached"], capture_output=True, text=True
    )
    commit_message = result.stdout.strip()

    # Remove any paths that start with a dot or contain a slash followed by a dot
    commit_message = "\n".join(
        [
            re.sub(r"^.*/", "", line)
            for line in commit_message.split("\n")
            if not re.search(r"(^\.)|(\/\.)", line)
        ]
    )

    # If commit message is empty after processing, provide a fallback message
    if not commit_message:
        commit_message = "Commit involves changes in hidden files or directories only."

    return commit_message


def main():

    # Parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="Path to apply the git operations to", default=".", nargs='?')
    args = parser.parse_args()
    # Change current working directory to the specified path
    os.chdir(args.path)    # Stage all changes

    subprocess.run(["git", "add", "."])

    # Check if there are any changes in the working directory
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

    # Generate commit message if not provided
    custom_message = sys.argv[1] if len(sys.argv) > 1 else generate_commit_message()

    # Commit the changes with the custom message
    commit_status = subprocess.run(["git", "commit", "-m", custom_message])

    if commit_status.returncode == 0:
        print("Commit successful. Pushing to remote.")

        # Push to remote
        push_status = subprocess.run(
            ["git", "push"]
        )
        if push_status.returncode == 0:
            print("Push successful.")
        else:
            print("Push failed.")
    else:
        print("Commit failed.")


if __name__ == "__main__":
    main()
