# TourCRM — Design System Specification

## 1. Назначение

Документ определяет единые правила визуального и компонентного поведения интерфейса TourCRM. Его цель — обеспечить единообразие реализации между модулями и сократить количество локальных UI-решений.

Это функционально-визуальный контракт, а не финальный графический макет.

---

## 2. Design principles

1. Clarity over decoration.
2. Consistency over local optimization.
3. Data density is adaptive: high on desktop, simplified on mobile.
4. Primary action is visually dominant and unique per context.
5. System state is always visible where it affects an action.
6. Sensitive information uses deliberate visual grouping.
7. Components must remain usable without color alone.
8. All patterns must support loading, empty, error and disabled states.

---

## 3. Layout system

Application uses a consistent content container and spacing scale.

Required layout primitives:

- Page;
- Section;
- Stack;
- Inline/Row;
- Grid;
- Card;
- Split layout;
- Drawer/Sheet;
- Modal;
- Sidebar;
- Bottom action bar.

Modules must compose these primitives rather than defining arbitrary margins and widths repeatedly.

---

## 4. Typography

Use a platform-compatible sans-serif stack with clear hierarchy.

Required semantic levels:

- Display;
- H1;
- H2;
- H3;
- body;
- body-small;
- caption;
- label;
- helper/error text.

Text hierarchy must be semantic rather than tied to component-specific names.

---

## 5. Color semantics

Exact palette is configurable, but semantic roles are fixed:

- primary — main action/navigation;
- success — completed/healthy;
- warning — attention/expiring;
- danger — destructive/error;
- info — neutral information;
- muted — secondary information;
- surface/background;
- border/divider.

A component must never encode a business state by color only.

---

## 6. Status badges

Standard statuses must use the common Badge component.

Examples:

- Active;
- Pending;
- Draft;
- Published;
- Cancelled;
- Archived;
- Expired;
- Present;
- Absent;
- Scheduled;
- Completed;
- Failed.

Status labels are localized in UI; canonical status identifiers remain English.

---

## 7. Buttons

Variants:

- primary;
- secondary;
- tertiary/text;
- danger;
- icon-only.

Button rules:

- one primary action per local context where possible;
- destructive actions use danger variant;
- loading disables repeated submission;
- icon-only buttons require accessible name and tooltip where appropriate.

---

## 8. Forms

Common form components:

- TextInput;
- TextArea;
- Select;
- MultiSelect;
- Combobox/SearchSelect;
- DatePicker;
- TimePicker;
- DateTimePicker;
- Checkbox;
- RadioGroup;
- Switch;
- FileUpload;
- TagInput;
- NumericInput;
- CurrencyInput.

All components must support:

- label;
- required state;
- helper text;
- validation error;
- disabled state;
- read-only state;
- loading where applicable.

---

## 9. Tables

Desktop table requirements:

- sticky header for long datasets;
- sortable columns where defined by module;
- pagination;
- row actions;
- column visibility only where justified;
- accessible headers;
- keyboard navigation.

Mobile tables convert to cards/list rows unless the dataset is specifically suitable for horizontal scrolling.

Actions should not be hidden behind ambiguous icons when a text label is important.

---

## 10. Cards

Cards are used for:

- summary metrics;
- event previews;
- person summaries;
- dashboard widgets;
- knowledge article previews;
- equipment entries.

Cards must not become alternative data models. They are presentation of existing domain data.

---

## 11. Modal and drawer behavior

Use modal for:

- short confirmation;
- focused small forms;
- destructive confirmation.

Use drawer/sheet for:

- filters;
- secondary detail;
- mobile contextual actions;
- medium workflows where context must remain visible.

Do not put long multi-step workflows into modal windows unless explicitly documented.

---

## 12. Confirmation dialog

Standard confirmation contains:

- title;
- consequence;
- affected object;
- cancel;
- confirm action;
- reason field if required.

For destructive actions, the confirm action must be visually distinct.

---

## 13. Notification UI

