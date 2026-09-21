"""Atomic Person-creation wizard (TH-0116, GitHub Issue #150).

Canonical guided flow (the issue's own wording): Person -> basic data ->
initial role -> role-specific contextual setup -> complete. The
technical entities behind each step (ClubMembership, User,
RoleAssignment, GroupMembership, GroupInstructorAssignment,
GuardianRelationship) stay exactly as they already are; only the guided
UX and this one orchestration entry point are new.

This introduces no new persistence model and no new domain validation.
Every mutation is performed by calling the exact same, already-canonical
service functions this codebase already uses for each entity
individually:

- app.people.service.create_person_with_membership (Person + its
  initial ClubMembership)
- app.authentication.account_provisioning.create_user_for_person
  (automatic account provisioning, ADR-0038 — active-with-email or
  pending-stub-without-email, per that module's own docstring)
- app.role_assignments.service.create_role_assignment (the initial
  RoleAssignment, ADR-0039)
- app.groups.service.create_group_instructor_assignment /
  create_group_membership (Instructor's 0..N groups / Member's 1..N
  groups)
- app.people.guardian_service.create_guardian_relationship (Guardian's
  1..N children)

Same validation, same typed errors, same audit actions as each of those
already-shipped endpoints. The one thing this module adds is the
transaction boundary itself. Each function above was written to commit
its own transaction when called directly from its own single-purpose
endpoint (correct there); calling several of them in sequence with the
real session would make a brand-new Person durably visible even if a
later step (e.g. a chosen Group being archived, or a duplicate
GuardianRelationship) then failed — exactly the partial-success state
this issue explicitly prohibits ("Person создан, но User/Role/Group/
Guardian setup не создан").

`_DeferredCommitSession` is a thin proxy, not a second orchestration
framework: it turns each nested `.commit()` call into a `.flush()`
(keeping every intermediate row's generated id/server defaults visible
to the next step, exactly as a commit would, without ending the
transaction), while forwarding every other call — `.rollback()`
included — to the real session unmodified. A business-rule rejection
deep inside any one of the composed functions still calls
`session.rollback()` on what is, underneath the proxy, the same real
session — aborting the *entire* wizard transaction, which is exactly the
"rollback everything" semantics this issue requires. Only this module's
own final `session.commit()` (on the real session, never the proxy) ever
durably commits anything.

Single-Club invariant (`_resolve_sole_club_id`): duplicated rather than
imported from app.role_assignments.person_roles/app.authentication.
account_provisioning's own identical helpers, per this codebase's own
established convention for this exact function (already duplicated
twice before this module).
"""

import uuid
from datetime import date, datetime, timezone
from typing import Literal, Optional, Sequence, cast

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.authentication import account_provisioning
from app.db.authorization import Role
from app.db.groups import Group
from app.db.identity import Club, Person
from app.groups import service as groups_service
from app.people import service as people_service
from app.people.guardian_service import create_guardian_relationship
from app.role_assignments.service import create_role_assignment

PersonWizardRoleCode = Literal["admin", "instructor", "member", "guardian"]

# Mirrors app.role_assignments.person_roles's identical constant/
# rationale exactly: `role.manage` is only ever effective with
# `scope_type='all'` today, and it is the only value already correct for
# every canonical role (see that module's own docstring point 2).
_ROLE_ASSIGNMENT_SCOPE_TYPE = "all"

# ADR-0025 §18's documented example value; also used by
# LinkChildDialog/CreateGuardianRelationshipDialog's own free-text field
# on the frontend — the wizard does not expose this as a choice.
_WIZARD_GUARDIAN_RELATIONSHIP_TYPE = "parent"

_WIZARD_INSTRUCTOR_ROLE_IN_GROUP = "instructor"


class PersonWizardError(Exception):
    """Base class for this module's typed, expected failures."""


class NoClubConfiguredError(PersonWizardError):
    """No Club exists yet. Should be unreachable — bootstrap creates the
    installation's one Club atomically with its first administrator."""


