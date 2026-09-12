# Review of Claude's Primary Specification Audit

## 1. Result

Claude's report is accepted as a useful independent consistency audit. The product understanding is materially aligned with the project specification and correctly identifies the major domain boundaries, source-of-truth principles, authorization model, workflow and implementation gates.

The report is not itself a normative project specification. Where it identified a conflict, the conflict must be resolved in the canonical documentation/ADR set.

## 2. Decisions made from the audit

The following technical conflicts are resolved:

1. **Primary keys:** `ADR-0010` remains authoritative. `ADR-0008` no longer lists PK strategy as open.
2. **Authorization scopes:** `ADR-0013` defines the canonical vocabulary. `assigned_events` is an alias of `own_events`; `own_records` is not a global scope.
3. **API response envelope:** `ADR-0014` defines direct single-resource responses and `items/pagination` collection responses.
4. **Event occurrences:** `ADR-0015` defines bounded materialization with a 180-day forward planning horizon, extension on demand, stable occurrence IDs and preservation of historical occurrences.
5. **Document ownership:** `ADR-0016` defines explicit FK-backed association tables for security-sensitive ownership relationships rather than relying on a polymorphic reference alone.
6. **File storage:** the accepted file-storage decision is canonical; duplicate historical ADRs must not be edited as competing sources of truth.
7. **HTTP 422 terminology:** use `422 Unprocessable Content` consistently.

## 3. Items deliberately not resolved by this audit

These remain product/legal/external-input decisions:

- exact medical data policy;
- tourism classification and experience rules;
- rating formula;
- calendar integration priorities;
- TourSlet integration until the ZIP is analyzed;
- legal retention/deletion policy;
- financial/accounting scope;
- concrete MAX integration contract.

## 4. Implementation gate

EPIC-00 Foundation may proceed after documentation consistency fixes.

EPIC-01 Identity/Club Core may proceed once the canonical scope and PK decisions are reflected in implementation specifications.

EPIC-02 Events/Schedule may proceed using ADR-0015.

Features depending on the unresolved business/external decisions remain gated by the corresponding GAP/ODR.

## 5. Rule for future audits

An external implementation agent may report concerns and recommendations, but must not silently convert recommendations into product decisions. Accepted technical decisions belong in ADRs; business decisions belong to the product owner; unresolved dependencies remain explicit GAP/ODR entries.
