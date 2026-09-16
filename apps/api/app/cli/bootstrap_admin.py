"""Interactive operator CLI for the initial administrator bootstrap
(Issue #99 / TH-0089, ADR-0027).

Invocation (documented in apps/api/README.md "Initial administrator
bootstrap"):

    python -m app.cli.bootstrap_admin

This module owns all of this operation's I/O — the interactive prompt,
confirmation, and exit code — and nothing else. The actual check/create
transaction lives in `app.authentication.bootstrap`, which this module
never bypasses. Bootstrap is deliberately not an HTTP endpoint (ADR-0027:
"not a normal `/api/v1/auth/*` endpoint") and this module adds no second
authentication/authorization mechanism of its own.

Security (ADR-0027 "Credential input" / Issue #99 §2): the password is
read only via `getpass.getpass()` (no local echo). It is never accepted
as a command-line argument, never read from an environment variable or
`.env` file, and never logged or printed back — this module prints only
the supplied email, the fixed placeholder name, the role code, and the
scope for operator confirmation.
"""

import getpass
import sys

from app.authentication.bootstrap import (
    ADMIN_ROLE_CODE,
    BOOTSTRAP_ADMIN_FIRST_NAME,
    BOOTSTRAP_ADMIN_LAST_NAME,
    GLOBAL_ADMIN_SCOPE_TYPE,
    BootstrapError,
    bootstrap_initial_administrator,
    global_administrator_exists,
)
from app.authentication.passwords import WeakPasswordError
from app.db.session import session_scope

_ALREADY_EXISTS_MESSAGE = (
    "A global administrator already exists. Bootstrap refuses to create another."
)


def _prompt_email() -> str:
    while True:
        email = input("Email: ").strip()
        if email:
            return email
        print("Email must not be empty.", file=sys.stderr)


def _prompt_password(label: str) -> str:
    return getpass.getpass(f"{label}: ")


def _confirm(email: str) -> bool:
    print()
    print("Create initial administrator:")
    print(f"  Email: {email}")
    print(f"  Name: {BOOTSTRAP_ADMIN_FIRST_NAME} {BOOTSTRAP_ADMIN_LAST_NAME}")
    print(f"  Role: {ADMIN_ROLE_CODE}")
    print(f"  Scope: {GLOBAL_ADMIN_SCOPE_TYPE} (global)")
    print()
    answer = input("Continue? [y/N]: ").strip().lower()
    return answer == "y"


def main() -> int:
    print("TourCRM initial administrator bootstrap")
    print()

    # Lock-free early check, purely so the operator is not asked for a
    # password when bootstrap could not possibly succeed (Issue #99 §5:
    # "до запроса пароля проверить ... пароль НЕ спрашивать"). The
    # authoritative, race-safe check happens again, advisory-lock-guarded,
    # inside bootstrap_initial_administrator below.
    with session_scope() as session:
        if global_administrator_exists(session):
            print(_ALREADY_EXISTS_MESSAGE, file=sys.stderr)
            return 1

    email = _prompt_email()
    password = _prompt_password("Password")
    confirm_password = _prompt_password("Confirm password")

    if password != confirm_password:
        print("Passwords do not match. No changes made.", file=sys.stderr)
        return 1

    if not _confirm(email):
        print("Aborted. No changes made.")
        return 0

    try:
        with session_scope() as session:
            result = bootstrap_initial_administrator(session, email=email, password=password)
    except (BootstrapError, WeakPasswordError) as exc:
        print(f"Cannot bootstrap: {exc}", file=sys.stderr)
        return 1

    print()
    print("Initial administrator created.")
    print(f"  Person ID: {result.person_id}")
    print(f"  User ID:   {result.user_id}")
    print()
    print("Log in normally: POST /api/v1/auth/login, then GET /api/v1/auth/me.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
