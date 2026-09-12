"""Static/config checks for the Issue #8 Docker Compose development
environment. These validate the compose configuration itself (via
`docker compose config`, which does not require pulling any image) and file
presence/content — no container runtime is required.

A real Docker Engine + `docker compose` CLI on PATH is required to run this
file; it is a documented prerequisite for Issue #8 (see README.md), not an
assumption specific to this test.
"""

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _docker_compose_available() -> bool:
    try:
        subprocess.run(
            ["docker", "compose", "version"],
            cwd=ROOT,
            capture_output=True,
            timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


requires_docker_compose = pytest.mark.skipif(
    not _docker_compose_available(),
    reason="docker compose is not available in this environment",
)


@pytest.fixture(scope="module")
def compose_config() -> dict:
    """The resolved Compose configuration, using .env.example so this test
    needs no real .env / no manual setup in a clean checkout."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "config",
            "--format",
            "json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_dockerfiles_exist() -> None:
    assert (ROOT / "apps" / "api" / "Dockerfile").is_file()
    assert (ROOT / "apps" / "web" / "Dockerfile").is_file()


def test_dockerignore_files_exist() -> None:
    assert (ROOT / "apps" / "api" / ".dockerignore").is_file()
    assert (ROOT / "apps" / "web" / ".dockerignore").is_file()


def test_dockerignore_excludes_secrets_and_vcs() -> None:
    for path in [ROOT / "apps" / "api" / ".dockerignore", ROOT / "apps" / "web" / ".dockerignore"]:
        content = path.read_text()
        assert ".env" in content
        assert ".git" in content


def test_dockerignore_excludes_dependency_and_cache_dirs() -> None:
    api_ignore = (ROOT / "apps" / "api" / ".dockerignore").read_text()
    assert ".venv" in api_ignore
    assert "__pycache__" in api_ignore

    web_ignore = (ROOT / "apps" / "web" / ".dockerignore").read_text()
    assert "node_modules" in web_ignore


def test_env_example_exists_with_no_real_secret_values() -> None:
    content = (ROOT / ".env.example").read_text()
    assert "POSTGRES_PASSWORD=changeme" in content
    # Guard against accidentally pasting something real-looking later.
    forbidden_substrings = ["BEGIN RSA", "BEGIN PRIVATE", "sk-", "AKIA"]
    for forbidden in forbidden_substrings:
        assert forbidden not in content


def test_env_is_gitignored_but_example_is_not() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],
        cwd=ROOT,
    )
    assert result.returncode == 0, ".env must be gitignored"

    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"],
        cwd=ROOT,
    )
    assert result.returncode == 1, ".env.example must NOT be gitignored"


@requires_docker_compose
def test_compose_config_is_valid(compose_config: dict) -> None:
    assert set(compose_config["services"].keys()) >= {"backend", "frontend", "db"}


@requires_docker_compose
def test_postgres_has_persistent_named_volume(compose_config: dict) -> None:
    assert "postgres_data" in compose_config["volumes"]

    db_volumes = compose_config["services"]["db"]["volumes"]
    assert any(
        v.get("source") == "postgres_data" and v.get("target") == "/var/lib/postgresql/data"
        for v in db_volumes
    )


@requires_docker_compose
def test_postgres_has_no_published_host_port(compose_config: dict) -> None:
    assert "ports" not in compose_config["services"]["db"]


@requires_docker_compose
def test_postgres_has_a_real_healthcheck(compose_config: dict) -> None:
    healthcheck = compose_config["services"]["db"].get("healthcheck")
    assert healthcheck is not None
    assert "pg_isready" in " ".join(healthcheck["test"])


@requires_docker_compose
def test_backend_waits_for_postgres_to_be_actually_healthy(compose_config: dict) -> None:
    depends_on = compose_config["services"]["backend"]["depends_on"]
    assert depends_on["db"]["condition"] == "service_healthy"


@requires_docker_compose
def test_backend_reaches_postgres_by_service_name_not_localhost(compose_config: dict) -> None:
    database_url = compose_config["services"]["backend"]["environment"]["DATABASE_URL"]
    assert "@db:" in database_url
    assert "localhost" not in database_url
    assert "127.0.0.1" not in database_url


@requires_docker_compose
def test_frontend_and_backend_are_reachable_from_the_host(compose_config: dict) -> None:
    for service in ("backend", "frontend"):
        assert "ports" in compose_config["services"][service]
