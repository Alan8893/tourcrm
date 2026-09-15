# ADR-0029 — EventOccurrence authorization relationships

## Status
Proposed — blocked pending PO decision.

## Context

`EventOccurrence` is a first-class operational entity in the accepted recurrence model (ADR-0028). The internal calendar must nevertheless preserve the canonical Event authorization semantics from ADR-0020: `all`, `own_groups`, `own_events`, `self`, and `children` are evaluated with permission and object relationships.

The current recurrence persistence model deliberately has no required nullable bridge from `EventOccurrence` to the legacy `Event` row. Its occurrence snapshot contains operational Event fields, while relationship entities such as EventStaffAssignment, EventGroupTarget and EventParticipation are defined separately.

The current physical recurrence schema does not yet define how those relationships are inherited/materialized for a recurring occurrence. Consequently, a calendar query cannot deterministically derive every canonical scope for recurring occurrences without a new accepted relationship strategy.

## Decision required

The PO must choose one canonical strategy for recurring occurrence authorization relationships.

### Option A — materialize occurrence relationships

When an occurrence is materialized, copy the applicable staff assignments, group targets and participation relationships into occurrence-level relationship records.

**Pros**
- direct and deterministic occurrence authorization;
- historical occurrence relationships can remain stable;
- efficient calendar filtering.

**Cons**
- additional persistence and synchronization rules;
- participation semantics must be separated from future registration policy;
- changes to Series-level relationships require explicit propagation semantics.

### Option B — introduce a canonical relationship owner inherited by occurrences

Store staffing/targeting/participation against a canonical recurring logical entity and resolve occurrence authorization through that owner, while preserving occurrence-specific exceptions where required.

**Pros**
- less relationship duplication;
- relationship changes can naturally apply to future occurrences.

**Cons**
- requires a new explicit inheritance model;
- historical semantics and exceptions become more complex;
- existing ADR-0023 relationship contracts need extension.

### Option C — bounded calendar authorization for the first slice

Implement the internal calendar initially only for relationships that are already deterministic (for example `all`), and explicitly defer recurring occurrence visibility for `own_groups`, `own_events`, `self`, and `children` until the corresponding relationship contracts exist.

**Pros**
- smallest implementation slice;
- no speculative persistence model.

**Cons**
- insufficient for the intended instructor/member/guardian calendar experience;
- creates a temporary functional limitation that must be explicit in the API contract.

## Constraints

The selected option must preserve:

- ADR-0013 scope vocabulary;
- ADR-0020 Event authorization semantics;
- ADR-0022 cross-Club ownership integrity;
- ADR-0023 explicit Event relationship model;
- ADR-0028 stable occurrence identity and no required nullable `event_id` bridge;
- historical integrity and IDOR protection;
- no new permission or scope.

## Decision rule

Do not choose an option by implementation convenience. The selected option is a product/architecture decision because it affects who can see recurring events and how future relationship changes affect historical occurrences.

## Consequence for TH-0079

TH-0079 remains a specification gate until this ADR is accepted. After acceptance, the recurrence schema/API/module documentation and the internal calendar contract must be reconciled before a calendar implementation Issue is created.
