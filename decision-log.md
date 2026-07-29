# Decision Log

## Check connectivity and remote write permission before changing a repository

- Date: 2026-07-29
- Decision: for network remotes, require NetworkManager to report full internet connectivity before the permission probe and recheck before pushing. An offline or indeterminate state skips network operations without an error. Before staging publishable work, verify the selected push remote with a non-mutating dry-run push and cache the result inside the repository by push URL. A cached or newly detected write denial skips the repository without changing it.
- Rationale: scheduled discovery includes upstream checkouts under `~/dev`; those repositories must not accumulate automatic local commits merely because their remotes are read-only or the machine is offline. Offline failures written to the notes inbox can otherwise create a self-sustaining commit/error loop.
- Trade-off: a conservative or stale NetworkManager state can defer a reachable remote until a later scheduled run. Cached permissions persist until the push URL changes or the cache is manually removed; a real push denial replaces a stale writable result with read-only.

## Repository-local two-week auto-commit pauses

- Date: 2026-07-14
- Decision: skip a repository while `.git/git_auto_commit.pause` is less than fourteen days old. Once it expires, retain it through any failed run and remove it only after the normal auto-commit/push path succeeds or confirms that nothing needs pushing.
- Rationale: autonomous work can stay in the original checkout and use ordinary feature branches without risking immediate scheduled commits or pushes. The marker's age makes protection independent of how many commits the work requires.

## Route auto-commit failures to the notes inbox

- Date: 2026-07-14
- Decision: append `gitAutoCommit.py` failures directly to `~/notes/inbox-index.md` without a desktop notification; do not send them to the shared `~/dev/error_log.txt` pipeline.
- Rationale: auto-commit failures can require judgment about repository state, so the scheduled error-fixing skill must not claim and potentially resolve them automatically.

## Lazy remote reconciliation for `gp`

- Date: 2026-06-27
- Decision: avoid the routine pre-push `git fetch`; push directly when there is something local to publish, then fetch/rebase/retry only after a non-fast-forward rejection.
- Rationale: profiling showed clean `gp` runs were dominated by network round trips, and `git push` already performs the authoritative remote fast-forward check.
- Trade-off: a local auto-commit can be created before discovering upstream changed. The script handles the common case by rebasing automatically and stops with an explicit error when a conflict requires manual resolution.

## Take ownership of stable staged state

- Decision: `gp` may commit staged changes left by another process after the staged diff remains stable for the configured wait, and may resume immediately when its own pending marker exists.
- Rationale: scheduled automation must eventually recover abandoned staged work without racing an active commit or blocking forever on state from a dead process.
- Constraint: a stale Git lock is removable only when no Git process still owns repository work. Already-pushed commits are never rewritten; later corrections use later commits.
