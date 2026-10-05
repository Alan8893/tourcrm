# TourCRM — Achievements & Normative Rules

**Status:** Canonical product/domain specification for the future Achievements block and its normative rules.  
**Scope:** MVP architecture and business rules. Implementation is not included in this document.

## 1. Purpose

TourCRM has a product block **Achievements (Достижения)**. The block combines:

1. club-defined achievements;
2. achievements based on external normative requirements, initially the requirements represented by the supplied FSTR reference material.

Achievements are intended for **participants (Member)** only.

The Achievement domain must be data-driven. Achievement definitions and normative requirements must not be hardcoded as individual business rules in application code.

## 2. Product model

The domain is separated into three concepts:

### 2.1 Achievement Definition

Describes an achievement that can be obtained by a participant.

Examples:

- Первый поход;
- Первая ночёвка;
- Юный путешественник — 1 степень;
- Юный путешественник — 2 степень;
- Юный турист — 1 степень.

An Achievement Definition has a source:

- `club` — internal TourCRM club achievement;
- `fstr` — achievement based on the adopted FSTR normative requirements.

An Achievement Definition also declares how it can be awarded:

- `automatic`;
- `manual`;
- `both`.

### 2.2 Achievement Requirement / Rule

Defines the conditions under which an Achievement Definition is considered satisfied.

Rules are data-driven. Adding or changing a club achievement must not require a dedicated `if` branch or a new code path for that achievement.

Rules may reference canonical TourCRM facts and metrics, for example:

- completed trip count;
- overnight count;
- one-day hike count;
- multi-day hike count;
- degree-hike count by degree;
- category-hike count by category;
- number of distinct tourism types;
- number of distinct tourism regions.

The concrete metric catalog must be aligned with the existing domain model when the Achievement Engine is implemented. Missing domain facts must not be invented in the implementation.

### 2.3 Achievement Award

Represents the fact that a particular participant received a particular achievement.

An Award must retain enough provenance to explain why it exists, including where applicable:

- achievement definition;
- requirement/rule version used;
- award method (`automatic` or `manual`);
- award date;
- verification state/source when applicable.

An Award is historical evidence and must not be silently rewritten because a later normative version changes.

## 3. Automatic and manual awards

The system supports both automatic and administrator-issued achievements.

### Automatic

The Achievement Engine evaluates the participant's canonical TourCRM facts and creates an Award when the applicable rule is satisfied.

### Manual

An Administrator may issue an achievement manually when the achievement definition allows manual awards.

Manual issuance must remain distinguishable from automatic calculation.

For normative/FSTR achievements, a manual administrator action may confirm a requirement when business process requires human verification. It must not be represented as though the engine independently calculated the result.

No separate ad-hoc manual achievement mechanism should bypass the Achievement domain.

## 4. Normative requirement sets

External requirements are stored as **versioned normative requirement sets**, not as hardcoded constants.

A requirement set represents a named source/version, for example conceptually:

`ФСТР — знаки отличия детско-юношеского туризма — <version>`

A requirement set contains one or more Achievement Requirements.

### Required source metadata

For an external normative set, store:

- source organization;
- source document title;
- source URL;
- document version;
- publication date, when known;
- effective-from date;
- effective-to date, when known;
- lifecycle/status.

The current product reference points to the official FSTR site and its materials for children's and youth tourism. The exact official document/version corresponding to the supplied table has **not yet been conclusively identified** and must not be inferred from the screenshot alone.

Official FSTR currently exposes a dedicated "Детский и юношеский туризм" section and publishes materials there; this is a source reference, not by itself proof that the supplied table is the currently effective normative edition. See the official source: https://tssr.ru/child/ .

## 5. Versioning and immutability

Normative requirements are versioned.

When an external source changes its requirements:

1. create a new requirement-set version;
2. record the new source/version metadata;
3. define the new requirements;
4. activate the new version according to the administrative workflow;
5. do not mutate a version that has already been used to calculate or verify Awards.

Historical Awards retain the requirement-set version used for their determination.

Therefore, a change of normative requirements must not retroactively invalidate or silently recalculate an already awarded achievement.

## 6. Administrator management

Only the **Administrator** manages normative requirement sets and Achievement Definitions.

The future Achievements administration UI must support, as applicable:

