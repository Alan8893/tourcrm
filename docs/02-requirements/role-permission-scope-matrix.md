# TourCRM — Current Role → Permission → Scope Matrix

## 1. Статус

Этот документ фиксирует утверждённую PO-политику role → permission → scope для базовых ролей TourCRM.

Он является каноническим дополнением к `docs/02-requirements/roles-and-permissions.md` и имеет приоритет над ранее сформулированными обобщёнными примерами матрицы, если они расходятся с этим документом.

Backend является единственным источником истины для authorization. Frontend visibility не является security boundary.

## 2. Базовые принципы

- Permission и scope проверяются на backend.
- Наличие роли само по себе не даёт доступа к объекту.
- Scope дополнительно ограничивается object relationship и состоянием ресурса.
- Для пользователя с несколькими ролями effective permissions являются UNION разрешений его активных role assignments.
- Grant одной роли не отменяется отсутствием grant другой роли.
- Explicit deny не используется без отдельного архитектурного решения.
- Scope `self` и `children` вычисляется backend по identity и существующим relationship, а не принимается от клиента как доверенное значение.
- `own_groups` вычисляется через фактическое назначение пользователя на группу; одна только роль `instructor` не даёт доступа ко всем участникам клуба.

## 3. Ролевая политика

### 3.1 Admin

`admin` имеет полное управление данными и функциями клуба в пределах application security model.

Для `person.update` admin может изменять **любые поля Person**, включая email и дату рождения. Это намеренное административное исключение для исправления ошибок в данных.

### 3.2 Instructor

`instructor` имеет операционный доступ к своим группам и связанным с ними объектам.

Instructor не получает административных прав на создание/удаление Person, membership lifecycle, импорт, роли, аккаунты или клубные настройки.

Instructor **не имеет `person.update`**.

### 3.3 Member

`member` работает со своими данными и связанными с ним объектами клуба.

`person.update(self)` разрешён только для следующих полей:

- `first_name`;
- `last_name`;
- `middle_name`;
- `phone`;
- `address`;
- `photo` / avatar.

Member не может изменять:

- `email`;
- `birth_date`;
- `id`;
- любые административные поля Person.

### 3.4 Guardian

`guardian` работает с данными связанных детей в пределах активной `GuardianRelationship`. Scopes определяются отдельно для каждого permission (§4 и далее); guardian не получает `person.read`/`person.update` через `self`. Scope `self` может быть добавлен конкретному permission guardian только отдельным PO decision (AUTH-2C).

`person.update(children)` разрешён для тех же полей, что и Member:

- `first_name`;
- `last_name`;
- `middle_name`;
- `phone`;
- `address`;
- `photo` / avatar.

Guardian не может изменять:

- `email`;
- `birth_date`;
- `id`;
- административные поля Person.

## 4. Person permissions

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `person.read` | `all` | `own_groups` | `self` | `children` |
| `person.create` | `all` | — | — | — |
| `person.update` | `all`, all fields | — | — | `children`, restricted fields |

### 4.1 Field-level policy for `person.update`

| Field | admin | instructor | member/self | guardian/children |
|---|---:|---:|---:|---:|
| `first_name` | ✅ | ❌ | ✅ | ✅ |
| `last_name` | ✅ | ❌ | ✅ | ✅ |
| `middle_name` | ✅ | ❌ | ✅ | ✅ |
| `phone` | ✅ | ❌ | ✅ | ✅ |
| `address` | ✅ | ❌ | ✅ | ✅ |
| `photo` / avatar | ✅ | ❌ | ✅ | ✅ |
| `email` | ✅ | ❌ | ❌ | ❌ |
| `birth_date` | ✅ | ❌ | ❌ | ❌ |
| `id` | ❌ | ❌ | ❌ | ❌ |

A request containing a field outside the caller's allowed field set must be rejected by backend authorization/validation. Frontend field visibility is not sufficient.

