"""Person API — /api/v1/persons (Issue #62, TH-0101 / Issue #116).

Canonical sources: docs/05-api/people-api.md §4-8 (as reconciled),
docs/02-requirements/roles-and-permissions.md, ADR-0013 (scopes),
ADR-0014 (response envelope), ADR-0017 (Person has no `status`),
ADR-0024/ADR-0025 (audit), ADR-0035 (People management authorization),
ADR-0034 (Person archiving deferred).

Endpoints intentionally NOT implemented here (Issue #62 non-goals):
Group/Invitation/RegistrationRequest/Import API, and
`POST /persons/{person_id}/archive`. (`GET/POST /persons/{person_id}/
role-assignments` and `DELETE .../role-assignments/{role_code}` were
added later by TH-0112 / ADR-0039 — see that section below; the
canonical RoleAssignment resource itself remains the flat
`/api/v1/role-assignments`, ADR-0025 §6. `GET/POST /persons/{person_id}/
account` and `POST .../account/password-reset` were added later still by
TH-0113 / ADR-0038 — see that section below.) The archive endpoint is
formally deferred by
ADR-0034, not an open gap: `Person` has no `status`/`archived_at`/
soft-delete field, physical deletion is not part of the domain contract,
and archive semantics must not be simulated through User/ClubMembership/
GroupMembership/GuardianRelationship lifecycle changes. A future archive
decision must define persistence, lifecycle, authorization and API
contract separately (ADR-0034) — this module does not implement or
approximate any of that.

`GET/POST /persons/{person_id}/guardian-relationships` (Issue #64 §7)
live here rather than in app.api.v1.guardian_relationships, mirroring how
`GET /persons/{person_id}/memberships` lives here rather than in
app.api.v1.memberships — only the top-level `/guardian-relationships/{id}
...` paths live in that other module.

Existence-hiding for the single-Person endpoints (detail/update),
mirroring app.api.v1.events exactly: a Person that does not exist and a
Person that exists but the caller is not authorized to act on receive an
identical 404. The list endpoint instead silently excludes unauthorized
rows via app.people.queries.list_persons_page (never a 403). `POST
/persons` (no object yet exists to hide) uses the generic 403
AuthorizationDenied contract; `PATCH /persons/{person_id}` also uses it,
but only for the narrower `birth_date` admin-only field gate (ADR-0035
§5) — by that point existence/general-update access has already been
established via the 404 check above, so a 403 there discloses nothing an
authorized-for-other-fields caller didn't already know.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentPrincipal,
    require_authenticated_principal,
    require_csrf_token,
)
from app.api.errors import APIError
from app.api.request_context import get_request_id
from app.api.schemas import CollectionResponse, Pagination
from app.api.v1.account_schemas import PersonAccountCredentialOut, PersonAccountOut
from app.api.v1.guardian_relationships import guardian_relationship_out
from app.api.v1.guardian_relationships_schemas import (
    GuardianRelationshipCreateRequest,
    GuardianRelationshipOut,
)
from app.api.v1.memberships_schemas import MembershipOut
from app.api.v1.persons_schemas import PersonCreateRequest, PersonOut, PersonUpdateRequest
from app.api.v1.role_assignments_schemas import (
    PersonRoleAssignmentCreateRequest,
    PersonRoleAssignmentOut,
)
from app.authentication import account_provisioning
from app.authorization.context import ResourceContext
from app.authorization.service import AuthorizationDenied, Authorizer
from app.db.authorization import UserRoleAssignment
from app.db.identity import Person, User
from app.db.session import get_db
from app.people import guardian_service
from app.people import service as people_service
from app.people.authorization import (
    has_person_create_assignment,
    is_person_visible,
    is_system_admin_person_update_grant,
    resolve_current_club_id_for_person_create,
)
from app.people.guardian_authorization import build_guardian_relationship_create_context
from app.people.guardian_lifecycle import (
    DuplicateActiveGuardianRelationshipError,
    SelfLinkNotAllowedError,
)
from app.people.guardian_queries import (
    GUARDIAN_RELATIONSHIP_DEFAULT_SORT,
    list_guardian_relationships_for_child,
)
from app.people.guardian_queries import InvalidSortError as InvalidGuardianSortError
from app.people.queries import (
    MEMBERSHIP_DEFAULT_SORT,
    PERSON_DEFAULT_SORT,
    InvalidSortError,
    list_memberships_page,
    list_persons_page,
)
from app.role_assignments import person_roles as person_role_service
from app.role_assignments.service import (
    DuplicateRoleAssignmentError,
    RoleAssignmentClubMembershipMissingError,
)

router = APIRouter(prefix="/persons", tags=["persons"])

_NOT_FOUND_DETAIL = "Person not found"
_ROLE_ASSIGNMENT_NOT_FOUND_CODE = "role_assignment_not_found"
_ROLE_ASSIGNMENT_NOT_FOUND_MESSAGE = "Role assignment not found"


def _person_out(person: Person) -> PersonOut:
    return PersonOut(
        id=person.id,
        first_name=person.first_name,
        last_name=person.last_name,
        middle_name=person.middle_name,
        birth_date=person.birth_date,
        phone=person.phone,
        email=person.email,
        address=person.address,
        photo_file_id=person.photo_file_id,
        created_at=person.created_at,
        updated_at=person.updated_at,
    )


def _membership_out(membership) -> MembershipOut:
    return MembershipOut(
        id=membership.id,
        club_id=membership.club_id,
        person_id=membership.person_id,
        membership_type=membership.membership_type,
        status=membership.status,
        joined_at=membership.joined_at,
        left_at=membership.left_at,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
    )


def _get_authorized_person_or_404(
    db: Session, *, person_id: uuid.UUID, user_id: uuid.UUID, permission_code: str
) -> Person:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    # Person authorization cannot use the generic Authorizer/ResourceContext
    # engine — see app.people.authorization module docstring for why.
    if not is_person_visible(
        db, person_id=person.id, user_id=user_id, permission_code=permission_code
    ):
        # Deliberately the same detail/status as "does not exist" above —
        # see module docstring.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    return person


@router.get("", response_model=CollectionResponse[PersonOut])
def list_persons(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=PERSON_DEFAULT_SORT),
    search: str | None = Query(default=None, max_length=255),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[PersonOut]:
    try:
        rows, total = list_persons_page(
            db,
            user_id=principal.user_id,
            permission_code="person.read",
            page=page,
            page_size=page_size,
            sort=sort,
            search=search,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_person_out(person) for person in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get("/{person_id}", response_model=PersonOut)
def get_person(
    person_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> PersonOut:
    person = _get_authorized_person_or_404(
        db, person_id=person_id, user_id=principal.user_id, permission_code="person.read"
    )
    return _person_out(person)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=PersonOut)
def create_person(
    payload: PersonCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonOut:
    # Person is Club-neutral (ADR-0017) and a not-yet-created Person has no
    # target Club to check a club-scoped assignment's boundary against —
    # the generic Authorizer/club_boundary_matches engine would incorrectly
    # deny a club-scoped `all`-scope assignment here (e.g. the bootstrap-
    # created primary administrator). See
    # app.people.authorization.has_person_create_assignment (TH-0106 /
    # Issue #131) for the narrow, documented exception this uses instead.
    # ADR-0035 §2 introduces `person.create` as its own canonical
    # permission, distinct from `person.update`; only `admin` holds it in
    # the current MVP.
    #
    # TH-0111 / Issue #140: this endpoint's actual operation is "add a
    # person to the current Club," not bare Person creation — a Person
    # with no ClubMembership is invisible to the club-scoped admin who
    # just created them (person_visibility_filter's club-scoped `all`
    # predicate requires one). `person.create` alone remains the
    # authorization gate: the initial ClubMembership this operation
    # creates has no discretionary field (membership_type/status/
    # joined_at are all fixed, see create_person_with_membership) and is
    # a structural consequence of this one operation, not an independent
    # act of membership management — so `membership.manage` is
    # deliberately NOT also required here. `PersonCreateRequest`/
    # `PersonOut` are unchanged; the target Club is resolved server-side,
    # never accepted from the client.
    if not has_person_create_assignment(db, principal.user_id):
        raise AuthorizationDenied("person.create")
    club_id = resolve_current_club_id_for_person_create(db, principal.user_id)

    person = people_service.create_person_with_membership(
        db,
        first_name=payload.first_name,
        last_name=payload.last_name,
        middle_name=payload.middle_name,
        birth_date=payload.birth_date,
        phone=payload.phone,
        email=payload.email,
        address=payload.address,
        photo_file_id=payload.photo_file_id,
        club_id=club_id,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
    )
    return _person_out(person)


@router.patch("/{person_id}", response_model=PersonOut)
def update_person(
    person_id: uuid.UUID,
    payload: PersonUpdateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonOut:
    person = _get_authorized_person_or_404(
        db, person_id=person_id, user_id=principal.user_id, permission_code="person.update"
    )
    fields = payload.model_dump(exclude_unset=True)
    if "birth_date" in fields:
        # ADR-0035 §5: only the canonical system admin role may change
        # birth_date, including on the admin's own Person — a bare
        # all-scope person.update grant via any role is not sufficient
        # (see app.people.authorization.is_system_admin_person_update_
        # grant's own docstring for why this cannot be expressed as a
        # plain Authorizer.check(ResourceContext()) scope check).
        if not is_system_admin_person_update_grant(db, principal.user_id):
            raise AuthorizationDenied("person.update")
    person = people_service.update_person(
        db,
        person=person,
        actor_user_id=principal.user_id,
        request_id=get_request_id(request),
        **fields,
    )
    return _person_out(person)


@router.get("/{person_id}/memberships", response_model=CollectionResponse[MembershipOut])
def list_person_memberships(
    person_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=MEMBERSHIP_DEFAULT_SORT),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[MembershipOut]:
    """A Person's membership history across Clubs (Issue #62 §7).

    Existence of `person_id` is checked, but visibility of the returned
    memberships is governed entirely by `membership.read` + scope (never
    `person.read`) — this is a Membership listing, not a Person read.
    """
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    try:
        rows, total = list_memberships_page(
            db,
            user_id=principal.user_id,
            permission_code="membership.read",
            page=page,
            page_size=page_size,
            sort=sort,
            person_id=person_id,
        )
    except InvalidSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[_membership_out(membership) for membership in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.get(
    "/{person_id}/guardian-relationships",
    response_model=CollectionResponse[GuardianRelationshipOut],
)
def list_person_guardian_relationships(
    person_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort: str = Query(default=GUARDIAN_RELATIONSHIP_DEFAULT_SORT),
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[GuardianRelationshipOut]:
    """Guardians of `person_id` — the child-side view (Issue #64 §7)."""
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    try:
        rows, total = list_guardian_relationships_for_child(
            db,
            person_id=person_id,
            user_id=principal.user_id,
            permission_code="guardian_relationship.read",
            page=page,
            page_size=page_size,
            sort=sort,
        )
    except InvalidGuardianSortError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_sort", f"Unsupported sort value: {sort}"
        ) from exc

    pages = (total + page_size - 1) // page_size if total else 0
    return CollectionResponse(
        items=[guardian_relationship_out(relationship) for relationship in rows],
        pagination=Pagination(page=page, page_size=page_size, total=total, pages=pages),
    )