- creating club Achievement Definitions;
- creating/editing a new normative requirement-set version;
- entering requirement rules;
- setting source metadata;
- activating/deactivating a version;
- reviewing existing versions.

An already-used normative version is immutable. Changes are made by creating a new version.

The normative management UI is part of the future Achievements block. It is not a separate standalone product module for the current MVP.

## 7. Requirement rule representation

The implementation should represent requirements as structured data rather than a fixed database schema that mirrors one specific external table.

Conceptually:

```json
{
  "conditions": [
    {
      "metric": "one_day_hikes",
      "operator": ">=",
      "value": 2
    },
    {
      "metric": "tourism_regions",
      "operator": ">=",
      "value": 1
    }
  ]
}
```

This is an architectural example, not a final API/DB schema. The final representation must be defined during Achievement Engine implementation and must remain compatible with the canonical domain model.

## 8. Current normative reference snapshot

The following is a transcription/reference snapshot of the table supplied to the project. It is retained as **source/reference material**, not yet as an authoritative statement of the currently effective FSTR rules.

The supplied table is titled:

> «Приложение 2. Нормативы для выполнения норм для награждения знаками отличия»

Columns shown in the source:

- Название знака отличия;
- Однодневные походы (кол-во);
- Многодневные походы (кол-во);
- Степенные походы: степень / количество;
- Категорийные походы: категория / количество;
- Количество видов туризма;
- Количество районов походов.

### Reference rows visible in the supplied source

| Achievement / sign | One-day | Multi-day | Degree hikes | Category hikes | Tourism types | Regions |
|---|---:|---:|---|---|---:|---:|
| Первый поход | 1 | — | — | — | — | — |
| Юный путешественник 1 степени | 2 | — | — | — | 1 | 1 |
| Юный путешественник 2 степени | 2 | 1 | — | — | 1 | 1 |
| Юный путешественник 3 степени | — | 2 | — | — | 1 | 1 |
| Юный путешественник 4 степени | — | 2 | 1 / 1 | — | 1 | 1 |
| Юный путешественник 5 степени | — | 2 | 2 / 1 | — | 1 | 1 |
| Юный путешественник 6 степени | — | 2 | 3 / 1 | — | 1 | 1 |
| Юный путешественник 7 степени | — | — | 3 / 1 | 1 / 1 | 1 | 1 |
| Юный путешественник 8 степени | — | — | 3 / 1 | 1 / 2 | 2 | 1 |
| Юный путешественник 9 степени | — | — | — | 1 / 3 | 2 | 2 |
| Юный турист 3 степени | — | — | — | 1 / 3 | 3 | 2 |
| Юный турист 2 степени | — | — | — | 1 / 4 | 3 | 3 |
| Юный турист 1 степени | — | — | — | 2 / 1; 1 / 2 | 3 | 3 |

For paired values, the notation is `degree/category / quantity`. The final row is represented exactly as readable from the supplied image and must be checked against the authoritative source document before implementation.

**Important:** this snapshot is deliberately kept separate from the executable requirement catalog. It must not be treated as the active FSTR version until the source document/version is identified and approved.

## 9. Tourism types and regions

### Tourism types

Tourism types use the **internal TourCRM reference catalog**. They are not hardcoded inside the FSTR requirement definitions.

The requirement may count the number of distinct tourism types represented by qualifying facts.

The internal catalog is authoritative for TourCRM classification; the Achievement Engine must not invent a parallel list inside the FSTR rules.

### Tourism regions

"Количество районов походов" means the number of **distinct tourism regions** in which qualifying hikes were completed.

Multiple hikes in the same region count as one region for this metric.

The canonical region reference/catalog is a separate domain concern and must be reused by the Achievement Engine.

## 10. Participant scope

These achievements apply to **participants (Member)**.

They are not automatically applicable to Instructor, Guardian or Administrator roles.

Guardian can consume a child's achievement information through the appropriate child relationship/read model when the relevant UI/API is implemented; this does not make Guardian the recipient of the achievement.

## 11. Source facts and calculation

The Achievement Engine must calculate automatic achievements from canonical TourCRM facts.

The system must not maintain a second independent source of truth for the participant's tourism history merely to support achievements.

Conceptually:

`Trip/Event facts → participation → classified tourism facts → Achievement Engine → Achievement Award`

The exact source entities and event/Trip classification fields must be reconciled with the current domain model before implementation.

