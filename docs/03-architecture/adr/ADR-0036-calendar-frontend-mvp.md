# ADR-0036 — Calendar Frontend MVP Behavior

- Status: Accepted
- Date: 2026-09-20
- Decision owner: Product Owner

## Context

TourCRM MVP needs a usable Events & Calendar block. The calendar is not a read-only projection: authorized users must be able to work with events from the Events UI.

The backend already provides canonical Event and EventOccurrence APIs, including calendar projection, mutation semantics, authorization, recurrence materialization and status transitions.

## Decision

The MVP Calendar is a working entry point for:

1. viewing events;
2. creating events;
3. opening event details;
4. editing events.

The frontend must use existing canonical Event API/domain semantics and must not create a parallel event domain model, recurrence engine or authorization model.

### Presentation

- Desktop: month calendar + selected-day event list.
- Tablet: month calendar + selected-day event list below.
- Mobile: compact day navigation + selected-day event list; this is a deliberate mobile composition, not a shrunken desktop calendar.
- /events opens the current month with today selected.
- URL date may override the initial date.
- Previous month, next month and Today are available.
- Changing the visible period reloads the corresponding [from,to) calendar range.

### Event display

Calendar items show time, title, type, status and recurring indicator.

Selected-day/detail presentation may show start/end, group, instructor and location when those fields are available and authorized.

Cancelled events must be visually distinguishable without relying on color alone.

### Filters

MVP filters:

- group;
- instructor/user;
- event type;
- status.

Filters survive month navigation and have an explicit Reset action.

### Timezone

- API uses canonical timezone-aware RFC 3339 instants normalized to UTC.
- Display uses the browser timezone.
- No persistent user timezone setting is introduced.
- DST behavior is handled by timezone-aware date/time formatting.

### Loading, empty and error states

- Initial calendar load uses a skeleton.
- Period navigation keeps the previous content visible while loading the new range.
- Empty range: «В этом периоде нет событий».
- Empty mobile day: «На этот день событий нет».
- Errors preserve the calendar shell and provide Retry without exposing technical API details.

### Pagination

Calendar range requests use the backend pagination contract. If a range spans multiple pages, the frontend sequentially loads all pages before building the final visual calendar. No separate pagination control is shown inside the calendar.

### Recurring events

Recurrence is calculated and materialized by the backend.

The frontend:
- displays concrete persisted occurrences;
- shows a recurring marker;
- opens the concrete occurrence;
- does not calculate RRULE;
- does not expose recurrence rules/version in the normal calendar UI.

Editing a recurring series is only available through explicit backend-supported mutation semantics. No new frontend recurrence semantics are invented.

### Mutations

Create and edit are in MVP.

The following are not part of the calendar interaction model:

- drag-and-drop scheduling;
- mouse resize;
- inline editing;
- create-on-empty-slot;
- a separate recurrence editor;
- notifications;
- attendance UI;
- conflict UI;
- iCalendar export/import;
- new permissions/scopes;
- new backend endpoints unless separately approved.

Archive/delete follows the existing Event API and permission policy only; the calendar does not invent a new deletion semantic.

### URL and navigation

Selected date/period and filters are deep-linkable through query parameters. Refresh, bookmark, browser back/forward and shared links must restore the calendar state. No new routing mechanism is introduced.

## Architectural boundary

Frontend:
- presentation;
- navigation;
- filters;
- calendar range requests;
- event create/edit interaction.

Backend:
- authorization;
- Event/EventOccurrence domain rules;
- recurrence materialization;
- status transitions;
- mutation validation;
- accessible data projection.

## Consequences

The Calendar MVP is operationally useful rather than read-only, while remaining a presentation layer over the existing Events domain.

Calendar implementation must not become a second domain engine.

## Canonical sources

- docs/05-api/events-api.md
- docs/05-api/api-conventions.md
- docs/05-api/endpoint-inventory.md
- docs/04-ux/information-architecture.md
- docs/06-ui/ux-detailed-specification.md
- ADR-0015
- ADR-0018
- ADR-0028
- ADR-0029
- ADR-0033
