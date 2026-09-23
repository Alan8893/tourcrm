# TourCRM UI Foundation Specification v1.0

**Status:** APPROVED BASELINE  
**Stage:** App Shell / UI Foundation  
**Product:** TourCRM — туристический клуб «Вектор»

## 1. Purpose

This specification is the frontend UX/UI contract for the shared TourCRM App Shell and UI Foundation across desktop, tablet and mobile.

It does not reopen approved visual decisions and does not authorize creation of new visual assets unless explicitly approved.

## 2. Non-negotiable rules

1. Use exactly seven primary navigation items: Главная, Люди, Группы, События, Достижения, Отчёты, Настройки.
2. Use the official «Вектор» logo only.
3. Do not create logo variants.
4. Do not use SVG.
5. Do not introduce generic icon-library icons for approved TourCRM concepts.
6. Use approved production assets and stable semantic IDs.
7. Do not modify closed asset packages.
8. Decorative assets remain deferred P2.
9. DOM-009 remains a semantic gap; do not invent artwork for it.
10. Frontend must not contain business/domain calculation logic.
11. Do not silently change approved UX/UI decisions.

## 3. App Shell

### 3.1 Authentication boundary

The App Shell is an authenticated application surface. An unauthenticated visitor must not enter or render the authenticated shell.

- Guest is not a TourCRM role.
- Do not render a Гость pseudo-profile, pseudo-role or authenticated shell state for unauthenticated visitors.
- If the current authentication/session state is unauthenticated, resolve the request at the existing authentication boundary and route the visitor to the login/auth entry.
- Protected application routes must not become usable through a frontend-only pseudo-user state.
- Do not introduce a new Guest permission, role, account or backend authorization model in this UI slice.
- Authenticated users continue to receive the existing role-aware navigation; this task does not redefine navigation IA.
- While authentication state is being resolved, use an explicit loading/resolution state rather than rendering the authenticated shell with placeholder identity.

### Desktop

- Persistent left navigation.
- Top brand/header zone.
- Official logo on the left.
- Notifications when applicable.
- Avatar/profile on the right.
- Main content area with generous spacing.
- Active navigation state must be visually clear.

Do not add a separate Settings icon to the header.

### Tablet

Preserve the same information architecture while allowing compact navigation and responsive reflow. Do not simply scale desktop down.

### Mobile

Use a dedicated responsive composition:

- compact header;
- menu/navigation control;
- logo;
- notifications/profile;
- vertical content flow;
- bottom navigation when it improves access to primary sections.

Normal workflows must not require horizontal scrolling.

## 4. Page structure

A page may contain:

- context/breadcrumb;
- title;
- short description;
- primary action;
- search/filters;
- main content;
- supporting content.

Not every page needs every element.

Every screen should make three things clear:

- Where am I?
- What is happening?
- What should I do next?

## 5. Visual foundation

TourCRM should feel light, warm, friendly, modern, calm and tourism-oriented.

Use:

- light neutral background;
- white/light surfaces;
- soft borders;
- restrained shadows;
- rounded surfaces;
- generous whitespace;
- clear information hierarchy.

Purple is an accent, not the dominant visual field.

Avoid the appearance of a generic corporate ERP.

## 6. Core components

Provide reusable patterns for:

- App Shell;
- Header;
- Sidebar;
- Mobile navigation;
- Page Header;
- buttons;
- inputs;
- search;
- filters;
- cards;
- lists;
- tabs;
- status/badges;
- dialogs and confirmations;
- notifications;
- loading;
- empty;
- error/system states.

Avoid excessive component nesting and duplicated shell logic.

## 7. Actions

Use three levels:

- **Primary** — main action in the current context.
- **Secondary** — useful but non-primary action.
- **Destructive** — consequential or irreversible action.

Do not make many local actions visually equal to the primary action.

## 8. Lists and details

Prefer readable object lists over dense enterprise tables when the task is discovery.

A group list item may expose:

- group image/thumbnail where approved;
- group name;
- participant count;
- instructor;
- status;
- contextual action.

Detail pages use:

- return/context;
- object identity;
- status;
- primary action;
- related tabs;
- main information;
- related objects/events.

## 9. Tabs

Tabs are only for closely related views of the same object.

Example:

`Обзор | Участники | События | Достижения | Файлы | Настройки`

Active state must be clear and keyboard accessible.

## 10. Status and illustrations

Use approved status assets without changing their meanings.

Approved status meanings:

- planned;
- ongoing;
- completed;
- ended;
- archived;
- success;
- warning;
- error;
- info.

Approved illustrations are used for empty, system and onboarding states.

`empty-search` and `no-results` remain distinct.

System illustrations are not interchangeable with status icons.

Broad decorative assets remain absent from this stage except for the explicitly approved minimal TH-0089 header treatment.

## 11. Responsive rules

### Desktop

Prioritize persistent navigation, multi-column layouts where useful and visible secondary metadata.

### Tablet

Prioritize compact navigation, responsive grids and preservation of primary actions.

### Mobile

Prioritize the primary task, readable cards/list rows, vertical flow and accessible controls.

Never hide a critical action solely because the viewport is narrow.

## 12. Accessibility

Implementation must provide:

- semantic HTML;
- keyboard access;
- visible focus;
- accessible names for icon-only controls;
- logical heading hierarchy;
- sufficient contrast;
- no color-only meaning;
- usable touch targets.

## 13. Reference screens

Validate the foundation against three screen types:

### Home

Club overview, key summary, nearest events, quick actions and current information. Avoid KPI-heavy dashboard design.

### Groups list

Title, description, create action, search, filters, readable group list, status and contextual actions.

### Group detail

Return/context, cover/identity, name and status, primary action, related tabs, main information and upcoming/related information.

These are reference patterns; do not invent domain data that is absent from backend contracts.

## 14. Asset source of truth

Production assets are consumed only from the approved integration source.

Approved families:

- Brand;
- Navigation;
- Actions;
- Status;
- Domain P2;
- Achievements P0/P1;
- Illustrations: Empty States, System, Onboarding.

Historical working archives are not production sources.

## 15. Definition of Done

The UI Foundation is complete when:

- App Shell works on desktop, tablet and mobile;
- all seven navigation entries are present;
- approved navigation assets are used;
- official brand master is used;
- profile is accessible from avatar;
- no SVG is introduced;
- no generic icon substitution is introduced;
- action hierarchy is consistent;
- responsive workflows do not require horizontal scrolling;
- keyboard/focus behavior works;
- approved illustrations are used for relevant states;
- broad decorative assets remain absent except for the explicitly approved minimal TH-0089 header treatment;
- closed visual decisions remain unchanged;
- visual QA is performed at desktop, tablet and smartphone widths;
- existing frontend checks remain green.

## 16. Change control

If implementation exposes a genuine UX/design problem:

1. describe the problem;
2. describe impact;
3. propose variants;
4. recommend one;
5. obtain PO approval;
6. document the change;
7. only then implement it.

Do not silently change the baseline.

## 17. Implementation boundary

This document defines UX/UI behavior. Claude writes the frontend implementation. This specification does not authorize changes to backend contracts, domain rules, navigation IA, brand source or approved asset packages.

## Traceability

Previous stage: PR #75 — production UI asset hand-off, merged.  
Current stage: TH-0088 / Issue #95 — App Shell / UI Foundation.