@router.post(
    "/{person_id}/guardian-relationships",
    status_code=status.HTTP_201_CREATED,
    response_model=GuardianRelationshipOut,
)
def create_person_guardian_relationship(
    person_id: uuid.UUID,
    payload: GuardianRelationshipCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> GuardianRelationshipOut:
    """Create a GuardianRelationship with `person_id` as the child side
    (Issue #64 §7-8)."""
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    if db.get(Person, payload.guardian_person_id) is None:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_person_id",
            "guardian_person_id does not exist",
        )

    authorizer = Authorizer(
        session=db, user_id=principal.user_id, permission_code="guardian_relationship.manage"
    )
    context = build_guardian_relationship_create_context(
        db, child_person_id=person_id, requester_user_id=principal.user_id
    )
    authorizer.check(context)

    try:
        relationship = guardian_service.create_guardian_relationship(
            db,
            guardian_person_id=payload.guardian_person_id,
            child_person_id=person_id,
            relationship_type=payload.relationship_type,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except SelfLinkNotAllowedError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "guardian_link_not_allowed", str(exc)
        ) from exc
    except DuplicateActiveGuardianRelationshipError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "guardian_link_not_allowed", str(exc)
        ) from exc
    return guardian_relationship_out(relationship)


# --- Person role assignments (TH-0112 / ADR-0039) --------------------------
#
# A Person-scoped, canonical-role-code-only view onto the same
# UserRoleAssignment rows the flat `/api/v1/role-assignments` resource
# already owns (ADR-0025 §6 remains the canonical resource identity; see
# app.role_assignments.person_roles's module docstring for why this
# narrower entry point does not re-litigate that decision). `role.manage`
# is the sole authorization gate, checked the same way the generic API
# checks it: effective `role.manage` against the (sole) Club boundary.


