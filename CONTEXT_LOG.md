# Auto-Commit Safety

- The notes cron entry runs `gitAutoCommit.py -p /home/pimania/notes` every 20 minutes. On 2026-05-31, it raced with a Codex session that had staged a partial notes change, committed that staged state, and pushed it before Codex amended the commit locally.
- `gitAutoCommit.py` should treat pre-existing staged changes as owned by another human/tool and abort rather than committing them. It should also abort when the upstream branch is ahead, so it does not create extra local commits when a push cannot fast-forward.
- A lingering `.git/index.lock` should not be deleted by this automation. If it remains after a short wait, the safer behavior is to abort and let the next cron run retry.
