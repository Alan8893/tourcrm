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
- `photo_file_id` nullable FK to file metadata
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

## 8. Groups

### `groups`

- `id` PK
- `club_id` FK
- `name`
- `description`
- `status`
- `valid_from`
- `valid_to`
- timestamps

### `group_memberships`

- `id` PK
- `group_id` FK
- `club_membership_id` FK
- `valid_from`
- `valid_to`
- `membership_status`
- timestamps

Rules:

- historical assignments are preserved;
- one membership can move between groups over time;
- overlapping current assignments should be rejected unless explicitly allowed.

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

Materialized occurrence records derived from a series.

- `id` PK
- `series_id` FK
- `event_id` FK nullable/required according to implementation strategy
- `occurrence_start_at`
- `occurrence_end_at`
- `status`
- `is_exception`
- timestamps

An occurrence is the unit to which attendance and operational changes attach.

### `event_participations`

- `id` PK
- `event_id` FK
- `person_id` FK
- `registration_status`
- `attendance_status` nullable until attendance exists
- `participant_role` nullable
- `registered_at` nullable
- `attendance_marked_at` nullable
- `absence_reason` nullable
- `result` nullable
- `notes` nullable
- timestamps

Constraints:

- one participation row per person/event;
- absence reason required for statuses that represent excused/unexcused absence according to business rules;
- attendance must not exist for a person without valid participation unless an administrator explicitly creates historical attendance.

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

Generic file metadata table.

- `id` PK
- `storage_key` unique
- `original_name`
- `mime_type`
- `size_bytes`
- `checksum`
- `storage_backend`
- `created_by` FK nullable
- timestamps

Binary payload is stored outside PostgreSQL unless an ADR explicitly chooses otherwise.

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
- `document_id` FK nullable
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

### `documents`

- `id` PK
- `document_type`
- `subject_type`
- `subject_id`
- `status`
- `issued_at` nullable
- `expires_at` nullable
- `file_id` FK
- `version_label` nullable
- `uploaded_by` FK
- timestamps

Polymorphic subject references require application-level integrity; for high-risk/legal documents dedicated association tables may be preferred.

### `consents`

- `id` PK
- `subject_person_id` FK
- `consent_type`
- `policy_version`
- `status`
- `given_by_person_id` FK
- `given_at`
- `revoked_at` nullable
- `document_id` FK nullable
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
- `document_id` FK nullable
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

### `audit_logs`

- `id` PK
- `occurred_at`
- `actor_user_id` FK nullable
- `action`
- `target_type`
- `target_id` nullable
- `correlation_id` nullable
- `status`
- `change_summary` / structured diff
- `ip_address` nullable
- `user_agent` nullable

Rules:

- append-only by application policy;
- secrets, passwords, access tokens and private credentials are never logged;
- high-value mutations must generate audit records.

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
- audit logs by `(target_type, target_id, occurred_at)` and `(actor_user_id, occurred_at)`;
- notifications by `(status, scheduled_at)` for workers;
- documents by `(subject_type, subject_id)` and `expires_at` where expiry reminders are enabled;
- inventory by `(club_id, availability_status)`;
- finance by `(club_id, payment_date)` / `(club_id, expense_date)`;
- full-text/search indexes only where explicitly required by module specification.

Indexes must be justified by actual query patterns; avoid indiscriminate indexing.

## 24. Referential actions

Default policy:

- historical records use `RESTRICT` or soft archival rather than destructive cascade;
- association tables may use `CASCADE` only when the child has no standalone historical meaning;
- deletion of a Person/User with historical activity must be prevented or converted to archival/anonymization flow;
- configuration/reference records may use restricted deletion or deactivation.

## 25. Soft delete and archival

Soft deletion is not a universal column added everywhere.

Use explicit lifecycle/status fields for business entities. Physical deletion is permitted only for records that have no historical/legal/audit significance and whose deletion is explicitly documented.

Where legal erasure/anonymization is required, implement a documented anonymization policy that preserves non-personal aggregate/history integrity.

## 26. Derived data

Derived values must have a declared source of truth.

Examples:

- `tourist_profiles.confirmed_distance_km` is derived from qualifying trip participation;
- dashboard counters are derived from domain data;
- debt is derived from financial obligations and payments where the finance model defines it.

Derived values may be cached/materialized, but recalculation must be possible and documented.

## 27. Migration policy

- Every schema change is an Alembic migration.
- Migrations are immutable after merge.
- No manual production-only SQL changes unless captured in a migration immediately.
- Destructive migrations require explicit review and a rollback/data-migration strategy.
- Seed/reference data must be versioned and reproducible.
- Production migrations must be safe for the deployment strategy and expected data volume.

## 28. Seed/reference data

System/reference catalogs should be distinguishable from user-generated records.

Initial reference domains include:

- roles;
- permissions;
- event types;
- membership statuses;
- attendance statuses;
- tourism types;
- difficulty categories;
- equipment statuses;
- finance statuses;
- notification channels;
- document types;
- consent types.

Reference values must have stable codes and human-readable localized names.

## 29. Open points before physical schema freeze

The following require separate ADR or module decision before Claude generates final models:

1. UUID vs another PK strategy.
2. Exact enum implementation (PostgreSQL ENUM vs lookup/reference tables vs application enums).
3. Exact naming convention singular/plural.
4. Whether `event_occurrences.event_id` is retained or Event itself represents each occurrence.
5. Medical data model and storage requirements.
6. File-storage backend implementation.
7. Exact anonymization/deletion policy.
8. Final finance accounting model.
9. Exact multi-provider identity/SSO model after TourSlet analysis.

These are intentionally not guessed by this document.