## 5. Membership permissions

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `membership.read` | `all` | `own_groups` | `self` | `children` |
| `membership.manage` | `all` | — | — | — |
| `membership.import` | `all` | — | — | — |

Membership lifecycle and membership type changes remain administrative operations.

## 6. Group permissions

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `group.read` | `all` | `own_groups` | own membership | children's groups |
| `group.manage` | `all` | — | — | — |

Instructor works only with groups to which the instructor is actually assigned.

## 7. Event permissions

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `event.read` | `all` | applicable `own_groups` / `own_events` | own participation | `children` |
| `event.create` | `all` | according to documented event policy and scope | — | — |
| `event.update` | `all` | assigned/owned scope | — | — |
| `event.cancel` | `all` | permission + scope | — | — |
| `event.manage` | `all` | assigned/owned scope | — | — |

### 7.1 Guardian `event.read(children)` policy

For Guardian, `event.read(children)` is defined by the dedicated Event authorization contract in ADR-0042 and ADR-0043.

An Event is readable when at least one accessible child satisfies **either** of these paths:

1. the child has an active `GroupMembership` in a Group targeted by the Event; or
2. the child has a direct `EventParticipation` for the Event.

For a Guardian with multiple accessible children, the authorized event set is the **UNION** across all children.

The same authorization semantics apply to event list, calendar, direct Event detail and recurring EventOccurrence access. Calendar/date/filter parameters may only narrow the already authorized set; they must never expand it.

For recurring occurrences, the group-membership → occurrence-group-target path is mandatory. Guardian does not need direct registration/participation merely to see a recurring group activity.

**Visibility is not registration.** A Guardian seeing a group event means that the activity is relevant to the child's group; it does not imply or modify `EventParticipation`.

The purpose of this policy is to let a Guardian see available/proposed activities and discuss participation with the child. Event visibility therefore must not depend on the child already being registered for the specific event.

The frontend must not reconstruct these relationships itself. Backend authorization remains the source of truth.

## 8. Attendance

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `attendance.read` | `all` | assigned/owned | `self` | `children` |
| `attendance.update` | `all` | assigned/owned | — | — |

## 9. Achievements

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `achievement.read` | `all` | `own_groups` | `self` | `children` |
| `achievement.award` | `all` | `own_groups` | — | — |

## 10. Documents

| Permission | admin | instructor | member | guardian |
|---|---|---|---|---|
| `document.read` | `all` | `own_groups` | `self` | `children` |
| `document.manage` | `all` | — | — | — |

Sensitive document subtypes may impose additional object-level policies.

## 11. Administrative permissions

The following permissions are admin-only under the current matrix:

- `person.create`;
- `membership.manage`;
- `membership.import`;
- `group.manage`;
- `report.read` where present in the canonical permission catalog;
- `account.manage`;
- `role.manage`;
- `settings.manage`;
- `audit.read`.

Instructor, Member and Guardian do not receive these permissions merely because they have another role or because the frontend exposes a route.

## 12. Multiple roles

Effective access is calculated as:

```text
UNION(permissions(role_assignment_1), permissions(role_assignment_2), ...)
→ scope evaluation
→ object relationship evaluation
→ resource state/policy evaluation
```

Example: a user with `instructor + guardian` receives the permissions granted by both roles, but each permission retains its own scope. `own_groups` does not become `all` because another role is present.

## 13. Backend enforcement

Every protected endpoint must enforce the effective permission and scope on the backend.

Examples of prohibited reliance on frontend restrictions:

- hiding a Person route while the API accepts another `person_id`;
- accepting a client-provided `scope=self` without deriving the subject from the authenticated user;
- accepting a client-provided child id without validating an active GuardianRelationship;
- accepting restricted Person fields from Member/Guardian because the frontend normally hides them.

A direct request with a known URL or object ID must not bypass authorization.

## 14. Single-club model

TourCRM MVP operates with exactly one Club. `ClubMembership` remains the domain membership record for that single Club; it is not a tenant selector and must not be used to introduce multi-club behavior.

---

**PO decision status:** approved 2026-09-29.
