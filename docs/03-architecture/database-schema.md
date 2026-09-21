# TourCRM — Database Schema Specification

## 1. Назначение

Документ определяет логический и физический контракт базы данных TourCRM. Он является источником требований для PostgreSQL, SQLAlchemy models и Alembic migrations.

Документ не является готовой SQL-схемой. Claude должен реализовать физическую схему строго по этому контракту и не добавлять доменные поля без соответствующего изменения документации или ADR.

## 2. Database technology

- PostgreSQL — canonical relational database.
- SQLAlchemy — ORM/data access layer.
- Alembic — schema migration tool.
- PostgreSQL timezone-aware timestamps (`timestamptz`) должны использоваться для событий, аудита и системных дат, где важна абсолютная временная точка.
- Денежные значения не хранятся в floating point; использовать `numeric/decimal`.
- UUID рекомендуется использовать как внешне неугадываемый идентификатор публичных сущностей. Внутренний strategy должен быть единообразно зафиксирован в implementation ADR.

## 3. Naming conventions

- Таблицы: `snake_case`, plural или singular — выбрать единый стиль в implementation ADR и не смешивать.
- Колонки: `snake_case`.
- Foreign keys: `<entity>_id`.
- Boolean: `is_*` / `has_*` или иной единый стиль.
- Enum values должны быть стабильными и не зависеть от UI-текста.
- API naming может отличаться от DB naming только по документированному convention.

## 4. Cross-cutting columns

Для большинства управляемых сущностей:

- `id` — primary key;
- `created_at` — required;
- `updated_at` — required;
- `created_by` — nullable FK, если есть понятие инициатора;
- `updated_by` — nullable FK, если есть понятие последнего редактора;
- `status` — только если сущность имеет lifecycle.

Не следует автоматически добавлять все поля во все таблицы.

## 5. Core identity model

### 5.1 `clubs`

Назначение: клуб-владелец данных.

Основные поля:

- `id` PK
- `name` required
- `short_name` nullable
- `description` nullable
- `status` required
- `created_at`
- `updated_at`

Constraints:

- `name` unique within installation.

### 5.2 `persons`

Назначение: физическое лицо.

Поля:

- `id` PK
- `last_name` required
- `first_name` required
- `middle_name` nullable
- `birth_date` nullable/required according to product policy for member records
- `phone` nullable
- `email` nullable
- `address` nullable
- `photo_file_id` nullable, planned FK to `files.id` (§11) — as of this writing (ADR-0040) it exists in code as a plain nullable UUID column with **no** FK constraint, because the `files` table does not exist yet; adding the constraint is a separate future implementation task, not this ADR
- `created_at`
- `updated_at`

Sensitive/optional health data must not be placed here unless explicitly defined by a dedicated medical domain model.

### 5.3 `users`

Назначение: authentication identity.

Поля:

- `id` PK
- `person_id` unique nullable/required depending on service-account policy
- `login_identifier` required, normalized
- `password_hash` nullable for future external-auth providers
- `status` required
- `email_verified_at` nullable
- `last_login_at` nullable
- `created_at`
- `updated_at`

Constraints:

- normalized login identifier unique;
- plaintext passwords forbidden.

### 5.4 `club_memberships`

Связывает Person с Club и хранит историческое членство.

Поля:

- `id` PK
- `club_id` FK
- `person_id` FK
- `membership_type` required
- `status` required
- `joined_at` required
- `left_at` nullable
- `created_at`
- `updated_at`

Constraints:

- `left_at >= joined_at` when set;
- overlapping active memberships of the same type should be prohibited unless explicitly allowed by business rules.

## 6. Roles and permissions

### 6.1 `roles`

- `id` PK
- `code` unique
- `name`
- `description`
- `is_system` boolean

### 6.2 `permissions`

- `id` PK
- `code` unique, e.g. `event.read`
- `description`

### 6.3 `role_permissions`

- `role_id` FK
- `permission_id` FK
- PK (`role_id`, `permission_id`)

### 6.4 `user_role_assignments`

- `id` PK
- `user_id` FK
- `role_id` FK
- `club_id` FK nullable if global role is supported
- `scope_type` required
- `scope_ref_id` nullable
- `created_at`
- `updated_at`

Rules:

