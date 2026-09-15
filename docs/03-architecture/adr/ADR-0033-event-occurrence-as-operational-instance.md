# ADR-0033 — EventOccurrence as the Operational Event Instance

**Status:** Accepted

**Date:** 2026-09-15

**Decision owner:** Product Owner

**Scope:** Amends ADR-0028 §13's "no `event_id` bridge" for the specific case below. Amends ADR-0031's Event/EventOccurrence candidate model (§3/§4) and the Calendar projection's candidate model to the extent §6 below requires. Does not reopen or rewrite any other part of ADR-0015, ADR-0028, ADR-0029, ADR-0030 or ADR-0031.

## Context

TH-0087 (Attendance, ADR-0032 §1) requires every Attendance record to belong to a concrete `EventOccurrence`, and requires ordinary, non-recurring `Event`s to receive Attendance too. Prior to this ADR, no canonical source defined how an ordinary `Event` maps to a concrete `EventOccurrence` — ADR-0015's materialization strategy is scoped entirely to recurring series, and ADR-0028 §13 established `EventOccurrence` as having no `event_id` bridge to `Event` at all. That gap was reported rather than resolved by TH-0087's own implementation.

This ADR is the PO decision that closes that gap.

## Decision

### 1. Every Event has at least one concrete EventOccurrence

A non-recurring `Event` has **exactly one** concrete `EventOccurrence` — created together with the `Event`, in the same transaction, and never duplicated.

A recurring `EventSeries` materializes **many** `EventOccurrence`s, exactly as ADR-0015/ADR-0028 already define — unchanged by this ADR.

`EventOccurrence` is the **sole operational instance model**: the one physical representation of "a concrete, scheduled, operationally-actionable happening", whether it originated from an ordinary `Event` or from a recurring `EventSeries`.

### 2. Physical relationship

`EventOccurrence` gains a second, nullable FK:

- `event_id` — FK to `events.id`, nullable, set only for the occurrence backing a non-recurring `Event`.
- `series_id` — FK to `event_series.id`, now nullable (was `NOT NULL`), set only for a recurring occurrence.
- `CHECK`: exactly one of `event_id`/`series_id` is set.

This amends ADR-0028 §13's "not a nullable bridge to `Event` ... has no `event_id` column at all" for this one, specific case: `EventOccurrence` remains a first-class operational entity — this is not a reintroduction of a generic Event/Occurrence discriminator anywhere else, and the recurring path (`series_id`, materialization, versioning, exceptions) is completely unchanged.