class MultipleClubsConfiguredError(PersonWizardError):
    """More than one Club exists — a violation of TourCRM's current
    single-Club product invariant (mirrors the identical reasoning in
    app.role_assignments.person_roles / app.authentication.
    account_provisioning)."""


class MemberRequiresAtLeastOneGroupError(PersonWizardError):
    """Section 10 of the issue: the Member role's contextual step requires
    1..N groups — the wizard does not complete with zero."""


class GuardianRequiresAtLeastOneChildError(PersonWizardError):
    """Section 11 of the issue: the Guardian role's contextual step
    requires 1..N children — the wizard does not complete with zero."""


class UnexpectedContextualSelectionError(PersonWizardError):
    """Backend authoritative (section 17): `group_ids` is only meaningful
    for `instructor`/`member`, `child_person_ids` only for `guardian` — a
    request combining a role with the other role's contextual selection
    is rejected outright rather than silently ignored, since the client
    is never trusted to have applied this rule itself."""

    def __init__(self, *, role_code: str, field_name: str) -> None:
        super().__init__(f"{field_name!r} is not applicable to role {role_code!r}")
        self.role_code = role_code
        self.field_name = field_name


class GroupNotFoundError(PersonWizardError):
    """One of `group_ids` does not exist. Checked explicitly before
    calling app.groups.service (whose own row-locking helpers use
    `.scalar_one()` and would otherwise raise an unhandled
    `NoResultFound`)."""

    def __init__(self, *, group_id: uuid.UUID) -> None:
        super().__init__(f"Group {group_id} not found")
        self.group_id = group_id


class ChildPersonNotFoundError(PersonWizardError):
    """One of `child_person_ids` does not exist. Checked explicitly before
    calling app.people.guardian_service (whose insert would otherwise
    fail with an unhandled foreign-key `IntegrityError`)."""

    def __init__(self, *, person_id: uuid.UUID) -> None:
        super().__init__(f"Person {person_id} not found")
        self.person_id = person_id


class _DeferredCommitSession:
    """See module docstring. `object.__setattr__` avoids recursing through
    `__getattr__` while setting the one real attribute this proxy owns."""

    _session: Session

    def __init__(self, session: Session) -> None:
        object.__setattr__(self, "_session", session)

    def commit(self) -> None:
        self._session.flush()

    def __getattr__(self, name: str) -> object:
        return getattr(self._session, name)


def _resolve_sole_club_id(session: Session) -> uuid.UUID:
    club_ids = session.execute(sa.select(Club.id)).scalars().all()
    if len(club_ids) == 0:
        raise NoClubConfiguredError("No Club exists yet; bootstrap must run first")
    if len(club_ids) > 1:
        raise MultipleClubsConfiguredError(
            "More than one Club exists; TourCRM's current product requires exactly one"
        )
    return club_ids[0]