- role assignment is distinct from membership;
- same user may have multiple roles;
- scope resolution is performed by authorization layer;
- assignment history must remain auditable.

## 7. Guardians and family relations

### `guardian_relationships`

Fields:

- `id` PK
- `guardian_person_id` FK -> persons
- `child_person_id` FK -> persons
- `relationship_type` required
- `is_primary_contact` boolean
- `status`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

Constraints:

- guardian and child cannot be identical;
- one child may have multiple guardians;
- one guardian may have multiple children;
- primary-contact semantics must be enforced at business/DB layer according to chosen rule.

API-level semantics (ADR-0025 §3): the `terminate` action always sets `status = revoked`; `inactive` is a distinct, non-revoked historical status reached through other lifecycle events (e.g. `valid_to` naturally elapsing), never through `terminate`. Canonical API resource/permission naming for this entity (`guardian-relationships`, `guardian_relationship.read`/`guardian_relationship.manage`) is defined by ADR-0025 §2/§4.

## 8. Groups

### `groups`

A Group is a standalone domain entity owned by exactly one Club.

Fields:

- `id` PK
- `club_id` FK -> clubs
- `name`
- `description` nullable
- `status`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

Rules:

- `valid_to >= valid_from` when `valid_to` is set;
- Group lifecycle/status vocabulary is intentionally not closed by ADR-0021;
- Group-related objects must not cross Club boundaries.

### `group_memberships`

Historical association between a `ClubMembership` and a `Group`.

Fields:

- `id` PK
- `group_id` FK -> groups
- `club_membership_id` FK -> club_memberships
- `valid_from`
- `valid_to` nullable
- `membership_status`
- `created_at`
- `updated_at`

Rules:

- `person_id` is not stored; resolve the Person through `club_membership_id -> ClubMembership.person_id`;
- `membership_status` is the canonical field name, not generic `status`;
- `is_primary` is not part of this persistence model;
- `assigned_by` is not a GroupMembership domain field;
- historical assignments are preserved;
- `Group.club_id` must equal `ClubMembership.club_id` on authoritative writes;
- whether simultaneous membership in multiple groups is allowed is a separate business-policy decision and is not constrained here merely by this schema contract.

### `group_instructor_assignments`

Explicit historical responsibility of an authenticated User for a Group.

Fields:

- `id` PK
- `group_id` FK -> groups
- `user_id` FK -> users
- `role_in_group`
- `is_primary`
- `valid_from`
- `valid_to` nullable
- `created_at`
- `updated_at`

Rules:

- `user_id` is used because authorization responsibility belongs to the authenticated User principal;
- `role_in_group` remains a plain string; its canonical vocabulary is not closed until reconciliation with the Event responsibility model;
- `is_primary` distinguishes the primary responsible instructor from other explicit assignments;
- historical assignments are preserved;
- an assignment is valid only when the User's Person has an active `ClubMembership` in the Group's Club;
- a global `instructor` role is not sufficient to establish group responsibility;
- these Club-ownership rules are application/service-layer invariants under ADR-0022, not database triggers or redundant `club_id` columns.

### Group ownership integrity

The authoritative write boundary must validate Group relationship ownership before writing and within the same transaction as the write. The shared application/service mechanism defined by ADR-0022 is the required enforcement point.

Database-level referential integrity remains responsible for ordinary foreign keys, deletion protection and physical constraints. It must not be replaced by caller-specific ownership checks.

Future Event-to-Group targeting must likewise enforce `Event.club_id == Group.club_id`; `Event.created_by` is never a substitute for this relationship.

## 9. Events and schedule

### `events`

Core fields:

- `id` PK
- `club_id` FK
- `event_type` required
- `title` required
- `description` nullable
- `start_at` required
- `end_at` required
- `timezone` required
- `location_type`
- `location_name`
- `location_address` nullable
- `location_latitude` nullable
- `location_longitude` nullable
- `status` required
- `cancellation_reason` nullable
- `created_by` FK
- `updated_by` FK
- timestamps

Constraints:

- `end_at > start_at`;
- cancellation reason required for cancelled events;
- completed events should not be silently mutated in ways that invalidate attendance/history.

### `event_series`

For recurring schedules.

- `id` PK
- `club_id` FK
- `event_type` required
- `title` required
- `timezone` required
- `rrule` required
- `series_start_at` required
- `series_end_at` nullable
- `status`
- timestamps

