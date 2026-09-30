# TourCRM — News / Announcements

## 1. Назначение

News is a club-wide announcement domain used to publish announcements about upcoming and other club activities. News is displayed on Home and has a separate list/detail surface available to all authenticated user roles.

News is a domain object and must not be implemented as Home-page-only state.

## 2. Audience and visibility

News is visible to all four current application roles:

- Administrator;
- Instructor;
- Member / Participant;
- Guardian.

The first version supports audience targeting by:

1. **Entire club** — visible to all four roles.
2. **Selected groups** — visible according to existing group membership context.

For group-targeted news:

- Member sees the news when the Member belongs to a selected group;
- Guardian sees the news when at least one child accessible to that Guardian belongs to a selected group;
- Instructor sees the news when the Instructor is associated with a selected group according to the existing group/instructor relationship model;
- Administrator can manage and view all news.

News targeting must reuse existing People/Group/Guardian relationships. Do not introduce a parallel audience-membership model.

Role-only targeting is out of scope for the first version.

## 3. Home placement

News is a Home/Dashboard content block, not a new global navigation item.

On desktop Home, News is placed before the “Ближайшие события” block.

The Home block shows the latest 3–5 published news items and provides a “Все новости” entry point to the full news list.

The block must support news with and without an image.

Mobile presentation must remain responsive and use the existing TourCRM visual language.

## 4. News list and detail

The full News surface is available to all four roles.

The list shows published news that are visible to the current user according to audience targeting.

A news detail view shows, when present:

- title;
- body/content;
- publication date;
- event date;
- location;
- image;
- linked Event;
- publication status where appropriate for Administrator.

If a News item is linked to an Event, the detail view provides navigation to that Event.

The Event link is optional.

## 5. News lifecycle

News has the following lifecycle states:

- `draft` — prepared but not visible to ordinary users;
- `published` — visible according to audience targeting;
- `archived` — no longer shown as current published news.

Only `published` news participates in the Home block and ordinary-user News list.

Allowed lifecycle transitions (PO decision, PR #229 / TH-0120):

- `draft → published`;
- `draft → archived`;
- `published → archived`.

`archived` is a terminal state:

- an archived News cannot be edited (content, audience, Event link or image);
- restoring an archived News (to `draft` or `published`) is not supported in the MVP.

No other transitions exist.

An Administrator may create, edit, publish/archive and delete News according to the management UI contract.

Deletion is a soft-delete/archive operation; the implementation must preserve the record rather than physically deleting the News object unless a later retention policy explicitly changes this rule.

## 6. News content

MVP fields:

- title — required;
- body/content — required;
- image — optional;
- publication status;
- publication date;
- event date — optional;
- location — optional;
- linked Event — optional;
- audience type — club or selected groups;
- selected groups — required when audience type is `groups`;
- created by;
- created at;
- updated at.

The image is optional. The UI must provide a useful card/detail representation without an image.

## 7. Administrator management

Only Administrator may:

- create News;
- edit News;
- publish News;
- archive/delete News;
- select audience;
- link News to an Event.

No new global navigation item is introduced for News.

The management entry point may be placed in the existing Reports/administrative area or another contextual administrative surface only by a separate navigation decision. The public News surface remains accessible from Home and its “Все новости” entry point.

## 8. Notifications

News publication does not send push, email, Telegram or other notifications in this version.

Notification delivery will be addressed as part of the future Notifications domain.

## 9. Authorization

Frontend visibility is not an authorization boundary. Backend authorization must enforce that only Administrator can mutate News and that readers receive only News items visible to their audience context.

No new permission is introduced by this UX decision unless required by the existing authorization model and separately approved.

## 10. Out of scope

- role-only audience targeting;
- push/email/Telegram notifications;
- saved news templates;
- comments/reactions;
- public unauthenticated news;
- physical deletion/retention policy beyond archive semantics;
- separate global navigation item for News.
