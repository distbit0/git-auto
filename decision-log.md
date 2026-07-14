# Decision Log

## Route auto-commit failures to the notes inbox

- Date: 2026-07-14
- Decision: append `gitAutoCommit.py` failures directly to `~/notes/inbox-index.md` while retaining the critical desktop notification; do not send them to the shared `~/dev/error_log.txt` pipeline.
- Rationale: auto-commit failures can require judgment about repository state, so the scheduled error-fixing skill must not claim and potentially resolve them automatically.

## Lazy remote reconciliation for `gp`

- Date: 2026-06-27
- Decision: avoid the routine pre-push `git fetch`; push directly when there is something local to publish, then fetch/rebase/retry only after a non-fast-forward rejection.
- Rationale: profiling showed clean `gp` runs were dominated by network round trips, and `git push` already performs the authoritative remote fast-forward check.
- Trade-off: a local auto-commit can be created before discovering upstream changed. The script handles the common case by rebasing automatically and stops with an explicit error when a conflict requires manual resolution.
