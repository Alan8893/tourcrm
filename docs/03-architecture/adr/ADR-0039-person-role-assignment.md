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
