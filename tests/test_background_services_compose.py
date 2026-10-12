"""Development Compose runs every TourCRM process with one command (PR #346
follow-up): db, backend, frontend and the background processes scheduler,
telegram-poller and outbox-worker.

Checks the RESOLVED configuration (`docker compose config`, no image pull,
no running container) against the real CLIs, and the documentation against
that configuration. A live smoke run of the same file is described in
README.md ("Фоновые процессы").
"""

import json
import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"

BACKGROUND = {
    "scheduler": ["supercronic", "-json", "/app/scheduler/crontab"],
    "telegram-poller": ["python", "-m", "app.cli.run_telegram_poller"],
    "outbox-worker": ["python", "-m", "app.cli.run_outbox_worker"],
}
ALL_SERVICES = {"db", "backend", "frontend", *BACKGROUND}


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


def _config(extra_env: dict[str, str] | None = None) -> dict:
    env = {**os.environ, **(extra_env or {})}
    result = subprocess.run(
        ["docker", "compose", "--env-file", ".env.example", "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def services() -> dict:
    return _config()["services"]


# --- One command starts everything ------------------------------------------------------


@requires_docker_compose
def test_plain_up_starts_every_required_service(services: dict) -> None:
    assert set(services) == ALL_SERVICES
    for name, service in services.items():
        assert "profiles" not in service, f"{name} would be skipped by `docker compose up`"


def test_compose_file_declares_no_profiles() -> None:
    assert not re.search(r"^\s+profiles:", (ROOT / "docker-compose.yml").read_text(), re.M)


# --- Existing entry points, same image/source/database ------------------------------------


@requires_docker_compose
def test_background_commands_are_the_existing_entry_points(services: dict) -> None:
    for name, command in BACKGROUND.items():
        assert services[name]["command"] == command, name
        assert services[name].get("entrypoint") is None, name
    for module in ("run_telegram_poller", "run_outbox_worker"):
        source = (API / "app" / "cli" / f"{module}.py").read_text()
        assert 'if __name__ == "__main__":' in source, module
    assert (API / "scheduler" / "crontab").is_file()


@requires_docker_compose
def test_background_services_share_the_backend_image_user_source_and_network(
    services: dict,
) -> None:
    backend = services["backend"]
    for name in BACKGROUND:
        service = services[name]
        assert service["build"] == backend["build"], name
        assert service["user"] == backend["user"] == "1000:1000", name
        assert service["volumes"] == backend["volumes"], name
        assert service["networks"] == backend["networks"], name
        assert (
            service["environment"]["DATABASE_URL"] == backend["environment"]["DATABASE_URL"]
        ), name
        assert "@db:5432/" in service["environment"]["DATABASE_URL"], name


# --- Environment: what each process needs, and nothing it does not ------------------------


@requires_docker_compose
def test_each_process_gets_exactly_the_key_rings_it_uses(services: dict) -> None:
    def keys(name: str) -> set[str]:
        return {k for k in services[name]["environment"] if k.endswith("_ENCRYPTION_KEYS")}

    both = {"SETTINGS_ENCRYPTION_KEYS", "AUTH_DELIVERY_ENCRYPTION_KEYS"}
    assert keys("backend") == both
    assert keys("outbox-worker") == both
    assert keys("telegram-poller") == {"SETTINGS_ENCRYPTION_KEYS"}
    assert keys("scheduler") == set()
    assert "APP_PUBLIC_BASE_URL" in services["outbox-worker"]["environment"]


@requires_docker_compose
def test_api_and_worker_always_receive_the_same_delivery_key_never_the_settings_key() -> None:
    resolved = _config(
        {
            "AUTH_DELIVERY_ENCRYPTION_KEYS": "mail1:delivery-test-value",
            "SETTINGS_ENCRYPTION_KEYS": "set1:settings-test-value",
        }
    )["services"]
    for name in ("backend", "outbox-worker"):
        env = resolved[name]["environment"]
        assert env["AUTH_DELIVERY_ENCRYPTION_KEYS"] == "mail1:delivery-test-value", name
        assert env["SETTINGS_ENCRYPTION_KEYS"] == "set1:settings-test-value", name
    assert resolved["telegram-poller"]["environment"]["SETTINGS_ENCRYPTION_KEYS"] == (
        "set1:settings-test-value"
    )


def test_key_rings_are_defined_once_and_shared_by_anchor() -> None:
    raw = (ROOT / "docker-compose.yml").read_text()
    for variable in ("AUTH_DELIVERY_ENCRYPTION_KEYS", "SETTINGS_ENCRYPTION_KEYS"):
        assert raw.count(f"${{{variable}") == 1, variable  # one interpolation only
    assert raw.count("@db:5432/${POSTGRES_DB}") == 1


@requires_docker_compose
def test_no_secret_travels_on_a_command_line(services: dict) -> None:
    for name, service in services.items():
        command = " ".join(service.get("command") or [])
        for marker in ("KEYS", "PASSWORD", "TOKEN", "postgresql", "DATABASE_URL"):
            assert marker not in command, (name, marker)


# --- Start order, migrations, health, restarts, shutdown -----------------------------------


@requires_docker_compose
def test_start_order_waits_for_a_ready_database_and_applied_migrations(services: dict) -> None:
    assert services["backend"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert "pg_isready" in " ".join(services["db"]["healthcheck"]["test"])
    for name in BACKGROUND:
        depends_on = services[name]["depends_on"]
        assert set(depends_on) == {"backend"}, name
        assert depends_on["backend"]["condition"] == "service_healthy", name


@requires_docker_compose
def test_nothing_depends_on_a_background_service(services: dict) -> None:
    for name, service in services.items():
        assert not set(service.get("depends_on", {})) & set(BACKGROUND), name


@requires_docker_compose
def test_only_the_backend_runs_migrations(services: dict) -> None:
    for name, service in services.items():
        assert "alembic" not in " ".join(service.get("command") or []), name
    # The backend uses the image's CMD, the single place that migrates.
    assert services["backend"].get("command") is None
    dockerfile = (API / "Dockerfile").read_text()
    cmd = [line for line in dockerfile.splitlines() if line.startswith("CMD ")]
    assert len(cmd) == 1 and cmd[0].count("alembic upgrade head") == 1
    assert "exec uvicorn" in cmd[0]  # SIGTERM reaches uvicorn: clean `stop`


@requires_docker_compose
def test_background_services_have_no_http_healthcheck_and_bounded_restarts(
    services: dict,
) -> None:
    for name in BACKGROUND:
        service = services[name]
        assert service["healthcheck"] == {"disable": True}, name
        assert service["restart"] == "on-failure:5", name
        assert "ports" not in service, name
        assert service.get("deploy", {}).get("replicas", 1) == 1, name


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(?:(\d+)m)?(?:(\d+)s)?", value)
    assert match, value
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


@requires_docker_compose
def test_stop_grace_periods_cover_work_in_progress(services: dict) -> None:
    assert _seconds(services["scheduler"]["stop_grace_period"]) >= 55
    # long poll (20s) + request timeout (10s)
    assert _seconds(services["telegram-poller"]["stop_grace_period"]) >= 30
    assert _seconds(services["outbox-worker"]["stop_grace_period"]) >= 30


# --- Documentation matches the configuration -----------------------------------------------


def test_env_example_documents_both_key_rings_without_values() -> None:
    for path in (ROOT / ".env.example", API / ".env.example"):
        text = path.read_text()
        for variable in ("SETTINGS_ENCRYPTION_KEYS", "AUTH_DELIVERY_ENCRYPTION_KEYS"):
            lines = [ln for ln in text.splitlines() if ln.lstrip("# ").startswith(variable)]
            assert lines, (path, variable)
            # Only commented placeholders, never a real-looking value.
            assert all(ln.startswith("#") and "<" in ln for ln in lines), (path, lines)


def test_docs_describe_the_actual_default_start_and_every_background_service() -> None:
    readme = (ROOT / "README.md").read_text()
    infra = (ROOT / "docs" / "08-infrastructure" / "infrastructure-and-devops.md").read_text()
    assert "docker compose up -d" in readme
    for name in BACKGROUND:
        assert f"docker compose logs -f {name}" in readme, name
    for text in (readme, infra, (API / "README.md").read_text()):
        assert "--profile scheduler" not in text
        assert "--profile telegram" not in text
