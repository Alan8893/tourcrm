# ADR-0035 — People Management Authorization

**Status:** Accepted — 2026-09-17  
**Decision owner:** Product Owner / Architect  
**Scope:** Person, ClubMembership, GuardianRelationship, People self-profile  

## 1. Context

TH-0096 completed the specification gate for the remaining People management work. During the gate, the authorization model for Person, ClubMembership and GuardianRelationship was made explicit for all four canonical roles: `admin`, `instructor`, `member`, `guardian`.

This ADR is the canonical decision for those People-management authorization rules. Where older text in `docs/02-requirements/roles-and-permissions.md` or `docs/05-api/people-api.md` conflicts with this ADR, this accepted ADR takes precedence until those documents are mechanically reconciled.

## 2. Person creation

A new canonical permission is introduced:

- `person.create`

Only `admin` receives `person.create` in the current MVP.

`instructor`, `member` and `guardian` do not receive `person.create`.

`Person` remains a Club-neutral identity. In the current MVP there is one initialized Club and the UI does not offer Club selection. Creating a Person and creating a ClubMembership remain distinct domain operations even if one UI flow performs them sequentially.

## 3. Person read/update

The existing `person.read` and `person.update` permissions remain the basis of access. No separate `person.contact.read` or `person.contact.update` permissions are introduced.

### 3.1 Own Person

All four roles may read and update their own Person subject to the rules below.

| Field | Admin | Instructor | Member | Guardian |
|---|---:|---:|---:|---:|
| `id` read | yes | yes | yes | yes |
| `id` update | no | no | no | no |
| `first_name` update | yes | yes | yes | yes |
| `last_name` update | yes | yes | yes | yes |
| `middle_name` update | yes | yes | yes | yes |
| `birth_date` read | yes | yes | yes | yes |
| `birth_date` update | yes | no | no | no |
| `phone` update | yes | yes | yes | yes |
| `email` update | yes | yes | yes | yes |
| `address` update | yes | yes | yes | yes |
| `photo_file_id` update | yes | yes | yes | yes |

Admin may update their own `birth_date`; this is intentional and resolves the previously ambiguous self-profile rule.

`id` is immutable for every role.

### 3.2 Other Person objects

- `admin`: may read/update Persons within the administrator's authorized `all` scope.
- `instructor`: may read/update Persons within `own_groups` only.
- `member`: no access to other Persons through People management.
- `guardian`: no general access to other Persons through People management.

Membership, group co-membership, or GuardianRelationship existence does not by itself grant a Member access to another Person.

## 4. Contact data

Contact fields are:

- `phone`
- `email`
- `address`

Access policy:

- `admin`: may read/update contact fields of Persons in the administrator's authorized scope;
- `instructor`: may read/update contact fields of Persons in `own_groups`;
- `member`: may read/update only their own contact fields;
- `guardian`: may read/update only their own contact fields.

A GuardianRelationship does **not** grant a Guardian access to the child's contact fields.

Contact fields remain fields of `Person`; no separate permission family is introduced.

## 5. Birth date

`birth_date` is readable by all four roles for a Person they are otherwise authorized to read.

Only `admin` may update `birth_date`, including for the administrator's own Person.

## 6. Person archive

Person archiving is not part of the current MVP. The canonical decision is ADR-0034.

There is no `Person.status`, `archived_at`, soft-delete mechanism, or physical-delete operation in the current domain contract. The former `POST /api/v1/persons/{person_id}/archive` endpoint is deferred and must not be implemented or simulated through related lifecycle changes.

## 7. ClubMembership management

`ClubMembership` remains a separate domain entity and preserves membership periods.

### 7.1 Create and type changes

Only `admin` may:

- create a ClubMembership;
- change `membership_type`.

`instructor`, `member` and `guardian` cannot perform these operations.

### 7.2 Lifecycle

Only `admin` may perform all ClubMembership lifecycle transitions already defined by the canonical model:

```text
pending   -> active
pending   -> archived
active    -> suspended
active    -> inactive
active    -> archived
suspended -> active
suspended -> inactive
suspended -> archived
inactive  -> archived
```

`archived` is terminal.

There is no `inactive -> active` transition. Rejoining creates a new membership period/record.

Members and Guardians cannot self-initiate membership lifecycle changes. Instructors cannot perform membership lifecycle changes.

### 7.3 Membership read

- `admin`: full authorized membership visibility;
- `instructor`: membership data within `own_groups` scope;
- `member`: own membership data (`self`);
- `guardian`: children's membership data through the authorized `children` / GuardianRelationship path.

The canonical membership history endpoint remains:

`GET /api/v1/persons/{person_id}/memberships`

No separate `/memberships/{id}/history` endpoint is introduced.

## 8. GuardianRelationship lifecycle

`GuardianRelationship` remains Club-neutral.

No `primary`, `is_primary`, or `primary_guardian_id` concept exists, including outside the MVP. Multiple active representatives are equal in the base relationship model.

