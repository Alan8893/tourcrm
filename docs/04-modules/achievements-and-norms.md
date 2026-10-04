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
