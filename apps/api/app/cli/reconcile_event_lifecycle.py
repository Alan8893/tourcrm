"""Periodic Event lifecycle reconciliation (Issue #281, ADR-0018).

Invocation (e.g. from cron / a scheduled job), the same way as
app.cli.reconcile_achievements:

    python -m app.cli.reconcile_event_lifecycle

Moves every due Event `published -> in_progress` once `start_at` is
reached and `in_progress -> completed` once `end_at` is reached,
converging any transition a previous run missed. Never publishes a
`draft` and never changes a `cancelled`, `completed` or `archived`
Event — see app.events.lifecycle_reconciliation. Safe to re-run and to
run concurrently. Exits non-zero if any Event could not be reconciled
(it is retried by the next run).
"""

import sys

from app.db.session import session_scope
from app.events.lifecycle_reconciliation import reconcile_event_lifecycle


def main() -> int:
    with session_scope() as session:
        result = reconcile_event_lifecycle(session)
    print(
        f"Event lifecycle reconciliation: {result.examined} due event(s), "
        f"{result.transitioned} transitioned ({result.started} started, "
        f"{result.completed} completed), {result.failed} failed."
    )
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