def _person_role_assignment_out(
    assignment: UserRoleAssignment, *, person_id: uuid.UUID
) -> PersonRoleAssignmentOut:
    return PersonRoleAssignmentOut(
        id=assignment.id,
        person_id=person_id,
        role_code=assignment.role.code,
        club_id=assignment.club_id,
        valid_from=assignment.valid_from,
    )


@router.get(
    "/{person_id}/role-assignments",
    response_model=CollectionResponse[PersonRoleAssignmentOut],
)
def list_person_role_assignments(
    person_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> CollectionResponse[PersonRoleAssignmentOut]:
    """All of `person_id`'s currently-effective system roles. Empty for a
    Person with no linked User (see app.role_assignments.person_roles.
    list_person_role_assignments) — indistinguishable here from "has a
    User but zero roles"; only the mutating endpoints below need to tell
    the two apart."""
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    club_id = person_role_service.resolve_sole_club_id(db)
    Authorizer(session=db, user_id=principal.user_id, permission_code="role.manage").check(
        ResourceContext(club_id=club_id)
    )

    rows = person_role_service.list_person_role_assignments(db, person_id=person_id)
    return CollectionResponse(
        items=[_person_role_assignment_out(row, person_id=person_id) for row in rows],
        pagination=Pagination(
            page=1, page_size=len(rows) or 1, total=len(rows), pages=1 if rows else 0
        ),
    )


@router.post(
    "/{person_id}/role-assignments",
    status_code=status.HTTP_201_CREATED,
    response_model=PersonRoleAssignmentOut,
)
def add_person_role_assignment(
    person_id: uuid.UUID,
    payload: PersonRoleAssignmentCreateRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonRoleAssignmentOut:
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    club_id = person_role_service.resolve_sole_club_id(db)
    Authorizer(session=db, user_id=principal.user_id, permission_code="role.manage").check(
        ResourceContext(club_id=club_id)
    )

    if payload.role_code not in person_role_service.CANONICAL_PERSON_ROLE_CODES:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_role_code",
            f"{payload.role_code!r} is not a canonical role code",
        )

    try:
        assignment = person_role_service.add_person_role(
            db,
            person_id=person_id,
            role_code=payload.role_code,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except person_role_service.PersonHasNoUserAccountError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "person_has_no_user_account", str(exc)
        ) from exc
    except RoleAssignmentClubMembershipMissingError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "role_assignment_club_membership_missing",
            str(exc),
        ) from exc
    except DuplicateRoleAssignmentError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "duplicate_role_assignment", str(exc)) from exc
    return _person_role_assignment_out(assignment, person_id=person_id)


