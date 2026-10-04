"""Periodic Achievement Engine reconciliation (Issue #220, A7).

Invocation (e.g. from cron / a scheduled job):

    python -m app.cli.reconcile_achievements

Re-evaluates every Member against every applicable Rule Version and
creates only the Awards that are missing (missed events, processing
failures). Never rewrites, revokes or re-points existing Awards — see
app.achievements.engine.
"""

import sys

from app.achievements.engine import reconcile
from app.db.session import session_scope


def main() -> int:
    with session_scope() as session:
        result = reconcile(session)
    print(
        f"Achievement reconciliation: {result.evaluated_people} member(s), "
        f"{result.evaluated_rules} rule(s), {result.awards_created} award(s) created."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
