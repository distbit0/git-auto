# Auto-Commit Safety

- The notes cron entry runs `gitAutoCommit.py -p /home/pimania/notes` every 20 minutes. On 2026-05-31, it raced with a Codex session that had staged a partial notes change, committed that staged state, and pushed it before Codex amended the commit locally.
- User intent for `gp`: it should avoid manual intervention and eventually auto-commit/push any reachable repo state, including staged changes left behind by a dead or interrupted process. It may wait briefly to avoid racing an active commit, but should not block indefinitely only because staged changes pre-existed the current run.
- Pre-existing staged changes are therefore handled by waiting for the staged diff to remain stable, then explicitly logging that auto-commit is taking them over. If a previous auto-commit run left a `.git/git_auto_commit.pending` marker, a later run may resume immediately because the script owns that staging window.
- It should abort when the upstream branch is ahead, so it does not create extra local commits when a push cannot fast-forward.
- A lingering `.git/index.lock` should be removed only after it is stale and no Git process is still active in the repo. This lets auto-commit recover from a crashed prior Git operation without deleting a live Git lock.
- The original divergence is better prevented by not rewriting/amending commits that have already been pushed. With that rule, auto-commit taking over stable staged changes can at worst create an early commit that later work follows up, rather than causing a local/remote history split.