@router.delete("/{person_id}/role-assignments/{role_code}", status_code=status.HTTP_204_NO_CONTENT)
def remove_person_role_assignment(
    person_id: uuid.UUID,
    role_code: str,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> None:
    """Revoke `person_id`'s own currently-effective `role_code` assignment
    only — never another Person's, never another role this Person holds.

    Existence-hiding, mirroring `POST /role-assignments/{id}/revoke`
    exactly: "no such Person", "Person has no User yet", and "this Person
    has no active assignment of this role" all receive the identical 404
    (`role_assignment_not_found`) — there is nothing here for an
    unauthorized or already-ended state to disclose.
    """
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)

    club_id = person_role_service.resolve_sole_club_id(db)
    Authorizer(session=db, user_id=principal.user_id, permission_code="role.manage").check(
        ResourceContext(club_id=club_id)
    )

    if role_code not in person_role_service.CANONICAL_PERSON_ROLE_CODES:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_role_code",
            f"{role_code!r} is not a canonical role code",
        )

    try:
        person_role_service.remove_person_role(
            db,
            person_id=person_id,
            role_code=role_code,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except (
        person_role_service.PersonHasNoUserAccountError,
        person_role_service.PersonRoleAssignmentNotFoundError,
    ) as exc:
        raise APIError(
            status.HTTP_404_NOT_FOUND,
            _ROLE_ASSIGNMENT_NOT_FOUND_CODE,
            _ROLE_ASSIGNMENT_NOT_FOUND_MESSAGE,
        ) from exc


# --- Person account management (TH-0113 / ADR-0038) -------------------------
#
# Administrative User-account provisioning for a Person, gated by the
# dedicated `account.manage` permission (never `role.manage`, `settings.
# manage`, or `person.update` — ADR-0038's own framing). Mirrors the exact
# `resolve_sole_club_id` + `Authorizer.check(ResourceContext(club_id=...))`
# shape TH-0112's role-assignment endpoints already established for an
# admin-only, single-Club-scoped permission.

_ACCOUNT_NOT_FOUND_CODE = "account_not_found"
_ACCOUNT_NOT_FOUND_MESSAGE = "Account not found"


def _person_account_out(user: User) -> PersonAccountOut:
    return PersonAccountOut(
        id=user.id,
        person_id=user.person_id,
        login_identifier=user.login_identifier,
        status=user.status,
        email_verified_at=user.email_verified_at,
        last_login_at=user.last_login_at,
    )


def _check_account_manage(db: Session, *, user_id: uuid.UUID) -> None:
    club_id = account_provisioning.resolve_sole_club_id(db)
    Authorizer(session=db, user_id=user_id, permission_code="account.manage").check(
        ResourceContext(club_id=club_id)
    )


@router.get("/{person_id}/account", response_model=PersonAccountOut)
def get_person_account(
    person_id: uuid.UUID,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
) -> PersonAccountOut:
    """Safe account metadata only — never `password_hash`, any token/
    hash, or session data (docs/07-security/security-and-privacy.md §2.6).
    """
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    _check_account_manage(db, user_id=principal.user_id)

    user = db.execute(select(User).where(User.person_id == person_id)).scalar_one_or_none()
    if user is None:
        raise APIError(
            status.HTTP_404_NOT_FOUND, _ACCOUNT_NOT_FOUND_CODE, _ACCOUNT_NOT_FOUND_MESSAGE
        )
    return _person_account_out(user)


@router.post(
    "/{person_id}/account",
    status_code=status.HTTP_201_CREATED,
    response_model=PersonAccountCredentialOut,
)
def create_person_account(
    person_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonAccountCredentialOut:
    """Create a User for `person_id` and issue a one-time first-access
    setup challenge (ADR-0038 §2). Accepts no request body: `person_id`
    is the path, `login_identifier` is always `Person.email`, and no
    password/role/club_id is ever accepted from the client.
    """
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    _check_account_manage(db, user_id=principal.user_id)

    try:
        user, raw_credential = account_provisioning.create_user_for_person(
            db,
            person_id=person_id,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except account_provisioning.PersonEmailMissingError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "person_email_missing", str(exc)
        ) from exc
    except account_provisioning.PersonAlreadyHasAccountError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "account_already_exists", str(exc)) from exc
    except account_provisioning.DuplicateLoginIdentifierError as exc:
        raise APIError(status.HTTP_409_CONFLICT, "duplicate_login_identifier", str(exc)) from exc
    return PersonAccountCredentialOut(
        account=_person_account_out(user), temporary_credential=raw_credential
    )


@router.post("/{person_id}/account/password-reset", response_model=PersonAccountCredentialOut)
def reset_person_account_password(
    person_id: uuid.UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(require_authenticated_principal),
    db: Session = Depends(get_db),
    _csrf: None = Depends(require_csrf_token),
) -> PersonAccountCredentialOut:
    """Issue a fresh one-time reset challenge for `person_id`'s existing
    User (ADR-0038 §7) — never sets a password directly, never creates a
    User. Use `POST .../account` first if `person_id` has no User yet.
    """
    if db.get(Person, person_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_DETAIL)
    _check_account_manage(db, user_id=principal.user_id)

    try:
        user, raw_credential = account_provisioning.admin_reset_password_for_person(
            db,
            person_id=person_id,
            actor_user_id=principal.user_id,
            request_id=get_request_id(request),
        )
    except account_provisioning.PersonHasNoAccountError as exc:
        raise APIError(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "person_has_no_account", str(exc)
        ) from exc
    return PersonAccountCredentialOut(
        account=_person_account_out(user), temporary_credential=raw_credential
    )
