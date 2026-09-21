# ADR-0039 — Person Role Assignment and Account Roles

**Status:** Accepted  
**Date:** 2026-09-20  
**Decision type:** PO Decision  
**Scope:** People Management / Authorization

## Context

A Person can belong to the Club through ClubMembership, while their capabilities in TourCRM are determined by system roles. These are different concepts.

The People UI currently allows management of Person and ClubMembership but does not provide the administrator with a canonical way to assign system roles.

## Decision

### 1. Role is assigned from Person Detail

The administrator manages a person's system roles from the Person Detail page.

Role assignment is not part of the initial Add person form.

### 2. RoleAssignment is the canonical source

System roles are stored through the existing RoleAssignment model/API.

Do not use ClubMembership.membership_type as a substitute for a system role.

### 3. Canonical roles

The MVP role set is:
- admin — club administrator;
- instructor — instructor/operational staff;
- member — club participant;
- guardian — parent/legal representative.

### 4. Multiple roles are allowed

One Person/User may have multiple active roles simultaneously.

Examples:
- instructor + guardian;
- member + guardian;
- admin + instructor, where explicitly permitted by the existing authorization policy.

Role permissions are combined according to the existing authorization model, followed by scope and object-level checks.

### 5. Role assignment does not mutate Person or ClubMembership

Adding/removing a role:
- does not change Person;
- does not change ClubMembership;
- does not change membership status;
- does not change membership_type.

This preserves the separation:

Person = identity  
ClubMembership = relationship with the Club  
RoleAssignment = capabilities in TourCRM

### 6. Instructor role

Assigning instructor does NOT automatically assign the person to any Group.

Group responsibility remains an explicit GroupInstructorAssignment.

Event responsibility remains an explicit EventStaffAssignment.

### 7. Guardian role

Assigning guardian does not automatically create a child relationship.

After assigning the guardian role, the Person Detail UI should provide the administrator with the next action to link one or more children through the existing GuardianRelationship model.

Guardian relationships remain independent records.

### 8. Member role

Assigning member requires no additional relationship configuration.

Club membership is already managed separately.

### 9. Admin role

Assigning admin creates the corresponding RoleAssignment and grants the permissions defined by the existing role-permission model.

It does not modify Person or ClubMembership.

### 10. Membership type is not exposed as a role selector

The People UI must not present membership_type as an alternative to system roles.

For the current Person creation workflow, the initial ClubMembership remains automatically created with the canonical fixed values:
- membership_type = member;
- status = active.

These values are technical/domain membership fields and are not the UI mechanism for choosing system roles.

## Consequences

- Administrators can clearly answer "what is this person in TourCRM?" from Person Detail.
- A person can simultaneously be an instructor and a guardian.
- Instructor/group responsibility remains explicit and auditable.
- Guardian/child relationships remain explicit and auditable.
- Existing authorization architecture does not need to be redefined.

## Explicitly not decided here

This ADR does not change:
- generic authorization semantics;
- permission catalog;
- GroupInstructorAssignment rules;
- EventStaffAssignment rules;
- GuardianRelationship authorization;
- ClubMembership lifecycle;
- multi-Club architecture;
- navigation architecture.

## Amendment (TH-0116 / Issue #150): initial role selection inside the Person creation wizard

### 12. §1's "not part of the initial Add person form" is superseded, narrowly, for the new wizard endpoint only

TH-0116 replaces the plain "Add person" form with a guided, multi-step creation wizard (`POST /api/v1/persons/wizard`, `docs/05-api/people-api.md` §6.1) whose second step is selecting the Person's initial system role, followed by role-specific contextual setup (group selection for instructor/member, child selection for guardian). This is an explicit, scoped PO decision for TH-0116 superseding §1's blanket statement — the same kind of narrow, documented supersession already established by ADR-0035 §8 over ADR-0023's `is_primary_contact` for `GuardianRelationship`.

The supersession is scoped strictly to the new wizard endpoint:

- `POST /api/v1/persons` (the plain, non-wizard Person creation endpoint, §6) is unchanged — it still creates only Person + technical ClubMembership, with no role/group/guardian selection, exactly as before this amendment.
- Managing roles from Person Detail (§1's first sentence, and the whole of §24.1 `people-api.md` — `GET/POST /persons/{person_id}/role-assignments`, `DELETE .../role-assignments/{role_code}`) is unchanged and remains the only way to add or remove roles **after** creation, including additional roles beyond the one chosen in the wizard.
- The wizard creates **exactly one** initial `RoleAssignment`, from the same four canonical codes as §3 (`admin`/`instructor`/`member`/`guardian`) — never more than one, and never automatically (§5, §6, §7 below remain intact: no automatic Group, no automatic GuardianRelationship, beyond what the wizard's own contextual step explicitly creates).

### 13. Contextual setup created by the wizard is exactly the same relationship model as manual, post-creation setup

Selecting a role in the wizard does not introduce a new relationship model:

- `instructor` — 0..N `GroupInstructorAssignment` records, same shape as would be created later via `POST /groups/{group_id}/instructors` (§16).
- `member` — 1..N `GroupMembership` records, same shape as `POST /groups/{group_id}/members` (§15); at least one group is required because, per §8's "no additional relationship configuration" reading in the wizard's synchronous context, a member role without any group would leave the Person's membership contextually meaningless in the guided flow — this is a wizard-level UX requirement, not a change to §8 or to `ClubMembership`, which the wizard still creates automatically and unconditionally regardless of role (§10 above, unchanged).
- `guardian` — 1..N `GuardianRelationship` records, same shape as `POST /persons/{child_person_id}/guardian-relationships` (§18); §7's guidance to prompt the administrator toward linking children is, for the wizard specifically, made a required part of the same atomic operation rather than a follow-up action, because the wizard is a single guided flow rather than a multi-visit Person Detail workflow.
- `admin` — no contextual step, matching §9 exactly.

None of this changes `GroupInstructorAssignment`, `GroupMembership`, or `GuardianRelationship` authorization, validation, or lifecycle rules — the wizard is a single atomic transaction composing the same canonical services these endpoints already use (`docs/05-api/people-api.md` §6.1).