## 12. Historical tourism experience

The current MVP does **not** support importing or manually entering verified tourism experience that happened before the relevant facts existed in TourCRM.

This is an explicit future technical-debt item.

Future capability may allow an Administrator to enter verified historical trips/experience so that a participant can receive achievements based on pre-TourCRM activity.

Historical evidence must be distinguishable from automatically derived TourCRM facts.

## 13. Reports relationship

The future Reports block consumes Achievement information but does not own the underlying achievement rules.

Reports must read canonical Achievement Awards / derived achievement facts rather than duplicate the normative calculation logic.

Likewise, the Achievements UI must not contain report-specific aggregation logic that belongs to Reports.

## 14. Current MVP boundaries

Not yet implemented:

- Achievement Engine;
- Achievement Definition management UI;
- normative requirement-set management UI;
- automatic Award generation;
- manual Award workflow;
- FSTR requirement import/entry workflow;
- Reports based on achievement data;
- historical pre-TourCRM tourism evidence.

This document establishes the architecture and business rules so that implementation can proceed later without inventing them in code.

## 15. Non-goals / explicit exclusions

- No multi-club business model.
- No per-club selection of different normative systems.
- No hardcoded FSTR constants in application code.
- No mutation of an already-used normative version.
- No separate achievement rules hidden inside Reports.
- No automatic application of achievements to non-participant roles.

The existing backend `Club` entity remains a technical implementation artifact because removing it would currently require a large refactor. It must not be interpreted as a product decision to support multiple clubs.

## 16. Achievement Definition lifecycle

Achievement Definition has a two-state lifecycle:

- `active`;
- `inactive`.

Canonical rules:

- Administrator may activate and deactivate an Achievement Definition;
- `active` means the Definition may participate in creation of new Awards according to its `award_method`;
- `inactive` means no new Awards are created from that Definition;
- deactivation does not revoke, modify or delete already issued Awards;
- an inactive Definition may be activated again;
- physical deletion of an Achievement Definition is not part of the current Achievement Domain.

Definition lifecycle is independent from the lifecycle/versioning of normative requirement sets and from the historical state of individual Awards.

## 17. Achievement Award lifecycle and correction

An Achievement Award is a historical record of the fact that a participant received an achievement. After issuance, an Award is not edited through an ordinary update operation.

An Award has two states:

- `active`;
- `revoked`.

Canonical correction rules:

- only an Administrator may revoke an Award;
- revocation requires an explicit reason;
- revocation preserves the original Award record and its provenance;
- the original Award cannot be physically deleted or rewritten as part of revocation;
- revocation does not modify or delete the canonical facts from which the Award was created;
- when a correction requires a new valid achievement, a new Award is created as a separate historical record with its own provenance;
- the Achievement Engine does not automatically revoke an already issued Award solely because source facts later change, a normative version changes, or the Achievement Definition is deactivated;
- automatic re-certification or automatic retrospective review of Awards is not part of the current decision and requires a separate business decision.

The Award lifecycle is independent from the lifecycle of its Achievement Definition and from normative requirement-set version lifecycle.


## 18. Achievement Award repeatability

Achievement Definition explicitly declares its repeatability semantics:

- `non_repeatable`;
- `repeatable`.

Canonical rules:

- for a `non_repeatable` Achievement Definition, a Participant may have at most one Award for that Definition across the Award history;
- for a `repeatable` Achievement Definition, a Participant may have multiple Awards for the same Definition;
- repeatability is a property of the Achievement Definition and is independent from the Definition lifecycle (`active`/`inactive`) and the Award lifecycle (`active`/`revoked`);
- repeatability does not by itself define what constitutes a new qualifying occurrence for a subsequent Award;
- the semantics of qualifying conditions, identification of a new qualifying occurrence, and deterministic prevention of issuing multiple Awards for the same qualifying basis are deferred to the Requirement / Rule semantics and Achievement Engine decisions;
- no implementation may infer repeatability from the name, source, award method, or normative requirement values of an Achievement Definition.

The repeatability decision does not authorize retrospective re-evaluation or automatic re-awarding of historical Awards.


## 19. Achievement Requirement / Rule versioning

Achievement Requirement / Rule has its own immutable versioning independent from the lifecycle of the Achievement Definition.

Canonical rules:

