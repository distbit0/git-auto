import subprocess
import sys


def main():
    custom_message = sys.argv[1] if len(sys.argv) > 1 else "Default commit message"

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

    # Stage all changes
    subprocess.run(["git", "add", "."])

    # Commit the changes with the custom message
    commit_status = subprocess.run(["git", "commit", "-m", custom_message])

    if commit_status.returncode == 0:
        print("Commit successful. Pushing to remote.")

        # Push to remote
        push_status = subprocess.run(["git", "push"])
        if push_status.returncode == 0:
            print("Push successful.")
        else:
            print("Push failed.")
    else:
        print("Commit failed.")


if __name__ == "__main__":
    main()