`recurrence_anchor_at` (ADR-0028 §4's materialization idempotency key) is meaningless for a non-recurring occurrence — there is no RRULE position to anchor to, and no repeated materialization to make idempotent against. For the `event_id`-linked occurrence it is set equal to the Event's own `start_at` at creation and never used for idempotency purposes (the natural uniqueness boundary for this case is `UNIQUE(event_id)` instead of the `(series_id, recurrence_anchor_at)` boundary ADR-0028 defines for the recurring case).

### 3. Creation and update semantics

Creating a non-recurring `Event` creates its one `EventOccurrence` in the same transaction, with the occurrence's own snapshot fields (`name`, `description`, `event_type`, `starts_at`, `ends_at`, `timezone`) mirrored from the `Event`.

Updating the `Event`'s schedule/field-model fields (PATCH), or transitioning its status, keeps the linked `EventOccurrence`'s mirrored fields in sync in the same transaction. The occurrence's `id` never changes and is never replaced — this is an in-place sync of the *same* row, not "create a new occurrence and retire the old one." No historical occurrence row is ever deleted.

### 4. Status vocabulary mapping (technical, not a new business rule)

`Event`'s status vocabulary (`draft`, `published`, `in_progress`, `completed`, `cancelled`, `archived` — ADR-0018) and `EventOccurrence`'s status vocabulary (`scheduled`, `in_progress`, `completed`, `cancelled` — ADR-0028/ADR-0015) are not identical: `Event` has a pre-publication (`draft`) and a post-terminal (`archived`) state that `EventOccurrence`'s vocabulary has no equivalent for.

For the `event_id`-linked occurrence, the mapping is: `published -> scheduled`, `in_progress -> in_progress`, `completed -> completed`, `cancelled -> cancelled` (the exact operational-window correspondence already established by `app.events.conflicts.CONFLICT_EVENT_STATUSES`/`CONFLICT_OCCURRENCE_STATUSES` pairing `published`/`scheduled` and `in_progress`/`in_progress`). `draft` and `archived` have no occurrence-status transition of their own — see §6 for why (Calendar/Conflicts eligibility for the `event_id`-linked case reads `Event.status` directly, not the mirrored occurrence status, precisely because the occurrence vocabulary cannot represent `draft`/`archived`).

### 5. Attendance, Participation

Attendance's identity is unchanged from ADR-0032 §1: `(occurrence_id, person_id)`, no `event_id` column on `Attendance` itself, no polymorphic target. There is exactly one Attendance model. The `/events/{event_id}/attendance...` endpoints resolve `{event_id}` deterministically: if it names an `Event`, its one linked `EventOccurrence` is used; if it directly names an `EventOccurrence` (the recurring case), that occurrence is used. This is a deterministic lookup (`Event.id -> its EventOccurrence` is a real FK join, not a fallback probe across two unrelated tables), unlike the previous implementation's stopgap.

Participation eligibility (ADR-0032 §6) is unchanged: a Person must have an active `EventOccurrenceParticipant` for the concrete occurrence. For the `event_id`-linked occurrence this is the occurrence backing the ordinary Event — the same table, the same rule, no second participation model.

### 6. Calendar and Conflict Detection: EventOccurrence is the only candidate source

ADR-0031 §3 ("An ordinary Event may conflict with an EventOccurrence") and the Calendar projection's own `Event ∪ EventOccurrence` `UNION ALL` were both written before every `Event` had a linked occurrence. Once every `Event` does, that union would produce **two** candidate rows for the same real-world event — the `Event` row and its own mirrored `EventOccurrence` row — causing duplicate Calendar entries and spurious self-conflicts (the same User/Group/Person relationship matching itself across the two rows).

This ADR resolves that by making `EventOccurrence` the **only** row source for both features going forward:

- The non-recurring candidate branch now selects from `EventOccurrence` joined to `Event` via `event_id`, returning `Event.id` as the item's public identity (so existing "kind=event, id=X" consumers still navigate to `/events/{id}` unchanged) and reading eligibility from **`Event.status`** (not the mirrored occurrence status — see §4 for why: the occurrence vocabulary cannot represent `draft`/`archived`, and ADR-0031 §4 requires `draft`/`archived` Events to never participate).
- The recurring candidate branch is unchanged: selects from `EventOccurrence` joined to `EventSeries` via `series_id`, returning `EventOccurrence.id`, reading eligibility from `EventOccurrence.status`, exactly as today.
- Both branches still exist and the public response `kind`/`object_type` (`"event"` vs `"occurrence"`) is unchanged — only the physical `FROM` clause of the non-recurring branch moves from `events` to `event_occurrences JOIN events`. No new resource type, no new permission/scope, no change to the conflict domains (ADR-0031 §2) or the authorization model (ADR-0031 §8, ADR-0029).

## Consequences

Positive:

- Attendance, and any future occurrence-scoped feature, has one uniform operational identity (`EventOccurrence`) regardless of whether the underlying Event recurs.
- Calendar/Conflicts avoid the duplicate-row/self-conflict regression this ADR's own change would otherwise cause.
- The recurring path (materialization, versioning, exceptions, occurrence authorization) is completely untouched.

Trade-offs:

- `EventOccurrence` now carries a second nullable FK and a mutual-exclusivity CHECK — a deliberate, narrow amendment to ADR-0028 §13's original "no bridge" stance, not a general precedent for adding more such columns elsewhere.
- The Event/Occurrence status-vocabulary mismatch (§4) means `draft`/`archived` Events have no corresponding occurrence status transition; Calendar/Conflicts eligibility for the non-recurring branch must read `Event.status`, an asymmetry from the recurring branch's own `EventOccurrence.status` read that future maintainers must not "fix" by inventing new occurrence statuses.

## Related decisions

- ADR-0015 — Event Occurrence Materialization
- ADR-0018 — Event Lifecycle
- ADR-0028 — Event Recurrence Persistence and Versioning (§13 amended for this one case)
- ADR-0029 — Event Occurrence Authorization Relationships
- ADR-0030 — Event Series Relationship Source
- ADR-0031 — Event Conflict Detection (§3/§4 candidate model amended per §6 above)
- ADR-0032 — Event Attendance (identity unchanged; the Event→occurrence gap it reported is closed by this ADR)

## Traceability

Specification gap reported by: TH-0087 / Issue #94 implementation report.
Implementation task: TH-0087 (PR #97), continued.