def create_person_with_wizard(
    session: Session,
    *,
    first_name: str,
    last_name: str,
    middle_name: Optional[str],
    birth_date: Optional[date],
    phone: Optional[str],
    email: Optional[str],
    address: Optional[str],
    role_code: PersonWizardRoleCode,
    group_ids: Sequence[uuid.UUID],
    child_person_ids: Sequence[uuid.UUID],
    actor_user_id: uuid.UUID,
    request_id: Optional[str] = None,
) -> tuple[Person, Optional[str]]:
    """Runs the entire wizard — Person + ClubMembership + User (ADR-0038
    account provisioning) + initial RoleAssignment (ADR-0039) + any
    role-specific contextual GroupInstructorAssignment/GroupMembership/
    GuardianRelationship — as one atomic operation (see module
    docstring). Returns `(person, temporary_credential)`; the credential
    is `None` exactly when `create_user_for_person` itself would return
    one for a bare account creation (Person has no email yet).

    Raises MemberRequiresAtLeastOneGroupError /
    GuardianRequiresAtLeastOneChildError before any write is attempted
    (`group_ids`/`child_person_ids` cardinality),
    UnexpectedContextualSelectionError before any write is attempted
    (a selection that does not apply to `role_code`), NoClubConfiguredError
    / MultipleClubsConfiguredError (fail-closed single-Club invariant,
    section 6), or any typed error already raised by the composed
    functions listed in the module docstring (DuplicateLoginIdentifierError,
    RoleAssignmentClubMembershipMissingError, DuplicateRoleAssignmentError,
    GroupArchivedError, InstructorClubMembershipMissingError,
    GroupMembershipClubMismatchError, DuplicateActiveGroupMembershipError,
    GroupInstructorPrimaryConflictError, SelfLinkNotAllowedError,
    DuplicateActiveGuardianRelationshipError) — persisting nothing in any
    case (see module docstring for why this module's own transaction
    boundary makes that true even though each of those functions commits
    when called directly).
    """
    if role_code in ("admin", "guardian") and group_ids:
        raise UnexpectedContextualSelectionError(role_code=role_code, field_name="group_ids")
    if role_code in ("admin", "instructor", "member") and child_person_ids:
        raise UnexpectedContextualSelectionError(role_code=role_code, field_name="child_person_ids")
    if role_code == "member" and len(group_ids) == 0:
        raise MemberRequiresAtLeastOneGroupError(
            "The member role requires at least one group"
        )
    if role_code == "guardian" and len(child_person_ids) == 0:
        raise GuardianRequiresAtLeastOneChildError(
            "The guardian role requires at least one child"
        )

    club_id = _resolve_sole_club_id(session)
    role_id = session.execute(
        sa.select(Role.id).where(Role.code == role_code)
    ).scalar_one()

    deferred = cast(Session, _DeferredCommitSession(session))
    try:
        person, membership = people_service.create_person_with_membership(
            deferred,
            first_name=first_name,
            last_name=last_name,
            middle_name=middle_name,
            birth_date=birth_date,
            phone=phone,
            email=email,
            address=address,
            club_id=club_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        user, temporary_credential = account_provisioning.create_user_for_person(
            deferred,
            person_id=person.id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        create_role_assignment(
            deferred,
            user_id=user.id,
            role_id=role_id,
            scope_type=_ROLE_ASSIGNMENT_SCOPE_TYPE,
            club_id=club_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

        now = datetime.now(timezone.utc)
        if role_code == "instructor":
            for group_id in group_ids:
                if session.get(Group, group_id) is None:
                    raise GroupNotFoundError(group_id=group_id)
                groups_service.create_group_instructor_assignment(
                    deferred,
                    group_id=group_id,
                    user_id=user.id,
                    role_in_group=_WIZARD_INSTRUCTOR_ROLE_IN_GROUP,
                    valid_from=now,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
        elif role_code == "member":
            for group_id in group_ids:
                if session.get(Group, group_id) is None:
                    raise GroupNotFoundError(group_id=group_id)
                groups_service.create_group_membership(
                    deferred,
                    group_id=group_id,
                    club_membership_id=membership.id,
                    valid_from=now,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )
        elif role_code == "guardian":
            for child_person_id in child_person_ids:
                if session.get(Person, child_person_id) is None:
                    raise ChildPersonNotFoundError(person_id=child_person_id)
                create_guardian_relationship(
                    deferred,
                    guardian_person_id=person.id,
                    child_person_id=child_person_id,
                    relationship_type=_WIZARD_GUARDIAN_RELATIONSHIP_TYPE,
                    actor_user_id=actor_user_id,
                    request_id=request_id,
                )

        session.commit()
    except Exception:
        session.rollback()
        raise
    return person, temporary_credential


__all__ = [
    "PersonWizardRoleCode",
    "PersonWizardError",
    "NoClubConfiguredError",
    "MultipleClubsConfiguredError",
    "MemberRequiresAtLeastOneGroupError",
    "GuardianRequiresAtLeastOneChildError",
    "UnexpectedContextualSelectionError",
    "GroupNotFoundError",
    "ChildPersonNotFoundError",
    "create_person_with_wizard",
]