Standard toast/snackbar categories:

- success;
- info;
- warning;
- error.

Transient notification is not a substitute for persistent state. Important failures must also remain visible on the relevant page.

---

## 14. Alerts

Use inline Alert for persistent contextual messages.

Examples:

- account pending approval;
- document expiring;
- trip has unresolved data;
- integration unavailable;
- payment overdue.

Alert should link directly to the resolving action where possible.

---

## 15. Empty states

Every collection component must define an empty state.

Empty state contains:

- short explanation;
- optional illustration/icon;
- primary action when user can create data;
- distinction from permission denied and search with no results.

---

## 16. Loading states

Use skeletons for content-heavy screens and spinners for localized actions.

Buttons use inline loading indicator during requests.

Do not blank a whole page for a small secondary request.

---

## 17. Error states

Errors are classified:

- validation;
- permission;
- conflict;
- not found;
- business rule;
- network;
- server.

UI must map these classes to the common patterns defined by UX specification.

---

## 18. Search

Common SearchInput behavior:

- debounce where appropriate;
- explicit loading state;
- clear action;
- keyboard-friendly suggestions;
- accessible announcement of result changes.

Global search and module-local search remain distinct concepts.

---

## 19. Filters

Filters use a shared FilterBar on desktop and FilterSheet on mobile.

Applied filters should be visibly summarized and removable individually.

Filters must be serializable in URL/query state where this supports navigation/shareability.

---

## 20. Pagination

Default pattern: server-side pagination.

UI should display:

- current range;
- total when known;
- page controls;
- page size only where useful.

For mobile, compact navigation is preferred.

---

## 21. Dates and time

Use semantic date/time components.

Event times must be rendered with timezone context when the viewer's timezone differs from event timezone or ambiguity is possible.

Date formatting follows locale settings.

---

## 22. File upload

Common FileUpload must show:

- selected filename;
- size;
- upload progress;
- validation errors;
- retry;
- remove before commit.

For sensitive documents, UI must show access scope and retention implications where required.

---

## 23. Maps and geodata

Map component is used for routes and geographic context only where useful.

Requirements:

- accessible textual alternative for important geographic facts;
- do not make map interaction the only way to inspect route points;
- loading/error state;
- mobile controls sized for touch.

Exact map provider is an architectural/infrastructure decision, not hard-coded by individual module.

---

## 24. Charts and analytics

Charts must have:

- title;
- unit;
- period;
- understandable legend;
- accessible textual summary where possible.

Do not use charts when a simple table/metric is clearer.

Color is not the sole channel for distinguishing series.

---

## 25. Responsive behavior

Components must define behavior in three conceptual states:

- wide;
- medium;
- narrow.

Breakpoints are centralized design tokens and must not be hard-coded differently per module.

---

## 26. Accessibility contract

Common components must support:

- keyboard interaction;
- focus management;
- semantic labels;
- ARIA only where necessary;
- reduced motion preference where applicable;
- visible focus;
- sufficient non-color distinction.

Dialogs must trap focus appropriately and return focus to the launching control on close.

---

## 27. Internationalization

All user-visible text must come from localization resources.

No user-facing Russian strings should be hard-coded directly into reusable components.

English remains the canonical language for technical identifiers and keys.

---

## 28. Component ownership rules

Reusable components belong to the shared UI layer only when they are genuinely domain-neutral.

Domain-specific behavior belongs to domain modules.

Do not turn every one-off domain element into a global component prematurely.

---

## 29. Acceptance Criteria

1. Shared UI components have consistent states and APIs.
2. Modules use semantic status and form patterns consistently.
3. Desktop/tablet/mobile behavior is predictable.
4. Accessibility is built into shared primitives.
5. Localization does not require rewriting components.
6. Loading/error/empty patterns are standardized.
7. Destructive operations use one confirmation pattern.
8. New components are justified by reuse or domain clarity, not convenience alone.
9. Visual implementation remains compatible with the UX specification and permission model.