- an Achievement Definition remains the stable identity of an achievement;
- each change to the executable Requirement / Rule creates a new immutable Rule Version rather than mutating the previously used version;
- a Rule Version belongs to exactly one Achievement Definition;
- multiple Rule Versions may exist for the same Achievement Definition;
- only the applicable current Rule Version participates in creation of new Awards;
- a Rule Version that has already been used to calculate or verify an Award is immutable;
- changing a Rule does not require creating a new Achievement Definition when the achievement identity remains the same;
- Rule Version is independent from Definition lifecycle (`active`/`inactive`) and Award lifecycle (`active`/`revoked`);
- Award provenance records the exact Rule Version used for that Award.

For achievements with `source = fstr`:

- the Rule Version additionally references the specific Normative Requirement Set Version on which the rule is based;
- a new normative requirement-set version results in new Rule Version(s) for the affected Achievement Definitions;
- the old Rule Version remains immutable and continues to identify the historical basis of Awards created from it.

For achievements with `source = club`:

- Rule Version may exist without any Normative Requirement Set reference;
- changes to a club achievement's conditions create a new Rule Version while preserving the same Achievement Definition identity.

The versioning model does not authorize retrospective re-evaluation or automatic re-awarding of historical Awards. Determination of which Rule Version is applicable to a given qualification event and how qualifying conditions produce a new Award remain part of the subsequent Requirement / Rule semantics and Achievement Engine decisions.


## 20. Requirement / Rule condition semantics

A Requirement / Rule may contain nested condition groups using the boolean operators:

- `AND` — every child condition/group must be satisfied;
- `OR` — at least one child condition/group must be satisfied.

Condition groups may be nested to express alternative or combined qualification paths. The logical structure is part of the Rule Version and must not be hidden in application code.

The basic case remains an `AND` group containing individual metric conditions. For example, a rule may require all of the following simultaneously:

- `one_day_hikes >= 2`;
- `tourism_types >= 1`;
- `tourism_regions >= 1`.

A more complex rule may express alternatives, for example:

```text
OR
├── AND
│   ├── degree_hikes[3] >= 1
│   └── tourism_regions >= 1
└── AND
    ├── category_hikes[1] >= 2
    └── tourism_types >= 2
```

Canonical data semantics:

- a condition may be evaluated only against canonical TourCRM facts and supported metrics;
- absence of a required canonical fact does not by itself satisfy the condition;
- the Rule must not invent or infer unsupported facts merely to make a condition evaluable;
- the same logical semantics apply to club and normative/FSTR rules;
- exact metric names, operators, value types and evaluation rules remain subject to the canonical metric catalog and Achievement Engine decisions.

The condition model does not by itself define when a Rule is evaluated, which Rule Version is applicable to a qualification event, or what constitutes a new qualifying occurrence. Those decisions remain separate Achievement Engine decisions.


## 21. Canonical metric sources

Achievement Engine does not maintain an independent history of participant tourism activity and does not become a second source of truth for tourism facts.

The canonical flow is:

```text
Event / Trip
    ↓
EventParticipation / TripParticipant
    ↓
Canonical Tourism Facts
    ↓
Metric Evaluation
    ↓
Achievement Engine
    ↓
Achievement Award
```

Canonical rules:

- Achievement Engine evaluates Requirement / Rule conditions using canonical TourCRM facts and metrics;
- the source of tourism history remains the canonical Tourism / Trip domain;
- counts of qualifying trips are derived from canonical Trip/Event facts and participation;
- one-day / multi-day classification is taken from the approved canonical hike classification;
- degree and category values may be used only when the corresponding canonical facts exist in the domain;
- tourism type metrics use the internal TourCRM TourismType reference catalog;
- tourism region metrics use the canonical Geography / tourism-region model;
- Achievement Engine must not introduce parallel catalogs or achievement-specific copies of these facts;
- derived metrics may be calculated during Rule evaluation, but their inputs must remain canonical domain facts;
- if a required metric cannot be grounded in an existing canonical domain fact, this is a domain GAP and must not be solved by inventing an alternative fact in Achievement Engine.

The exact metric catalog and the precise source entity/field for every metric remain subject to the Achievement metric-catalog decision and implementation reconciliation with the current canonical domain model.

This boundary prevents Achievement Engine from duplicating tourism-domain business logic or maintaining a second independent source of truth.


## 22. Achievement Engine triggering and reconciliation

