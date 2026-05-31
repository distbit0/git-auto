# Auto-Commit Safety

- The notes cron entry runs `gitAutoCommit.py -p /home/pimania/notes` every 20 minutes. On 2026-05-31, it raced with a Codex session that had staged a partial notes change, committed that staged state, and pushed it before Codex amended the commit locally.
- `gitAutoCommit.py` should treat pre-existing staged changes as owned by another human/tool and abort rather than committing them. If a previous auto-commit run left a `.git/git_auto_commit.pending` marker, a later run may resume those staged changes because the script owns that staging window.
- It should abort when the upstream branch is ahead, so it does not create extra local commits when a push cannot fast-forward.
- A lingering `.git/index.lock` should be removed only after it is stale and no Git process is still active in the repo. This lets auto-commit recover from a crashed prior Git operation without deleting a live Git lock.
