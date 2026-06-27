# Decision Log

## Lazy remote reconciliation for `gp`

- Date: 2026-06-27
- Decision: avoid the routine pre-push `git fetch`; push directly when there is something local to publish, then fetch/rebase/retry only after a non-fast-forward rejection.
- Rationale: profiling showed clean `gp` runs were dominated by network round trips, and `git push` already performs the authoritative remote fast-forward check.
- Trade-off: a local auto-commit can be created before discovering upstream changed. The script handles the common case by rebasing automatically and stops with an explicit error when a conflict requires manual resolution.