Achievement Engine uses a combined event-driven and reconciliation model.

### Event-driven evaluation

When a canonical tourism fact that may affect achievements changes, the Achievement Engine may be triggered to evaluate the affected Achievement Definitions and applicable Rule Versions.

Conceptually:

```text
Canonical tourism fact changed
        ↓
Achievement Engine trigger
        ↓
Identify affected Achievement Definitions
        ↓
Evaluate applicable Rule Versions
        ↓
Create Award when qualification is satisfied
```

The exact event list and dispatch mechanism are implementation concerns and must not introduce new business facts outside the canonical domain.

### Periodic reconciliation

A periodic reconciliation process is also required as a safety mechanism for missed events, processing failures or other operational inconsistencies.

Reconciliation may re-evaluate canonical facts to identify Awards that should exist but were not created by the event-driven path.

Reconciliation must not mean rewriting or silently recalculating historical Awards. Its purpose is to identify and create valid missing Awards according to the applicable current Rule Version and the repeatability semantics of the Achievement Definition.

### Idempotency and historical safety

The Achievement Engine must be idempotent:

- processing the same canonical change more than once must not create duplicate Awards for the same qualifying basis;
- for a `non_repeatable` Achievement Definition, repeated evaluation must not create more than one Award across the Award history;
- an already issued Award is not deleted, rewritten or automatically revoked by Engine evaluation;
- changes to source facts do not automatically revoke an existing Award;
- changes to normative versions or Definition lifecycle do not automatically revoke an existing Award;
- automatic retrospective re-certification is not part of this model.

For `repeatable` Achievement Definitions, the Engine must distinguish a new qualifying occurrence from repeated processing of the same occurrence. The exact semantics for identifying a qualifying occurrence are deferred to the Requirement / Rule semantics and Engine implementation decisions.

The Engine evaluates only active Achievement Definitions and applicable Rule Versions for new automatic Awards. A Definition being inactive does not alter historical Awards.

This triggering/reconciliation model does not define the exact event schema, scheduling mechanism, locking strategy, or persistence constraints. Those are implementation details to be resolved without changing the canonical business semantics.


## 23. Achievement metric catalog and canonical sources — A8

The Achievement Engine uses an explicit metric catalog. A metric may be used by a Requirement / Rule only when its canonical source and semantics have been approved.

### Approved metric

The first approved metric is:

- `completed_trips` — number of Trips that are completed and in which the Member has `TripParticipant.actual_participation = true`.

For the current MVP, `completed_trips` is the only approved executable tourism metric for Achievement evaluation.

This decision explicitly authorizes the Achievement Engine to use the existing Trip/Event completion and actual-participation facts for this metric.

### Unsupported metrics

The following metrics are not executable until their canonical sources and semantics are separately approved:

- overnight count;
- one-day hike count;
- multi-day hike count;
- degree-hike count by degree;
- category-hike count by category;
- distinct tourism types;
- distinct tourism regions;
- any other metric not present in the approved metric catalog.

The reference FSTR table in §8 remains source/reference material and does not make these metrics executable.

A Requirement / Rule referencing an unsupported metric must not be accepted as an executable rule. The Achievement Engine must not invent or infer a replacement source.

This allows the Achievement Domain implementation to proceed with a data-driven metric registry while keeping unsupported tourism facts outside the Achievement Engine.

## 24. Repeatable automatic Awards — A9

For the current Achievement Engine implementation:

- automatic Award creation is supported for `non_repeatable` Achievement Definitions;
- `repeatable` Achievement Definitions are supported for manual awarding only;
- the automatic Engine must not create Awards for a `repeatable` Definition until qualifying-occurrence semantics are approved by a separate decision.

This is a temporary implementation boundary, not a change to the meaning of the `repeatable` property.

The future qualifying-occurrence model must define how a new qualifying occurrence is distinguished from repeated processing of the same canonical facts. No implementation may invent fact-consumption or trigger-fact semantics without a separate canonical decision.

## 25. Achievement authorization — A10

Achievement authorization is Administrator-only for management and Award operations.

The canonical permission model is:

- `achievement.manage` — Administrator only; manages Achievement Definitions, Requirement / Rule Versions and Normative Requirement Sets, including activation/deactivation and version management;
- `achievement.award` — Administrator only; permits manual Award issuance and Award revocation according to the canonical Award lifecycle.

