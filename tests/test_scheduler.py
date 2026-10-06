"""Static checks for the periodic-system-job scheduler (Issue #289,
ADR-0044): the repository crontab, the pinned supercronic install in the
API image, and the development-only Compose `scheduler` service.

No running container or database is needed here (only the `docker compose`
CLI for the Compose checks). The crontab syntax itself is validated by
`supercronic -test`, and the built image is exercised end to end, in the CI
`scheduler` job; the crontab command is run against a real PostgreSQL in
apps/api/tests/integration/test_scheduler_crontab.py.
"""

import json
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CRONTAB = ROOT / "apps" / "api" / "scheduler" / "crontab"
DOCKERFILE = ROOT / "apps" / "api" / "Dockerfile"
COMPOSE_FILE = ROOT / "docker-compose.yml"

RECONCILE_CLI = "python -m app.cli.reconcile_event_lifecycle"
SCHEDULER_COMMAND = ["supercronic", "-json", "/app/scheduler/crontab"]
CRON_INTERVAL_SECONDS = 60


def _crontab_jobs() -> list[tuple[str, str]]:
    """(five-field schedule, command) for every job line of the crontab."""
    jobs = []
    for line in CRONTAB.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split(None, 5)
        jobs.append((" ".join(fields[:5]), fields[5]))
    return jobs


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)s", value)
    assert match, f"timeout durations must be explicit seconds, got {value!r}"
    return int(match.group(1))


def _reconciliation_timeout() -> tuple[int, int]:
    """(TERM limit, extra seconds before KILL) of the reconciliation job."""
    _, command = _crontab_jobs()[0]
    match = re.fullmatch(
        r"timeout --verbose --kill-after=(?P<kill>\S+) (?P<limit>\S+) " + re.escape(RECONCILE_CLI),
        command,
    )
    assert match, f"unexpected reconciliation command: {command!r}"
    return _seconds(match.group("limit")), _seconds(match.group("kill"))


def _docker_compose_available() -> bool:
    try:
        subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_docker_compose = pytest.mark.skipif(
    not _docker_compose_available(),
    reason="docker compose is not available in this environment",
)


def test_crontab_runs_only_event_lifecycle_reconciliation_every_minute() -> None:
    jobs = _crontab_jobs()
    assert len(jobs) == 1
    schedule, command = jobs[0]
    assert schedule == "* * * * *"
    assert command.endswith(RECONCILE_CLI)


def test_reconciliation_run_is_bounded_within_the_one_minute_interval() -> None:
    # ADR-0044 §6.3: overlap is disabled, so one stuck run must end as an
    # observable failure (exit 124, or 137 after the hard kill) rather than
    # block every later run. TERM bound + hard-kill grace stays below the
    # interval, so a timed-out run never delays the next minute's run.
    limit, kill_after = _reconciliation_timeout()
    assert 0 < limit
    assert 0 < kill_after
    assert limit + kill_after < CRON_INTERVAL_SECONDS


def test_overlapping_runs_are_never_enabled() -> None:
    # supercronic's default never starts a job while its previous run is
    # still going; only its `-overlapping` flag would change that.
    for _, command in _crontab_jobs():
        assert "-overlapping" not in command
    assert "-overlapping" not in SCHEDULER_COMMAND
    for path in (DOCKERFILE, COMPOSE_FILE, ROOT / ".github" / "workflows" / "ci.yml"):
        assert "-overlapping" not in path.read_text(), path


def test_supercronic_is_pinned_and_checksum_verified() -> None:
    content = DOCKERFILE.read_text()

    version = re.search(r"^ARG SUPERCRONIC_VERSION=(\S+)$", content, re.MULTILINE)
    assert version, "SUPERCRONIC_VERSION must be pinned in the Dockerfile"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", version.group(1)), version.group(1)
    assert "latest" not in version.group(1)

    for arch in ("AMD64", "ARM64"):
        checksum = re.search(rf"^ARG SUPERCRONIC_SHA256_{arch}=(\S+)$", content, re.MULTILINE)
        assert checksum, f"SUPERCRONIC_SHA256_{arch} must be fixed in the Dockerfile"
        assert re.fullmatch(r"[0-9a-f]{64}", checksum.group(1))

    assert (
        "https://github.com/aptible/supercronic/releases/download/"
        "${SUPERCRONIC_VERSION}/supercronic-linux-${arch}"
    ) in content
    assert "sha256sum -c -" in content
    # Installed outside /app, which the development compose bind-mounts.
    assert "/usr/local/bin/supercronic" in content


def test_api_dependencies_add_no_broker_or_in_process_scheduler() -> None:
    requirements = (ROOT / "apps" / "api" / "requirements.txt").read_text().lower()
    for forbidden in ("celery", "redis", "apscheduler", "rq", "dramatiq", "arq"):
        assert not re.search(rf"^{forbidden}\b", requirements, re.MULTILINE), forbidden


def _compose_config(*profiles: str) -> dict:
    args = ["docker", "compose", "--env-file", ".env.example"]
    for profile in profiles:
        args += ["--profile", profile]
    result = subprocess.run(
        [*args, "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def scheduler_compose_config() -> dict:
    return _compose_config("scheduler")


@requires_docker_compose
def test_application_starts_without_the_scheduler() -> None:
    # Development-only and opt-in: a plain `docker compose up` must not
    # need (or start) the scheduler.
    assert "scheduler" not in _compose_config()["services"]


@requires_docker_compose
def test_scheduler_service_runs_supercronic_with_the_repository_crontab(
    scheduler_compose_config: dict,
) -> None:
    scheduler = scheduler_compose_config["services"]["scheduler"]
    assert scheduler["profiles"] == ["scheduler"]
    # Exec form, no entrypoint/init wrapper: supercronic is PID 1.
    assert scheduler["command"] == SCHEDULER_COMMAND
    assert scheduler.get("entrypoint") is None
    assert not scheduler.get("init", False)


@requires_docker_compose
def test_scheduler_matches_the_backend_build_user_and_database(
    scheduler_compose_config: dict,
) -> None:
    services = scheduler_compose_config["services"]
    scheduler, backend = services["scheduler"], services["backend"]
    assert scheduler["build"] == backend["build"]
    assert scheduler["user"] == backend["user"] == "1000:1000"
    assert scheduler["volumes"] == backend["volumes"]
    assert scheduler["environment"]["DATABASE_URL"] == backend["environment"]["DATABASE_URL"]
    # Starts only after the backend has applied migrations.
    assert scheduler["depends_on"]["backend"]["condition"] == "service_healthy"


@requires_docker_compose
def test_scheduler_exposes_nothing_and_is_a_single_replica(scheduler_compose_config: dict) -> None:
    scheduler = scheduler_compose_config["services"]["scheduler"]
    assert "ports" not in scheduler
    assert scheduler["healthcheck"] == {"disable": True}
    assert scheduler.get("deploy", {}).get("replicas", 1) == 1
    for volume in scheduler["volumes"]:
        assert "docker.sock" not in volume["source"]


@requires_docker_compose
def test_scheduler_stop_grace_period_covers_one_bounded_run(
    scheduler_compose_config: dict,
) -> None:
    limit, kill_after = _reconciliation_timeout()
    grace = scheduler_compose_config["services"]["scheduler"]["stop_grace_period"]
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", grace)
    assert match, grace
    grace_seconds = int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    assert grace_seconds >= limit + kill_after