### `event_occurrences`

Materialized occurrence records — from a recurring series, or 1:1 with
an ordinary, non-recurring Event (ADR-0033, resolving what this section
used to leave open as "nullable/required according to implementation
strategy"):

- `id` PK
- `series_id` FK, nullable — set only for the recurring case
- `event_id` FK, nullable — set only for the non-recurring case (created
  together with the Event and kept in sync with it, app.events.crud)
- CHECK: exactly one of `series_id`/`event_id` is set
- `starts_at`/`ends_at` (current effective schedule)
- `recurrence_anchor_at` (materialization idempotency key — recurring
  case only; for the non-recurring case it carries no idempotency
  meaning, see database-schema-recurrence.md §2)
- `status`
- timestamps

`UNIQUE(event_id)` (at most one occurrence per Event) and
`UNIQUE(series_id, recurrence_anchor_at)` (the existing recurring
materialization boundary) coexist without conflict — PostgreSQL treats
every NULL as distinct, so neither constraint is triggered by rows
belonging to the other case.

An occurrence is the unit to which attendance and operational changes attach — for every Event, recurring or not (ADR-0033).

### `event_participations`

Currently accepted persistence model (Issue #51, per ADR-0023 §4):

- `id` PK
- `event_id` FK
- `person_id` FK
- `registration_status`
- timestamps

Constraints (implemented):

- `UNIQUE(event_id, person_id)` — at most one participation row per Event/Person, enforced by PostgreSQL, not application code;
- `registration_status` is a plain string with no CHECK/enum constraint. ADR-0020 §4's five values (`invited`, `registered`, `waitlisted`, `declined`, `removed`) are documented reference values only, not an enforced vocabulary; the full transition graph and registration policy remain a separate deferred business decision.

#### Deferred (not implemented) concepts

`participant_role`, `registered_at`, `result`, `notes` remain possible future `event_participations` attributes and are **not** part of the currently implemented model; the full `registration_status` transition graph and registration policy also remain a separate deferred business decision. `attendance_status`/`attendance_marked_at`/`absence_reason` are no longer deferred: ADR-0032 (Issue #94 / TH-0087) resolved Attendance as its own separate table (`attendance`, not an `event_participations` column) — see `### attendance` below and `domain-model.md` §11 "Attendance".

### `attendance`

Canonical model per ADR-0032 §1 (Issue #94 / TH-0087) — occurrence-only identity, exactly as accepted:

- `id` PK
- `occurrence_id` FK to `event_occurrences.id`, `NOT NULL`
- `person_id` FK
- `status` — exactly `present`/`absent`
- `absence_reason` — nullable, closed vocabulary (`sick`, `family_reason`, `injury`, `education`, `work`, `other`), not club-configurable
- `comment` — nullable, allowed only for `absent`
- timestamps

Constraints (implemented):

- `UNIQUE(occurrence_id, person_id)` — the single ordinary DB constraint ADR-0032 §1 requires;
- `present` requires both `absence_reason` and `comment` to be `NULL` (CHECK).

No `event_id` column on `attendance` itself and no per-object-type
discriminator: an earlier draft of this implementation added a second
nullable `event_id` FK directly on `Attendance` to also support
ordinary, non-recurring `Event`s. That was reverted on review — it
re-decided ADR-0032's canonical identity rather than resolving a
technical detail. **Resolved instead by ADR-0033** at the
`event_occurrences` level (see `### event_occurrences` above): every
ordinary Event now has exactly one linked `EventOccurrence`
(`event_occurrences.event_id`), so `attendance.occurrence_id` reaches an
ordinary Event's attendance the same way it reaches a recurring one's —
through that one FK column, unchanged, with no Attendance-level
polymorphism at all.

No `valid_from`/`valid_to`: unlike `EventGroupTarget`/`EventStaffAssignment`/`EventParticipation`, Attendance is a single current mark per occurrence/Person pair, corrected in place (last-write-wins, ADR-0032 §12) rather than a historical relationship timeline.

### `event_document_requirements` — planned (ADR-0040 §5, TH-0117)

Expresses that an Event requires a document type from its participants, without coupling `Document` directly to `Event`. See §15.2 for the full field list and constraints — listed there alongside `documents` (§15.1) since both are introduced together by ADR-0040.

## 10. Trips

### `trips`

Extension of Event.

- `event_id` PK/FK
- `tourism_type`
- `difficulty_category`
- `region`
- `route_id` FK nullable
- `planned_distance_km` numeric
- `actual_distance_km` numeric nullable
- `planned_duration_minutes` integer nullable
- `actual_duration_minutes` integer nullable
- `leader_person_id` FK nullable
- `result_status`
- `notes`

### `trip_participants`

- `id` PK
- `trip_id` FK
- `person_id` FK
- `role_in_trip`
- `participation_status`
- `completed_distance_km` numeric nullable
- `result` nullable
- `notes`
- timestamps

Constraints:

- one row per person/trip;
- completed distance cannot be negative;
- participation facts are not inferred solely from attendance.

## 11. Routes and geodata

### `routes`

- `id` PK
- `club_id` FK nullable
- `name`
- `tourism_type`
- `region`
- `description`
- `planned_distance_km` numeric nullable
- `elevation_gain_m` numeric nullable
- `status`
- timestamps

### `route_points`

- `id` PK
- `route_id` FK
- `sequence` integer
- `name` nullable
- `point_type` nullable
- `latitude` numeric
- `longitude` numeric
- `elevation_m` numeric nullable
- `description` nullable

Constraints:

- sequence unique within route;
- latitude in [-90,90]; longitude in [-180,180].

### `files`

Generic, domain-neutral file metadata table — the canonical `File` entity of ADR-0040 §1. Immutable once created: content is never overwritten in place, and no column here is mutated by a document replace (ADR-0040 §4 creates a new `files` row instead).

- `id` PK
- `storage_key` unique
- `original_name`
- `mime_type`
- `size_bytes`
- `checksum`
- `storage_backend`
- `created_by` FK nullable
- timestamps

Binary payload is stored outside PostgreSQL unless an ADR explicitly chooses otherwise. Access is only through the `FileStorage` port (ADR-0040 §3) behind an authorized application endpoint; `storage_key` is never a public URL.

Shared by `route_files` below, participant `documents` (§15), and — once a later implementation task adds the FK — `persons.photo_file_id` (§5.2).

### `route_files`

- `route_id` FK
- `file_id` FK
- `file_type` (gpx/source/preview/etc.)
- PK (`route_id`, `file_id`, `file_type`)

## 12. Tourist profile

### `tourist_profiles`

One profile per club membership where needed.

- `id` PK
- `club_membership_id` unique FK
- `confirmed_trip_count` integer
- `confirmed_distance_km` numeric
- `last_calculated_at`
- timestamps

Important: aggregates are derived data. Canonical facts reside in trip-related records.

### `tourist_profile_tourism_types`

Association between profile and tourism type.

### `skills`

- `id` PK
- `club_id` nullable
- `code` unique per scope
- `name`
- `description`
- `is_active`

### `person_skills`

- `id` PK
- `person_id` FK
- `skill_id` FK
- `level` nullable
- `status`
- `verified_at` nullable
- `verified_by` FK nullable
- timestamps

### `qualifications`

- `id` PK
- `person_id` FK
- `qualification_type`
- `name`
- `level`
- `issued_at`
- `valid_until` nullable
- `document_id` FK nullable, -> `documents.id` (§15.1, ADR-0040)
- `status`
- timestamps

## 13. Achievements

### `achievements`

- `id` PK
- `club_id` FK nullable
- `code` unique per scope
- `name`
- `description`
- `category`
- `award_mode` (`manual` / `automatic`)
- `rule_definition` nullable
- `is_active`
- timestamps

### `achievement_awards`

- `id` PK
- `achievement_id` FK
- `person_id` FK
- `awarded_at`
- `awarded_by` FK nullable
- `source_type` nullable
- `source_id` nullable
- `notes`
- timestamps

Constraints:

- duplicate award semantics must be explicitly defined; default is one active award of the same achievement per person unless repeatable achievement is configured.

## 14. Knowledge base

### `knowledge_categories`

- `id` PK
- `club_id` FK nullable
- `name`
- `slug`
- `description`
- `parent_id` FK nullable
- `sort_order`
- `is_active`

### `knowledge_articles`

- `id` PK
- `category_id` FK
- `slug` unique within publication scope
- `title`
- `summary`
- `status`
- `author_person_id` FK
- `published_at` nullable
- `current_version_id` nullable FK
- timestamps

### `knowledge_article_versions`

- `id` PK
- `article_id` FK
- `version_number`
- `content`
- `change_summary`
- `created_by` FK
- `created_at`

Constraints:

- version number unique per article;
- published version is immutable; correction creates a new version.

## 15. Documents and consents

The generic `documents` shape previously sketched here (a single table keyed by a polymorphic `subject_type`/`subject_id` pair) is **superseded for the participant-document case by ADR-0040** — see §15.1 below. That polymorphic shape remains an unresolved question for any other future document-owning domain (Trip, Equipment, Finance, Club-level documents); this document no longer proposes it as the mechanism for Person-owned documents.

### 15.1 `documents` (participant documents — ADR-0040)

Canonical shape for a Person-owned document (e.g. `medical_certificate`), per ADR-0040 §1/§2/§4. Explicit FK association, never polymorphic, for this case:

- `id` PK
- `document_group_id` — stable identity shared by every version of the same logical document; equal to the first version's own `id`
- `version_number` — positive integer, starts at 1, strictly increasing per replace within a `document_group_id`
- `person_id` FK -> `persons.id`
- `document_type` — open string; TH-0117 requires at least `medical_certificate`; no closed vocabulary is introduced
- `status` — CHECK, closed vocabulary `active` / `expired` / `revoked` (ADR-0040 §4)
- `issued_at` nullable
- `expires_at` nullable
- `file_id` FK -> `files.id` (§11), `RESTRICT` — a `File` still referenced by document history must not be deleted out from under it
- `uploaded_by` FK -> `users.id`, nullable
- timestamps

Constraints:

- `UNIQUE(document_group_id, version_number)`;
- the **current** version of a logical document is the row with `MAX(version_number)` for its `document_group_id` — no separate "is current" boolean is stored, to avoid a flag that could drift out of sync;
- replacing a document's file creates a new row (new `file_id`, `version_number = previous + 1`, same `document_group_id`) rather than mutating the prior version's `file_id` in place (ADR-0040 §1/§4);
- `revoked` is applied to the current version in place and does not create a new version;
- current validity for `EventDocumentRequirement` checks (§15.2) is computed at read time from `status` + `expires_at`, exactly like `GuardianRelationship`'s read-time expiry (§7) — no background job flips `status` to `expired`.

`missing` is never a value of `status` — it exists only as a possible *result* of the `EventDocumentRequirement` check in §15.2, when no `documents` row of the required `document_type` exists for a Person at all.

### 15.2 `event_document_requirements` (ADR-0040 §5)

Expresses that an Event requires a given document type from its participants. Does **not** associate `documents` directly with `events` — see ADR-0040 §5 for why.

- `id` PK
- `event_id` FK -> `events.id`
- `document_type` — open string, matching `documents.document_type`
- `required` boolean
- timestamps

Constraints:

- `UNIQUE(event_id, document_type)` — at most one requirement row per Event/document-type pair.

Checking a requirement against a specific Person's documents (`valid` / `missing` / `expired`, ADR-0040 §5) is a read-only query, not a persisted row.

### `consents`

- `id` PK
- `subject_person_id` FK
- `consent_type`
- `policy_version`
- `status`
- `given_by_person_id` FK
- `given_at`
- `revoked_at` nullable
- `document_id` FK nullable, -> `documents.id` (§15.1, ADR-0040) — Consent's own subject/ownership model (`subject_person_id`) is unaffected; only the type of the referenced evidence document changes
- timestamps

## 16. Equipment

### `equipment`

- `id` PK
- `club_id` FK
- `inventory_code` unique within club
- `name`
- `category`
- `serial_number` nullable
- `condition_status`
- `availability_status`
- `storage_location` nullable
- `acquired_at` nullable
- `acquisition_cost` numeric nullable
- timestamps

### `equipment_issues`

- `id` PK
- `equipment_id` FK
- `issued_to_person_id` FK
- `event_id` FK nullable
- `issued_at`
- `expected_return_at` nullable
- `returned_at` nullable
- `condition_before`
- `condition_after` nullable
- `notes`
- timestamps

Rules:

- equipment cannot have overlapping active issues unless specifically configured as divisible/consumable inventory;
- returns close an issue record instead of deleting it.

## 17. Finance

### `financial_accounts`

- `id` PK
- `club_id` FK
- `name`
- `account_type`
- `currency`
- `status`
- timestamps

### `payments`

- `id` PK
- `club_id` FK
- `person_id` FK nullable
- `event_id` FK nullable
- `account_id` FK
- `amount` numeric(precision, scale)
- `currency`
- `payment_date`
- `payment_method`
- `external_reference` nullable
- `status`
- `notes`
- created/updated metadata

### `expenses`

- `id` PK
- `club_id` FK
- `event_id` FK nullable
- `account_id` FK
- `amount`
- `currency`
- `expense_date`
- `category`
- `vendor` nullable
- `description`
- `document_id` FK nullable — a finance-domain (receipt/invoice) document, not a participant document; the `documents` table defined in §15.1 (ADR-0040) is Person-owned (`person_id` `NOT NULL`) and does not fit this case. Finance document ownership remains an open question, unresolved by ADR-0040 (see its "Non-decisions" section)
- `status`
- timestamps

### `event_budgets`

- `event_id` PK/FK
- `planned_amount`
- `currency`
- `status`
- timestamps

### `event_expenses`

Association/details between event and expense when an expense can be allocated separately.

- `event_id` FK
- `expense_id` FK
- `allocated_amount`
- PK (`event_id`, `expense_id`)

Money operations must be auditable and not silently overwritten after posting.

## 18. Notifications and communications

### `notification_templates`

- `id` PK
- `code` unique
- `channel`
- `locale`
- `subject_template` nullable
- `body_template`
- `version`
- `is_active`
- timestamps

### `notification_rules`

- `id` PK
- `club_id` FK nullable
- `event_type`
- `channel`
- `recipient_scope`
- `is_enabled`
- scheduling parameters
- timestamps

### `notifications`

- `id` PK
- `user_id` FK nullable
- `channel`
- `template_id` FK nullable
- `trigger_type`
- `trigger_id` nullable
- `status`
- `scheduled_at` nullable
- `sent_at` nullable
- `retry_count`
- `provider_message_id` nullable
- `last_error` nullable
- timestamps

### `communication_preferences`

- `id` PK
- `user_id` FK
- `channel`
- `notification_type`
- `enabled`
- quiet-hours configuration where applicable
- timestamps

## 19. Audit

Canonical contract: ADR-0024 (closes ODR-015).

### `audit_logs`

- `id` PK (UUID, immutable, ADR-0010)
- `occurred_at` `timestamptz`, required, server-generated at insert
- `actor_type` required, CHECK `IN ('user','system')`
- `actor_user_id` FK -> `users.id` (`RESTRICT`), nullable — required when `actor_type='user'`, forbidden (NULL) when `actor_type='system'`; no synthetic "System" `User` row is created
- `club_id` FK -> `clubs.id` (`RESTRICT`), nullable — event context only, never an authorization mechanism
- `action` required, CHECK restricted to the closed vocabulary in ADR-0024 §4 — a stable business action code, never an HTTP method/URL/UI text
- `resource_type` nullable
- `resource_id` nullable, opaque UUID (ADR-0010) — not necessarily an FK, since targets span many tables
- `outcome` required, CHECK `IN ('success','failure')`
- `request_id` nullable — the existing per-request correlation value (`app.api.request_context`)
- `correlation_id` nullable — application-supplied identifier linking multiple audit records to one broader business operation; no new distributed-tracing infrastructure populates it
- `details` `jsonb`, nullable — only explicit, hand-built safe data

No `created_at`/`updated_at`/`created_by`/`updated_by`: `occurred_at` is the only timestamp this immutable record needs.

Constraints:

- `ck_audit_logs_actor_type_valid` — `actor_type IN ('user','system')`;
- `ck_audit_logs_actor_user_id_consistent` — `(actor_type='user' AND actor_user_id IS NOT NULL) OR (actor_type='system' AND actor_user_id IS NULL)`;
- `ck_audit_logs_outcome_valid` — `outcome IN ('success','failure')`;
- `ck_audit_logs_action_valid` — `action` restricted to ADR-0024 §4's closed vocabulary;
- `ck_audit_logs_resource_consistent` — `(resource_type IS NULL) = (resource_id IS NULL)`.

Indexes: `occurred_at`; `actor_user_id`; `club_id`; `(resource_type, resource_id)`; `request_id`.

Rules:

- append-only by application policy — the reusable write boundary (`app.audit.service.record_audit_event`) only inserts; no update/delete operation is provided;
- passwords, password hashes, access/refresh/session/reset/verification/invitation tokens, API keys, cookies, `Authorization` header values and other credentials/secrets are never stored in `details`, at any nesting depth — rejected outright, never masked;
- `details` must be an explicit, hand-built JSON-safe payload; an ORM entity, HTTP request/response object or `Session` is never automatically serialized into it;
- the audit insert happens in the same database transaction as the business mutation it documents for audit-required operations; if the audit insert fails, the whole transaction (including the business mutation) is rolled back (fail-closed) — see ADR-0024 §5;
- no asynchronous audit delivery is implemented;
- high-value mutations must generate audit records, restricted at this stage to ADR-0024 §4's action vocabulary;
- retention/deletion is intentionally undefined — see ODR-013 and `docs/03-architecture/data-retention-and-deletion.md`; no TTL/retention job/automatic cleanup exists.

## 20. System and feature settings

### `system_settings`

- `id` PK
- `club_id` FK nullable
- `key` stable unique key
- `value_json`
- `updated_by` FK nullable
- timestamps

Examples:

- `rating.enabled`
- `achievements.enabled`
- `finance.enabled`
- `telegram.enabled`
- `max.enabled`
- `email.enabled`

Security permissions must never be bypassed through settings.

## 21. Registration and invitations

### `registration_requests`

- `id` PK
- `requested_email` / identifier
- `person_data_snapshot`
- `requested_role`
- `club_id` FK
- `status`
- `reviewed_by` FK nullable
- `reviewed_at` nullable
- `rejection_reason` nullable
- timestamps

### `invitations`

- `id` PK
- `club_id` FK
- `token_hash`
- `email` nullable
- `role` nullable
- `expires_at`
- `used_at` nullable
- `revoked_at` nullable
- `created_by` FK
- timestamps

Raw invitation tokens are never stored after generation; only a secure hash/reference is persisted.

## 22. External integrations

### `external_identities`

Associates local users/persons with external providers.

- `id` PK
- `user_id` FK
- `provider`
- `external_subject`
- `metadata_json` nullable
- timestamps

Unique constraint on (`provider`, `external_subject`).

### `integration_records`

Optional generic mapping table for external objects where required.

- `id` PK
- `integration_name`
- `local_entity_type`
- `local_entity_id`
- `external_entity_type`
- `external_entity_id`
- `sync_status`
- `last_synced_at`
- `last_error`
- timestamps

Do not add this generic table solely for convenience if a domain-specific mapping provides stronger integrity.

## 23. Indexing strategy

Required index categories:

- all foreign keys used in joins/filtering;
- active membership queries by `club_id`, `person_id`, `status`;
- events by `(club_id, start_at)`;
- event participations by `(event_id, person_id)` unique;
- group memberships by `(group_id, valid_from, valid_to)` as operationally required;
- group instructor assignments by Group and User as operationally required;
- audit logs by `occurred_at`, `actor_user_id`, `club_id`, `(resource_type, resource_id)` and `request_id` (ADR-0024);
- document expiration;
- notification delivery state.

## 24. Ownership integrity and transaction boundary

The Club ownership invariant is part of the data contract, even where it cannot be represented by a simple foreign key.

For Group relationships:

- `GroupMembership` is valid only when `Group.club_id == ClubMembership.club_id`;
- `GroupInstructorAssignment` is valid only when the assigned User's Person has an active `ClubMembership` in `Group.club_id`;
- ownership validation and write must occur in the same transaction;
- concurrent changes to the rows on which the decision depends must be protected according to ADR-0022;
- the shared application/service ownership validator is authoritative;
- no DB trigger, redundant `club_id`, or `Event.created_by` inference is used as a substitute.

For future Event-to-Group targeting, `Event.club_id == Group.club_id` is mandatory.

See ADR-0021 and ADR-0022 for the normative Group persistence and cross-Club ownership decisions.
