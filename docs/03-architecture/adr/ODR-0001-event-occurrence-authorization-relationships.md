# ODR-0001 — EventOccurrence authorization relationships

## Status
Open — blocks the general internal calendar read slice.

## Context

TourCRM now treats `EventOccurrence` as a first-class operational entity for recurring Events. ADR-0020 requires Event read access to remain scope-aware (`all`, `own_groups`, `own_events`, `self`, `children`) and explicitly states that role membership alone must not grant unrestricted Event visibility.

ADR-0028 and the recurrence database addendum intentionally make `EventOccurrence` independent of a required nullable `event_id` bridge. The current physical recurrence model contains the occurrence's Event snapshot, but it does not itself persist the relationships needed to derive all canonical Event scopes:

- `own_events` requires an explicit staff/responsibility relationship;
- `own_groups` requires Event ↔ Group targeting plus Group instructor responsibility;
- `self` requires EventParticipation/registration relationship;
- `children` requires EventParticipation plus active GuardianRelationship.

The recurrence schema currently states that participation, staffing and group targeting are occurrence-level operational relationships, but the corresponding occurrence-level persistence/API contract is not yet implemented in the recurrence slice.

## Problem / blocking ambiguity

A calendar projection cannot safely expose recurring occurrences to all canonical scopes without a deterministic relationship from an `EventOccurrence` to the relevant staff, groups and participants.

Copying arbitrary relationship identifiers into the occurrence snapshot would create a new authorization model unless explicitly accepted. Authorizing recurring occurrences only by `club_id` would incorrectly turn `own_groups`, `own_events`, `self`, or `children` into unrestricted club access. Requiring a nullable bridge to the legacy `Event` row would contradict ADR-0028's accepted recurrence identity model.

## Decision required

Choose the canonical relationship strategy for recurring `EventOccurrence` authorization.

At minimum the decision must determine how the following are evaluated for an occurrence:

1. `own_events` — which persisted relationship identifies the responsible User(s);
2. `own_groups` — which persisted relationship identifies the targeted Group(s);
3. `self` — which persisted relationship identifies participant visibility;
4. `children` — which persisted relationship connects the occurrence to a child's participation while preserving GuardianRelationship checks.

The decision must also state whether these relationships are:

- copied/materialized from the governing Series version;
- inherited through another canonical entity;
- attached directly to each occurrence;
- or intentionally deferred, with a correspondingly bounded calendar scope.

## Constraints

The decision must preserve:

- ADR-0013 canonical scope vocabulary;
- ADR-0020 Event authorization semantics;
- ADR-0022 cross-Club ownership integrity;
- ADR-0023 Event relationship model;
- ADR-0028 stable occurrence identity and no required nullable `event_id` bridge;
- historical integrity;
- IDOR/existence-hiding requirements;
- no new permission or scope unless separately accepted at product level.

## Non-goals

This ODR does not decide:

- calendar UI layout;
- external iCalendar feeds;
- notification delivery;
- self-registration policy;
- attendance workflow;
- new permissions;
- new scopes.

## Required resolution

After the decision is accepted:

1. update ADR-0028/database schema if the physical relationship model changes;
2. reconcile `events-and-schedule.md` and `events-api.md`;
3. complete the internal calendar specification gate;
4. create the bounded calendar implementation Issue.

Until then, no implementation may silently invent occurrence authorization relationships.