### 8.1 Create

Only `admin` may create a GuardianRelationship.

### 8.2 Update

Only `admin` may modify an existing GuardianRelationship's supported non-lifecycle attributes.

`instructor`, `member` and `guardian` cannot modify GuardianRelationship records.

### 8.3 Terminate

Only `admin` may terminate a GuardianRelationship.

`terminate` always produces `status = revoked`.

`revoked` is terminal: it cannot be restored to `active`. If the relationship is needed again, a new GuardianRelationship is created.

Instructors do not create requests/tasks inside the system for termination. If an Instructor identifies a relationship that should be terminated, they communicate that information to an Admin outside the system; the Admin performs the actual termination.

### 8.4 Read visibility

- `admin`: all GuardianRelationships within the administrator's authorized global scope;
- `instructor`: relationships involving Persons reachable through the Instructor's `own_groups` object scope;
- `member`: their own GuardianRelationship records when the Member is a participant in the relationship;
- `guardian`: their own GuardianRelationship records.

A Guardian must not see other representatives of the same child merely because they are both related to that child.

GuardianRelationship visibility does not automatically grant access to Person contact fields.

## 9. Guardian children projection

The canonical endpoint is:

`GET /api/v1/me/children`

It returns only current children for the authenticated Guardian: there must be an active, interval-valid GuardianRelationship from the requester to the child.

Stored/derived relationship states that are not currently active (`inactive` or `revoked`) are excluded.

The endpoint does not accept a client-supplied guardian identity as proof of authorization.

The child projection is:

```text
id
last_name
first_name
middle_name
birth_date
photo_file_id
```

It does not include:

```text
phone
email
address
other GuardianRelationship records
```

The endpoint is a current-children projection, not a historical relationship endpoint.

## 10. Authorization scopes

No new scope is introduced.

People authorization uses the existing canonical vocabulary:

- `all`
- `own_groups`
- `self`
- `children`
- `own_events`
- `none`

For the People decisions in this ADR, the relevant role-level patterns are:

```text
admin      -> all
instructor -> own_groups + self
member     -> self
guardian    -> children + self
```

The scope alone never grants access: permission, object relationship, object status and other canonical authorization conditions are still evaluated server-side.

## 11. Instructor object relationship

For Person access through `own_groups`, the relationship is explicitly derived from the Instructor's active responsibility for a Group and the Person's active membership in that Group:

```text
Instructor
  -> active GroupInstructorAssignment
  -> Group
  -> active GroupMembership
  -> Person
```

Being an Instructor, or being in the same Club, is not sufficient to access every Person.

## 12. IDOR and cross-Club protection

A UUID/opaque identifier never proves authorization.

All People operations must evaluate the authenticated principal, permission, scope, object relationship and applicable object status before returning or mutating data.

Cross-Club relationships must be validated according to the existing Club integrity ADRs. No People endpoint may expose another Club's data through an identifier supplied by the client.

For GuardianRelationship item operations, the existing existence-hiding behavior remains canonical: unauthorized/nonexistent relationships use the same public not-found response.

## 13. Audit and transactions

People mutations follow ADR-0024:

- mutation and audit event are committed in the same transaction;
- audit failure is fail-closed;
- historical business facts are not physically deleted through ordinary People CRUD;
- lifecycle operations use the canonical audit action vocabulary already accepted for the domain.

No new audit action is introduced solely by this ADR unless separately accepted by the audit vocabulary owner.

## 14. Consequences

- People authorization is explicit and role/scope based rather than inferred from role names.
- Member and Guardian have the same self-Person edit model.
- Admin has full Person update capability in scope, including `birth_date` on their own record.
- Instructor has operational Person access only through `own_groups`.
- GuardianRelationship management is deliberately administrative; no in-system request workflow is introduced for Instructor/Guardian termination.
- Contact visibility remains controlled by the accepted Person/object policy and is not inherited from GuardianRelationship.
- Person archiving remains deferred under ADR-0034.

## 15. Required documentation reconciliation

The following canonical documents must be mechanically reconciled with this ADR before the People implementation Issues are considered documentation-complete:

- `docs/02-requirements/roles-and-permissions.md`
- `docs/05-api/people-api.md`
- `docs/05-api/endpoint-inventory.md` where applicable
- `docs/03-architecture/domain-model.md` / `data-model.md` where the accepted Person/GuardianRelationship rules are represented

Implementation agents must not reinterpret conflicting legacy wording; this ADR is the accepted decision source until reconciliation is merged.

## 16. References

- TH-0096 — People Management — specification gate
- ADR-0005 — Identity / Access Control
- ADR-0013 — Canonical Scope Vocabulary
- ADR-0024 — Audit Infrastructure
- ADR-0025 — People / Membership API decisions
- ADR-0034 — Person archiving deferred
- `docs/02-requirements/roles-and-permissions.md`
- `docs/05-api/people-api.md`
