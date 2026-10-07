# ADR-0046 — Guardian Child-to-Group Context

- **Status:** Accepted
- **Date:** 2026-10-07
- **Decision owner:** Product Owner
- **Related:** Guardian Home, Group API, `group.read(children)`, ADR-0042, ADR-0043, Issue #285

## 1. Context

The canonical UX intentionally does not give Guardian a standalone «Группы» navigation section. The product flow is contextual:

```text
Guardian
  ↓
Мои дети
  ↓
Child
  ↓
Group
```

The permission matrix already describes Guardian `group.read` as access to children's groups, but the detailed Group API contract did not yet define the `children` scope. This created a documentation/implementation GAP: the intended relationship was known, but the API authorization contract was incomplete.

## 2. Decision

Guardian receives read access to Groups through the existing `group.read` permission with the canonical `children` scope.

No new permission, scope vocabulary, role, or standalone Guardian Groups navigation item is introduced.

For a Guardian, a Group is readable when at least one currently accessible child satisfies all of the following:

1. the Guardian has an active, interval-valid `GuardianRelationship` to the child;
2. the child has an active `ClubMembership` in the same Club context;
3. the child has an active `GroupMembership` for the Group;
4. the Group has `status = active`.

For multiple children, the authorized Group set is the UNION across all accessible children.

## 3. API contract

### 3.1 Group list

`GET /api/v1/groups` uses `group.read` and supports the existing canonical scopes:

- `all`;
- `own_groups`;
- `children`.

For Guardian `children`, the backend returns only the union of active Groups reachable through the relationship chain defined in §2.

Client-supplied child/person IDs do not establish authorization and are not required to resolve the Guardian's child-scoped Group set.

### 3.2 Group detail

`GET /api/v1/groups/{group_id}` uses the same object authorization.

A Guardian may retrieve a Group only if at least one currently accessible child has an active `GroupMembership` in that Group and the Group is active.

Unauthorized or foreign Groups continue to use the existing existence-hiding behavior and return the canonical not-found response.

### 3.3 Child projection

The canonical `GET /api/v1/me/children` projection includes a compact `groups[]` read-only summary for each accessible child:

- `id`;
- `name`.

This projection exists to support the contextual Home flow. It is not a second Group resource and does not bypass Group authorization. Opening Group Detail must perform the normal backend authorization check again.

Only active current GroupMembership records and active Groups are included.

## 4. UX contract

Guardian navigation remains:

- Home;
- Events;
- Achievements;
- Settings.

There is no Guardian Groups navigation item.

On Guardian Home, «Мои дети» is the entry point to group context. Each child may show their current active Groups as compact links/chips. Selecting a Group opens the existing Group Detail.

For Guardian, Group Detail exposes only:

- **Расписание**.

Guardian does not receive through this decision:

- Group roster / participant list;
- Group membership management;
- Instructor assignment management;
- Group create/update/archive;
- any Group administration controls.

Frontend visibility is not the security boundary; backend authorization remains authoritative.

## 5. Lifecycle and history

Archived Groups are excluded from Guardian's current Group set and are not directly readable through the Guardian `children` scope.

Ended/historical GroupMembership does not establish current Guardian Group access.

This ADR does not change the existing ODR-0002 schedule semantics for Group schedule history. It defines only which Groups are currently reachable through Guardian's contextual Group flow.

## 6. Security invariants

The following remain mandatory:

- cross-Club access is denied;
- revoked or expired GuardianRelationship does not grant access;
- inactive/ended child ClubMembership does not grant access;
- ended GroupMembership does not grant access;
- Group UUID alone is never proof of access;
- frontend filtering must never be used as authorization;
- a Guardian with multiple children receives only the union of Groups reachable through those children.

## 7. Non-goals

This ADR does not:

- add a new permission or scope;
- create a new Group endpoint;
- add a standalone Guardian Groups page;
- grant Guardian access to Group participants;
- change Event visibility policy;
- change Event registration/self-registration policy;
- decide whether an archived Group should continue to expose already-targeted Events to Guardian.

## 8. Canonical reconciliation

The following documents are authoritative together:

- `docs/02-requirements/role-permission-scope-matrix.md`;
- `docs/04-ux/information-architecture.md`;
- `docs/05-api/people-api.md`;
- `docs/05-api/endpoint-inventory.md`.

The Endpoint Inventory is unchanged because this decision extends authorization semantics of existing Group endpoints and `GET /me/children); no new endpoint is introduced.

## 9. Implementation boundary

Implementation must be delivered as a separate Issue/PR after this specification change.

The implementation must add backend-authoritative `children` Group authorization and the `groups[]` child projection, plus the contextual Guardian Home links. It must not introduce unrelated Guardian navigation or Group permissions.

