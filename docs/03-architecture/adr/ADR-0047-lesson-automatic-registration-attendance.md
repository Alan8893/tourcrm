# ADR-0047: Lesson automatic registration, attendance workflow and calendar attendance indication

## Status

Accepted

## Context

TourCRM uses three deliberately separate facts:

- Group membership — a person's membership in a Group;
- EventParticipation — registration for an Event;
- Attendance — the fact that a person was present or absent at a concrete EventOccurrence.

The generic Event policy from ADR-0037 intentionally does not infer registration from Group targeting. However, a regular club lesson has a different operational meaning: creating a lesson for a Group means that the current members of that Group are the expected participants of that lesson. The product therefore needs an explicit lesson-specific exception without changing the general Event model.

The existing Attendance model from ADR-0032 already supports `present`, `absent` and unmarked participants, with Administrator/Instructor authorization through `attendance.update`.

## Decision

### 1. Lesson automatic registration

For `Event.event_type = lesson` with one or more target Groups:

1. At Event creation, every person who has an active `GroupMembership` in at least one targeted Group at that moment receives an `EventParticipation` for the lesson with `registration_status = registered`.
2. Duplicate participation rows are prohibited by the existing `(event_id, person_id)` invariant.
3. The operation is part of the same transaction as Event creation. If target validation or automatic participation creation fails, the Event creation fails atomically.
4. A person joining the Group after the lesson was created is not automatically added to the existing lesson.
5. A person leaving the Group after the lesson was created is not automatically cancelled from the existing lesson.
6. Automatic registration does not create Attendance.
7. Registration does not imply presence.

For all other Event types, ADR-0037 remains authoritative: Group targeting does not automatically create EventParticipation.

### 2. Lesson participant semantics

The automatically created registration represents the expected participant roster for the lesson. The existing `registered` / `cancelled` participation lifecycle remains authoritative.

Self-registration remains available according to ADR-0037 where applicable. It must not create duplicate participation.

Guardian self-registration of a child is not introduced by this decision.

### 3. Attendance

Attendance uses the existing ADR-0032 model without changing its domain semantics:

- `present`;
- `absent`;
- unmarked (`Attendance` does not yet exist).

Administrator and Instructor with the applicable `attendance.update` authorization may mark or change attendance for eligible lesson participants.

The UI must make the distinction between unmarked and absent explicit.

Bulk attendance uses the existing canonical bulk API and semantics.

Attendance remains occurrence-based and is never created merely because automatic lesson registration occurred.

### 4. Calendar indication for Member

For a Member viewing their own calendar, a lesson's attendance indication is:

- `present` → green;
- `absent` → red;
- no Attendance yet → neutral/default event presentation.

`unmarked` must never be rendered as absent.

The indication is derived from the canonical Attendance state; the frontend must not invent or persist a second attendance state.

### 5. Calendar indication for Guardian

For a Guardian viewing an Event through the existing `children` visibility policy, attendance indication is aggregated only over the accessible children of that Guardian who are participants of the Event.

Priority is:

`present > absent > unmarked`

Therefore:

- at least one accessible child is `present` → green;
- no accessible child is `present`, and at least one accessible child is `absent` → red;
- all accessible child attendance is unmarked → neutral/default.

The Guardian must never receive attendance data about unrelated participants or children outside the canonical `children` authorization scope.

If multiple accessible children have mixed attendance (`present` + `absent`), the Guardian sees green.

### 6. Staff calendar indication

Administrator and Instructor calendar views do not use one global green/red event color derived from all participants, because one Event may contain mixed attendance.

Staff views may show an attendance summary indicator such as `15/18`, while the participant list remains the authoritative detailed attendance UI.

### 7. Attendance editing lifecycle

The existing ADR-0032 lifecycle remains unchanged:

- normal attendance mutation while the occurrence is `scheduled` or `in_progress`;
- completed occurrences require the existing correction workflow;
- cancelled occurrences do not receive attendance.

### 8. Authorization and security

No new permission or scope is introduced.

Existing authorization remains authoritative:

- Event participant visibility follows EventParticipation/Event authorization;
- attendance read uses `attendance.read`;
- attendance mutation uses `attendance.update`;
- Guardian calendar aggregation uses only children visible under the existing Guardian policy.

Cross-Club and IDOR protections remain unchanged.

## Consequences

- A lesson immediately has a deterministic participant roster based on the Group membership snapshot at creation.
- Attendance can be recorded directly against that roster without requiring separate registration actions.
- The same canonical EventParticipation and Attendance models are reused.
- General Event targeting semantics remain unchanged for non-lesson Event types.
- Member and Guardian calendars can provide useful attendance feedback without exposing unrelated participant data.

## Supersession / reconciliation

This ADR supersedes ADR-0037 §3 only for `Event.event_type = lesson`. ADR-0037 remains authoritative for all other Event types and for the general self-registration lifecycle.

This ADR does not replace ADR-0032. It explicitly reuses the existing Attendance model and authorization.

## Traceability

- ADR-0032 — Event Attendance
- ADR-0037 — Event targeting, self-registration and participation policy
- `docs/02-requirements/business-rules.md` §10–§12
- `docs/05-api/events-api.md` §6, §18, §22–§23