Instructor does not receive manual Achievement Award authority.

The backend remains authoritative for these checks. Frontend visibility must not be treated as an authorization boundary.

No additional achievement permission is introduced beyond `achievement.manage` and `achievement.award` without a separate PO decision.

## 26. Applicable Rule Version — A11

For creation of a new automatic Award, exactly one current active Rule Version is applicable for an active Achievement Definition.

The applicable Rule Version is selected from the Rule Versions active at the time of evaluation. The Engine does not retrospectively switch an Award to a newer Rule Version after issuance.

For `source = fstr`, the Rule Version must reference the corresponding Normative Requirement Set Version, and that normative version must be active/applicable at the time the Rule is evaluated.

Historical Awards retain the exact Rule Version and normative version recorded in their provenance.

If no applicable active Rule Version exists, no automatic Award is created.

## 27. Achievement recipient — A12

The recipient of an Achievement Award is a Member represented by the existing canonical membership/role model.

For Achievement Domain purposes:

- Administrator, Instructor and Guardian are not Achievement recipients;
- participation in an Event alone does not redefine a user as a Member;
- the Engine must use the existing canonical Member/membership assignment rather than introduce an achievement-specific recipient classification;
- Guardian access to a child's achievements remains read/access behavior and does not make Guardian the Award recipient.

The implementation must reconcile the exact existing membership/role entity and field used for this check before writing the recipient authorization path. If the existing canonical membership model cannot provide this fact, implementation must stop and report a GAP rather than invent a new source.

## 28. Current Achievement implementation boundary after A8–A12

The Achievement Engine may now be implemented using:

- approved metric: `completed_trips`;
- `non_repeatable` automatic Awards;
- manual Awards for Definitions whose `award_method` permits manual issuance;
- Administrator-only achievement management and Award operations;
- one applicable active Rule Version per Definition at evaluation time;
- Member recipients from the existing canonical membership/role model.

All other tourism metrics and automatic repeatable-occurrence semantics remain explicitly outside the current implementation boundary.

## 29. Definition semantic-field immutability — A14

The Achievement Definition is a stable business identity. The following semantic fields are immutable after Definition creation:

- `code`;
- `source`;
- `award_method`;
- `repeatability`.

The human-facing `name` and `description` may be edited without creating a new Definition.

Changing any immutable semantic field requires creating a new Achievement Definition. This preserves the meaning of historical Awards and prevents a Definition from changing its award semantics underneath existing Award history.

## 30. Rule Version immutability — A13

An executable Achievement Requirement / Rule Version is immutable after creation.

Any change to the executable rule condition or its normative reference creates a new Rule Version belonging to the same Achievement Definition.

This applies even when the existing Rule Version has not yet been used to create or verify an Award. A Rule Version is a versioned executable artifact, not an editable draft.

Therefore:

- an existing Rule Version must not be edited in place;
- changing a condition creates a new Rule Version;
- changing the referenced Normative Requirement Set Version creates a new Rule Version;
- the existing version remains available for historical/reference purposes;
- activation/deactivation is lifecycle state, not mutation of the executable Rule content.

## 31. Manual Award provenance — A15

Manual Award issuance must remain distinguishable from automatic Engine calculation.

A manual Award:

- records `award_method = manual`;
- records the Administrator who issued it;
- preserves the Administrator's verification note/source when applicable;
- may reference an exact Rule Version when the Administrator explicitly verifies the Award against that Rule Version;
- may have no Rule Version reference when the manual Award is not being asserted as a determination against a specific Rule Version.

For FSTR/manual normative Awards, the manual verification/source information must be retained so that the Award is not represented as though the Engine independently calculated it.

The system must not silently attach the currently active Rule Version to a manual Award merely because one exists.

Historical manual Awards retain the provenance recorded at issuance.

## 32. Normative effective interval semantics — A16

Normative Requirement Set Version applicability uses an inclusive calendar-date interval:

`effective_from <= evaluation_date <= effective_to`

when `effective_to` is present.

Therefore:

- `effective_from` is inclusive;
- `effective_to` is inclusive;
- a missing `effective_to` means the version has no configured end date.

For a new automatic FSTR Award, the referenced Normative Requirement Set Version must be both active and applicable on the evaluation date according to this interval.

These date semantics do not retrospectively change the provenance of an existing Award.

