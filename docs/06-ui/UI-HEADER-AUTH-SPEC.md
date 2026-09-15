# TourCRM — Header & Authentication Boundary Specification

**Status:** APPROVED BASELINE FOR TH-0089
**Owner:** Product Owner
**Scope:** App Shell Header, responsive Header composition, mobile navigation entry, unauthenticated boundary

## 1. Purpose

This document records the post-TH-0088 product acceptance decisions from real-server review. It extends the UI Foundation without reopening the closed navigation, action, status, or brand catalogs.

The implementation target is the visual direction approved during the PO review and the corresponding authentication boundary.

## 2. Product decision: Guest does not exist

`Guest` is not a TourCRM role.

An unauthenticated visitor is not an application user and must not be represented as a pseudo-user inside the authenticated App Shell.

Required conceptual flow:

`Unauthenticated → authentication entry → authenticated user → App Shell`

The App Shell is an authenticated application surface. Protected application pages and protected data/actions must not be presented as if they were available to a guest.

### Unauthenticated state

- No `Гость` label in the App Shell header.
- No fake/default profile identity.
- No application navigation presented as an authenticated experience.
- Authentication entry is the explicit boundary for unauthenticated users.
- After successful authentication, the user enters the existing App Shell.

### Authenticated state

- Existing 7-item navigation remains exactly as approved:
  1. Главная
  2. Люди
  3. Группы
  4. События
  5. Достижения
  6. Отчёты
  7. Настройки
- No role or navigation expansion is introduced by TH-0089.

## 3. Header — desktop

The Header remains a compact single-row application header.

### Brand

- Use the existing official Vector master only.
- Increase the displayed logo size from the current implementation so the club identity is immediately readable.
- Preserve the official asset, proportions, and visual appearance.
- Do not create alternate logo variants.
- Do not introduce SVG.

### Tourism visual treatment

Add a restrained TourCRM-specific tourism/nature illustration treatment inside the Header brand zone.

Direction:
- mountains / forest / trail / tent / water may form a light panoramic composition;
- the treatment is atmospheric rather than informational;
- it must remain secondary to the logo and application controls;
- it must not become a large hero banner;
- it must not materially reduce usable content height;
- it must not interfere with contrast, focus states, navigation, notifications, or profile controls.

The approved visual reference combines a light tourism panorama with the existing warm TourCRM UI language.

This is a targeted Header treatment, not approval to reopen the deferred general Decorative P2 catalog.

## 4. Header — mobile

Mobile receives a dedicated composition. The desktop Header must not simply be scaled down.

Target structure:

`[Меню] [larger official logo] [notifications/profile when applicable]`

Requirements:
- compact height;
- larger, readable logo;
- clear menu control;
- profile/notifications remain available when applicable;
- no unnecessary controls;
- no horizontal scrolling;
- preserve vertical content space.

A very light tourism/nature accent may appear below or within the Header, but it must remain visually restrained and must not turn the Header into a banner.

## 5. Mobile navigation drawer

The drawer remains the full approved seven-item navigation.

Requirements:
- prominent official logo/brand treatment at the top;
- clear standard close button (`×` conceptually; implementation must use an accessible control);
- existing active-state language;
- all seven navigation items available;
- no additional role-specific navigation;
- drawer overlay remains accessible and keyboard/screen-reader usable.

## 6. Visual character

TourCRM should feel:

- warm;
- friendly;
- modern;
- tourism-oriented;
- lightly playful;
- professional enough for a school tourism club's operational CRM.

Avoid:
- generic corporate ERP appearance;
- stock illustrations;
- generic icon-library decoration;
- excessive visual noise;
- child-oriented gamification of the shell;
- large decorative banners;
- SVG assets.

## 7. Responsive acceptance

Visual QA must cover at least:

- desktop: approximately 1440px wide;
- tablet: approximately 834px wide;
- smartphone: approximately 390px wide.

The Header must preserve hierarchy and usability at all three sizes.

## 8. Accessibility

- Semantic controls and landmarks.
- Visible keyboard focus.
- Accessible names for icon-only controls.
- No information conveyed by color alone.
- Sufficient contrast.
- Touch targets remain usable on mobile.
- Mobile drawer focus behavior remains correct.

## 9. Asset/change control

The following remain closed and unchanged:

- Navigation catalog;
- Actions catalog;
- Status catalog;
- official Brand master.

TH-0089 may introduce only the minimal new Header visual treatment required by this specification. It must not silently create a broad decorative asset package or modify closed catalogs.

## 10. Definition of Done

- `Гость` is removed from the authenticated App Shell concept.
- Unauthenticated users are stopped at the authentication boundary.
- Authenticated users retain the existing seven-item navigation.
- Official logo is visibly larger and remains the approved master.
- Desktop Header has restrained TourCRM tourism/nature visual treatment.
- Mobile Header is a dedicated compact composition.
- Mobile drawer has a clear close control and stronger brand treatment.
- No SVG, generic icon-library art, stock art, or broad decorative package is introduced.
- Desktop/tablet/mobile visual QA passes.
- Authentication behavior and Header behavior are covered by appropriate tests.
- No business/domain calculation logic is moved into the frontend.
