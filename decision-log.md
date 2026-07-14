# Decision Log

## Repository-local two-week auto-commit pauses

- Date: 2026-07-14
- Decision: skip a repository while `.git/git_auto_commit.pause` is less than fourteen days old. Once it expires, retain it through any failed run and remove it only after the normal auto-commit/push path succeeds or confirms that nothing needs pushing.
- Rationale: autonomous work can stay in the original checkout and use ordinary feature branches without risking immediate scheduled commits or pushes. The marker's age makes protection independent of how many commits the work requires.

## Route auto-commit failures to the notes inbox

- Date: 2026-07-14
- Decision: append `gitAutoCommit.py` failures directly to `~/notes/inbox-index.md` while retaining the critical desktop notification; do not send them to the shared `~/dev/error_log.txt` pipeline.
- Rationale: auto-commit failures can require judgment about repository state, so the scheduled error-fixing skill must not claim and potentially resolve them automatically.

## Lazy remote reconciliation for `gp`

- Date: 2026-06-27
- Decision: avoid the routine pre-push `git fetch`; push directly when there is something local to publish, then fetch/rebase/retry only after a non-fast-forward rejection.
- Rationale: profiling showed clean `gp` runs were dominated by network round trips, and `git push` already performs the authoritative remote fast-forward check.
- Trade-off: a local auto-commit can be created before discovering upstream changed. The script handles the common case by rebasing automatically and stops with an explicit error when a conflict requires manual resolution.
