"""allow pending user without login identifier

Revision ID: 9a1c2f5e7b3d
Revises: 668662ab95e0
Create Date: 2026-09-21 00:00:00.000000

TH-0116 / GitHub Issue #150: automatic account provisioning at Person-
creation time must be able to create a User even when the Person has no
email yet (`login_identifier` unknown). `users.login_identifier` was
previously `NOT NULL`, which made that impossible.

Desired invariant (TH-0116's own wording): a `pending` account created
without an email has `login_identifier = NULL` and `password_hash =
NULL`; any `active`/`locked`/`suspended` account has `login_identifier
!= NULL`.

That literal wording cannot become a blanket `status = 'pending' ->
login_identifier IS NULL` CHECK constraint, though: self-registration
(`app.authentication.service.register`, FR-REG-001) has ALREADY used
`status = 'pending'` since before this task for a *different* meaning —
an awaiting-admin-approval account that already has a real
`login_identifier` and `password_hash`. A blanket constraint would
reject every such existing/future row.

The constraint below instead encodes the actual invariant needed: a NULL
`login_identifier` is permitted ONLY for this new pending-and-
credential-less "stub" case (`status = 'pending' AND password_hash IS
NULL`) — self-registration's pending rows are untouched (they satisfy
the constraint via `login_identifier IS NOT NULL` regardless of status/
password), and any `active`/`locked`/`suspended` row is still forced to
have a `login_identifier` (the pending-stub disjunct requires `status =
'pending'`, which such a row never satisfies).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a1c2f5e7b3d'
down_revision: Union[str, None] = '668662ab95e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT_NAME = "ck_users_login_identifier_required_unless_pending_stub"


def upgrade() -> None:
    op.alter_column("users", "login_identifier", existing_type=sa.String(length=255), nullable=True)
    # The generated column's own NOT NULL must be relaxed too:
    # `lower(trim(NULL))` evaluates to NULL, which a NOT NULL generated
    # column would reject on insert. Multiple NULLs never violate
    # `uq_users_normalized_login_identifier` (standard SQL NULL
    # semantics), so any number of pending-stub accounts can coexist.
    op.alter_column(
        "users", "normalized_login_identifier", existing_type=sa.String(length=255), nullable=True
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "users",
        "login_identifier IS NOT NULL OR (status = 'pending' AND password_hash IS NULL)",
    )


def downgrade() -> None:
    # No existing row can have a NULL login_identifier before this
    # revision's upgrade ever ran, but a row created by TH-0116's pending-
    # stub account provisioning after upgrading would violate the
    # restored NOT NULL constraints — this downgrade does not attempt to
    # invent a placeholder identifier for such rows (see this module's
    # own "no fictitious/generated login identifier is invented" rule);
    # an operator downgrading past this revision must first resolve any
    # such row (assign a real email/login_identifier or delete it).
    op.drop_constraint(_CONSTRAINT_NAME, "users", type_="check")
    op.alter_column(
        "users", "normalized_login_identifier", existing_type=sa.String(length=255), nullable=False
    )
    op.alter_column("users", "login_identifier", existing_type=sa.String(length=255), nullable=False)
