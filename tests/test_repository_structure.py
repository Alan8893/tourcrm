"""Repository-level structure smoke check for Issue #4 (application skeleton).

Verifies the monorepo layout documented in
docs/03-architecture/application-architecture.md (§19) and guards against
accidental duplicate/alternative application roots.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

FORBIDDEN_ALTERNATIVE_ROOTS = ["frontend", "backend", "client", "server", "src"]


def test_expected_application_roots_exist() -> None:
    assert (ROOT / "apps" / "web").is_dir()
    assert (ROOT / "apps" / "api").is_dir()


def test_no_duplicate_application_roots() -> None:
    for name in FORBIDDEN_ALTERNATIVE_ROOTS:
        assert not (ROOT / name).exists(), f"Unexpected alternative application root: {name}"


def test_backend_entrypoint_exists() -> None:
    assert (ROOT / "apps" / "api" / "app" / "main.py").is_file()


def test_frontend_entrypoint_exists() -> None:
    assert (ROOT / "apps" / "web" / "src" / "main.tsx").is_file()
