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


def _resolved_compose_config(extra_env: dict | None = None) -> dict:
    """The resolved Compose configuration, using .env.example so this test
    needs no real .env / no manual setup in a clean checkout."""
    import os

    env = {**os.environ, **(extra_env or {})}
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
        env=env,
    )
    assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def compose_config() -> dict:
    return _resolved_compose_config()


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


@requires_docker_compose
def test_backend_and_frontend_run_as_non_root_by_default(compose_config: dict) -> None:
    for service in ("backend", "frontend"):
        user = compose_config["services"][service]["user"]
        assert user == "1000:1000"  # sane default; never "0:0"


@requires_docker_compose
def test_backend_and_frontend_uid_gid_are_not_hardcoded() -> None:
    # Regression test for the reported EACCES: the compose file itself must
    # use variable substitution for the container UID/GID, not a literal
    # value, so it can be made to match whatever host account owns the
    # bind-mounted apps/api and apps/web directories.
    raw = (ROOT / "docker-compose.yml").read_text()
    assert raw.count("${HOST_UID:-1000}") >= 2  # backend + frontend `user:`
    assert raw.count("${HOST_GID:-1000}") >= 2

    overridden = _resolved_compose_config(extra_env={"HOST_UID": "1234", "HOST_GID": "5678"})
    for service in ("backend", "frontend"):
        assert overridden["services"][service]["user"] == "1234:5678"
        assert overridden["services"][service]["build"]["args"]["UID"] == "1234"
        assert overridden["services"][service]["build"]["args"]["GID"] == "5678"


@requires_docker_compose
def test_backend_and_frontend_build_args_match_runtime_user(compose_config: dict) -> None:
    # The image must be built with the SAME uid/gid it will run as, so
    # files it chowns at build time (e.g. frontend's node_modules, seeding
    # the anonymous volume) are actually owned by the runtime user.
    for service in ("backend", "frontend"):
        svc = compose_config["services"][service]
        user = svc["user"]
        args = svc["build"]["args"]
        assert user == f"{args['UID']}:{args['GID']}"


def test_dockerfiles_parameterize_uid_gid_via_build_args() -> None:
    for dockerfile in [ROOT / "apps" / "api" / "Dockerfile", ROOT / "apps" / "web" / "Dockerfile"]:
        content = dockerfile.read_text()
        assert "ARG UID=1000" in content
        assert "ARG GID=1000" in content
        # No leftover hardcoded --uid/--gid 1000 bypassing the build args.
        assert "--uid 1000" not in content
        assert "--gid 1000" not in content
