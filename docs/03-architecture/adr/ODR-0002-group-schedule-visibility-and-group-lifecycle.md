# ODR-0002 — Group Schedule Visibility and Group Lifecycle

- **Status:** Resolved — 2026-09-15
- **Resolution:** Product Owner decision
- **Scope:** `GET /api/v1/groups/{group_id}/schedule`

## Decision

The Group Schedule is a contextual projection of Events/EventOccurrences explicitly targeted to the requested Group. Group membership is an access relationship for `self`/`children`, but it is never treated as an Event GroupTarget.

### Access policy

| Scope | Group Schedule access |
|---|---|
| `all` | Groups within the caller's allowed Club boundary |
| `own_groups` | Groups where the caller has an active `GroupInstructorAssignment` |
| `own_events` | **No standalone Group Schedule access** |
| `self` | Groups where the requester's Person has an active GroupMembership |
| `children` | Groups where an eligible child has an active GroupMembership |
| `none` | No access |

`self` and `children` access is intentionally limited to **future** schedule items. A schedule item is future when its effective Event/EventOccurrence `start_at`/`starts_at` is greater than or equal to the current server UTC instant at authorization/query evaluation time.

`all` and `own_groups` are not subject to this future-only restriction; they may read historical items that fall inside the explicitly requested range, subject to normal Event status visibility and object policy.

`own_events` does not grant access to the Group Schedule as a contextual collection. A caller with `own_events` can still access an individually authorized Event/EventOccurrence through the canonical Event/calendar APIs.

### Membership versus event targeting

GroupMembership answers whether a Person is a member of a Group. EventGroupTarget answers whether an Event/EventOccurrence belongs to that Group's schedule.

Therefore `self`/`children` authorization requires both:

1. an applicable active GroupMembership relationship for the requester/eligible child; and
2. an explicit EventGroupTarget (ordinary Event) or occurrence-level GroupTarget (recurring EventOccurrence) for the requested Group.

Membership never exposes an unrelated Event.

### Membership effectivity

For `self`/`children`, membership is evaluated at request time as an **active** GroupMembership. Because access is future-only, a currently ended membership does not authorize the Group Schedule even if the user was a member when a historical event occurred. Historical schedule access for `self`/`children` is intentionally unavailable.

### Archived Groups

An archived Group remains readable for historical schedule queries by callers otherwise authorized through `all` or `own_groups` and normal object policy. Archiving the Group does not delete or rewrite historical Event/EventOccurrence records.

An archived Group is not a valid basis for new GroupMembership or new Event targeting operations under the Group lifecycle rules. Existing future Events/EventOccurrences are not automatically deleted or cancelled solely because the Group is archived; their visibility follows the Event authorization and status rules, with `self`/`children` still restricted to future items.

### Recurring events

Ordinary Events qualify through their explicit EventGroupTarget. Recurring EventOccurrences qualify through direct occurrence-level GroupTarget relationships under ADR-0029. Future materialization takes SeriesGroupTarget definitions from the governing EventSeries version under ADR-0030 and copies applicable relationships atomically.

### Security

`Group.club_id` and `EventOccurrence.club_id` are never sufficient to authorize non-`all` access. IDOR/existence-hiding and cross-Club integrity remain governed by the existing authorization model.

No new permission or scope is introduced.

## Consequence for TH-0083

The Group Schedule authorization blocker is resolved. The canonical API specification can be marked implementation-ready and a separate implementation Issue may now be created for TH-0083.
