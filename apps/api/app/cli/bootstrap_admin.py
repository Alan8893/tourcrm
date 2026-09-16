"""Interactive operator CLI for the initial TourCRM installation bootstrap.

Invocation:

    python -m app.cli.bootstrap_admin

The CLI owns operator I/O only. Transactional bootstrap logic lives in
`app.authentication.bootstrap` and is outside the public HTTP API.

Passwords are read only through `getpass.getpass()` and are never printed,
logged, accepted as command-line arguments, or read from environment/config
files.
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


def _prompt_club_name() -> str:
    while True:
        club_name = input("Club name: ").strip()
        if club_name:
            return club_name
        print("Club name must not be empty.", file=sys.stderr)


def _prompt_email() -> str:
    while True:
        email = input("Administrator email: ").strip()
        if email:
            return email
        print("Administrator email must not be empty.", file=sys.stderr)


def _prompt_password(label: str) -> str:
    return getpass.getpass(f"{label}: ")


def _confirm(club_name: str, email: str) -> bool:
    print()
    print("Create initial TourCRM installation:")
    print(f"  Club:  {club_name}")
    print(f"  Email: {email}")
    print(f"  Name:  {BOOTSTRAP_ADMIN_FIRST_NAME} {BOOTSTRAP_ADMIN_LAST_NAME}")
    print(f"  Role:  {ADMIN_ROLE_CODE}")
    print(f"  Scope: {GLOBAL_ADMIN_SCOPE_TYPE} (Club)")
    print()
    answer = input("Continue? [y/N]: ").strip().lower()
    return answer == "y"


def main() -> int:
    print("TourCRM initial installation bootstrap")
    print()

    # Avoid asking for credentials when bootstrap is already completed. The
    # authoritative race-safe check is repeated under the advisory lock by
    # bootstrap_initial_administrator().
    with session_scope() as session:
        if global_administrator_exists(session):
            print(_ALREADY_EXISTS_MESSAGE, file=sys.stderr)
            return 1

    club_name = _prompt_club_name()
    email = _prompt_email()
    password = _prompt_password("Password")
    confirm_password = _prompt_password("Confirm password")

    if password != confirm_password:
        print("Passwords do not match. No changes made.", file=sys.stderr)
        return 1

    if not _confirm(club_name, email):
        print("Aborted. No changes made.")
        return 0

    try:
        with session_scope() as session:
            result = bootstrap_initial_administrator(
                session,
                club_name=club_name,
                email=email,
                password=password,
            )
    except (BootstrapError, WeakPasswordError) as exc:
        print(f"Cannot bootstrap: {exc}", file=sys.stderr)
        return 1

    print()
    print("Initial TourCRM installation created.")
    print(f"  Club ID:   {result.club_id}")
    print(f"  Person ID: {result.person_id}")
    print(f"  User ID:   {result.user_id}")
    print()
    print("Log in normally through the TourCRM login page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
