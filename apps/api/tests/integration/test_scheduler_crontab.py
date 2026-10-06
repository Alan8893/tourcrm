"""Scheduler crontab smoke test (Issue #289, ADR-0044).

Runs the exact command line from the repository crontab
(apps/api/scheduler/crontab) — the one supercronic executes every minute —
against real PostgreSQL, the way the scheduler container does: through
`/bin/sh -c`, from the API root, configured only by DATABASE_URL.

The reconciliation behaviour itself is covered by
test_event_lifecycle_reconciliation.py; this file only proves that the
scheduled command reaches the official CLI and that its wall-clock timeout
ends a stuck run as an observable failure that leaves the database clean
for the next run.
"""

import datetime
import os
import pathlib
import subprocess
import sys
import uuid

import sqlalchemy as sa

from app.db.event_recurrence import EventOccurrence
from app.db.events import Event
from app.db.identity import Club
from app.db.session import session_scope

from .conftest import requires_postgres

API_ROOT = pathlib.Path(__file__).resolve().parents[2]
CRONTAB = API_ROOT / "scheduler" / "crontab"
RECONCILE_CLI = "python -m app.cli.reconcile_event_lifecycle"


def _reconciliation_command() -> str:
    for line in CRONTAB.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            command = stripped.split(None, 5)[5]
            if command.endswith(RECONCILE_CLI):
                return command
    raise AssertionError("no Event lifecycle reconciliation job in the crontab")


def _run(command: str) -> subprocess.CompletedProcess[str]:
    # `python` in the crontab must resolve to this test run's interpreter.
    env = {
        **os.environ,
        "PATH": os.pathsep.join([str(pathlib.Path(sys.executable).parent), os.environ["PATH"]]),
    }
    return subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _create_due_published_event() -> uuid.UUID:
    """A `published` Event whose start_at and end_at have both passed."""
    now = datetime.datetime.now(datetime.timezone.utc)
    start_at, end_at = now - datetime.timedelta(hours=2), now - datetime.timedelta(hours=1)
    with session_scope() as session:
        club = Club(name=f"Club {uuid.uuid4().hex[:8]}", status="active")
        session.add(club)
        session.flush()
        event = Event(
            club_id=club.id,
            event_type="lesson",
            title="Scheduled event",
            start_at=start_at,
            end_at=end_at,
            timezone="UTC",
            status="published",
        )
        session.add(event)
        session.flush()
        session.add(
            EventOccurrence(
                event_id=event.id,
                series_id=None,
                club_id=club.id,
                name=event.title,
                event_type=event.event_type,
                recurrence_anchor_at=start_at,
                starts_at=start_at,
                ends_at=end_at,
                timezone="UTC",
                status="scheduled",
            )
        )
        session.commit()
        return event.id


def _statuses(event_id: uuid.UUID) -> tuple[str, str]:
    with session_scope() as session:
        event = session.get(Event, event_id)
        assert event is not None
        occurrence = session.execute(
            sa.select(EventOccurrence).where(EventOccurrence.event_id == event_id)
        ).scalar_one()
        return event.status, occurrence.status


@requires_postgres
def test_crontab_command_runs_the_reconciliation_cli() -> None:
    event_id = _create_due_published_event()

    result = _run(_reconciliation_command())

    assert result.returncode == 0, result.stderr
    assert "Event lifecycle reconciliation: 1 due event(s), 1 transitioned" in result.stdout
    assert _statuses(event_id) == ("completed", "completed")


@requires_postgres
def test_timed_out_run_fails_observably_and_the_next_run_converges() -> None:
    event_id = _create_due_published_event()
    command = _reconciliation_command()
    limit = command.split()[3]
    # Same command, with the limit shortened so the test does not wait for
    # the production bound.
    shortened = command.replace(f" {limit} ", " 3s ", 1)
    assert shortened != command

    # Another transaction holds the Event's row lock for longer than the
    # limit, so the run blocks on its `SELECT ... FOR UPDATE`.
    with session_scope() as blocker:
        blocker.execute(sa.select(Event.id).where(Event.id == event_id).with_for_update())
        result = _run(shortened)
        assert result.returncode == 124, (result.returncode, result.stderr)
        assert "timeout: sending signal TERM to command" in result.stderr
        blocker.rollback()

    # The killed run committed nothing for the Event and left no open
    # transaction behind: the next scheduled run converges it.
    assert _statuses(event_id) == ("published", "scheduled")
    result = _run(command)
    assert result.returncode == 0, result.stderr
    assert _statuses(event_id) == ("completed", "completed")
