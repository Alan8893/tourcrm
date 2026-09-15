# TourCRM — Authentication Login UX Specification

## Status

APPROVED BASELINE

## 1. Purpose

This document defines the first-login/authentication UX for the TourCRM web application. It is a frontend contract and does not change the backend authentication API.

## 2. Canonical backend flow

The login page consumes the existing authentication contract:

1. User enters `identifier` and `password`.
2. Frontend sends `POST /api/v1/auth/login`.
3. On success the backend establishes the server-side authenticated session.
4. Frontend loads the authenticated principal from `GET /api/v1/auth/me`.
5. User is routed into the authenticated TourCRM application shell.

The frontend must not implement its own token storage or identity authority.

## 3. Visual direction

Use the approved TourCRM Visual Foundation and official Brand master.

The login screen must feel like TourCRM, not like a generic corporate authentication portal:

- light warm neutral background;
- white/light rounded authentication surface;
- official `Вектор` logo in the brand zone;
- restrained purple accent;
- natural/warm supporting tones where already provided by the visual foundation;
- generous whitespace;
- friendly but mature typography;
- no generic stock authentication illustration;
- no decorative asset generation for this screen unless separately approved under the Decorative P2 process.

## 4. Desktop composition

Centered authentication composition with a clear visual hierarchy:

1. official logo;
2. heading `Вход в TourCRM`;
3. short supporting text explaining that the user enters their TourCRM account;
4. identifier field;
5. password field;
6. primary `Войти` action;
7. secondary `Забыли пароль?` action/link where the recovery flow is available;
8. optional registration/invitation entry only when a canonical product flow exists.

Do not place unrelated navigation items, dashboard content, or role-selection controls on the login screen.

## 5. Identifier field

Label: `Email или логин`.

Use the backend contract's `identifier` semantics. Do not assume email-only authentication in the frontend.

The field must have:

- visible label;
- autocomplete appropriate for username/email credentials;
- keyboard focus;
- clear validation state;
- no account-existence probing.

## 6. Password field

Label: `Пароль`.

Requirements:

- password input must be masked by default;
- provide an accessible show/hide control if the component library supports it without inventing a new visual asset;
- do not log or persist the password in frontend state beyond the minimum request lifecycle;
- support Enter to submit;
- clear, accessible error state.

## 7. Login states

### Idle

Form ready for input. Primary action enabled only when the form is valid enough to submit.

### Submitting

Prevent duplicate submission and communicate progress through the existing loading pattern. Do not replace the whole page with a blocking spinner.

### Success

Accept the server session and resolve `/auth/me`, then enter the authenticated application shell.

### Invalid credentials

Use the backend's generic authentication failure semantics. Do not say whether the identifier exists, whether the password alone was wrong, or whether an account is present.

Recommended user-facing copy:

`Не удалось войти. Проверьте логин и пароль.`

### Pending / disabled / locked / suspended / archived account

Do not reveal sensitive account-state details if the backend intentionally returns a generic authentication failure. The UI must respect the actual API error contract rather than infer account state from local data.

### Network/server error

Use the existing system/error visual language and provide a retry path without losing entered identifier. Never display raw backend exceptions.

## 8. Password recovery

When the canonical recovery endpoint is available in the current deployment, provide `Забыли пароль?` as a secondary action.

Recovery UI must preserve the backend's non-enumeration rule: the UI must not confirm that an account exists based on the submitted identifier.

## 9. Registration

Do not put a prominent `Регистрация` action on the login screen unless the currently approved product policy explicitly makes public registration available.

The existing backend registration flow creates `pending` users, so the UI must not imply that registration immediately grants access.

## 10. Bootstrap boundary

The login screen is NOT the initial-administrator setup screen.

Initial administrator creation is an operator-controlled deployment operation defined by ADR-0027. No public `Создать администратора` action appears on the login page.

A clean installation is prepared by the operator first; after bootstrap, the administrator uses this normal login screen.

## 11. Authenticated redirect

If an already authenticated user opens `/login`, the frontend should avoid showing the login form and redirect to the appropriate authenticated entry page according to the existing application routing policy.

After successful login, default destination is the authenticated Home page unless an explicit protected-route return target exists.

Return targets must be validated as internal application routes; never redirect to arbitrary external URLs supplied by query parameters.

## 12. Responsive behavior

### Tablet

Keep the same information architecture and composition while reducing surrounding whitespace as needed. Do not turn the login screen into a desktop dashboard layout.

### Mobile

Single-column, vertically centered or naturally stacked form:

- logo;
- heading/supporting text;
- identifier;
- password;
- primary action;
- recovery action.

Touch targets must remain accessible. No horizontal scrolling.

## 13. Accessibility

- semantic form structure;
- visible labels;
- logical tab order;
- Enter submits the form;
- visible keyboard focus;
- accessible names for icon-only controls;
- errors associated with their fields;
- error meaning not conveyed by color alone;
- sufficient contrast;
- no focus trap except where a modal is actually used.

## 14. Security boundaries

Frontend must never:

- decide whether a user is authenticated solely from local state;
- accept a client-supplied user ID as identity;
- store server session secrets in localStorage/sessionStorage;
- expose password hashes/tokens;
- distinguish existing vs non-existing accounts using client-side heuristics;
- treat hidden UI as an authorization mechanism.

Backend remains the source of truth for authentication and authorization.

## 15. Relationship to UI Foundation

This specification extends, but does not replace, `docs/06-ui/UI-FOUNDATION-SPEC.md`.

Use existing reusable components and approved assets wherever possible. Do not create a second visual language for authentication.

## 16. Acceptance criteria

- [ ] Login page uses official Brand master only.
- [ ] No SVG and no generic icon-library substitution.
- [ ] Form submits to existing `POST /api/v1/auth/login` contract.
- [ ] Successful login resolves `GET /api/v1/auth/me` and enters authenticated shell.
- [ ] Generic credential failure does not reveal account existence.
- [ ] Password is never persisted as a client-side credential store.
- [ ] Recovery respects non-enumeration semantics.
- [ ] No public administrator-bootstrap UI exists.
- [ ] Desktop/tablet/mobile layouts follow UI Foundation.
- [ ] Accessibility requirements are met.
- [ ] Network/server errors use existing system state patterns.
- [ ] No fabricated role-specific or account-specific data is shown before authentication.

## 17. Change control

Any change to authentication behavior, backend contracts, session semantics, registration policy, bootstrap behavior, brand assets, navigation, or visual foundation requires the relevant canonical document/ADR to be updated before implementation.
