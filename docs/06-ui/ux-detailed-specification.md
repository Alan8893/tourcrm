# TourCRM — Detailed UX Specification

## 1. Назначение

Документ является контрактом информационной архитектуры и пользовательского интерфейса TourCRM. Он определяет структуру навигации, набор экранов, ключевые пользовательские сценарии, требования к формам, спискам, состояниям, responsive-поведению и role-based visibility.

Документ не является визуальным дизайн-макетом. Цветовая схема, typography tokens, конкретная component library и visual design system определяются отдельным design-system документом, но не могут нарушать описанные здесь информационные и функциональные требования.

---

## 2. UX-принципы

1. Пользователь всегда понимает, где он находится и к какому объекту относится текущий экран.
2. UI скрывает недоступные действия на основании permissions, но backend остаётся единственным источником истины для authorization.
3. Длинные операции показывают понятное состояние выполнения.
4. Ошибки формы отображаются рядом с соответствующим полем; системные ошибки имеют общий recovery path.
5. Удаление или необратимое действие требует подтверждения.
6. Исторические данные должны визуально отличаться от текущих, но оставаться доступными пользователям с соответствующим permission.
7. Основные операции должны быть доступны с телефона без горизонтального скролла.
8. Табличные desktop-представления на mobile преобразуются в карточки, списки или stacked rows.
9. Поиск, фильтрация и сортировка должны сохранять состояние при возврате на список.
10. UI не должен дублировать бизнес-логику, расчёты и правила доступа backend.

---

## 3. Целевые устройства

Поддерживаются:

- desktop;
- laptop;
- tablet;
- smartphone.

Основной режим — responsive web application.

Отдельное native mobile application в текущем scope отсутствует.

Минимальный поддерживаемый сценарий: пользователь способен выполнить основные пользовательские операции на смартфоне одной рукой или с минимальным количеством сложных жестов.

---

## 4. Information Architecture

### 4.1 Общая структура

```text
TourCRM
├── Dashboard
├── Calendar
├── People
│   ├── Members
│   ├── Guardians
│   ├── Instructors
│   ├── Groups
│   └── Invitations / Registrations
├── Events
├── Trips
├── Tourist Profile
├── Achievements
├── Knowledge Base
├── Documents
├── Equipment
├── Finance
├── Notifications
├── Reports / Analytics
└── Administration
    ├── Users
    ├── Roles & Permissions
    ├── Club Settings
    ├── Feature Settings
    ├── Reference Data
    ├── Integrations
    └── Audit Log
```

Не каждый раздел отображается каждой роли.

---

## 5. Navigation by role

### 5.1 Administrator

Основная навигация:

- Dashboard;
- Calendar;
- People;
- Events;
- Trips;
- Achievements;
- Knowledge Base;
- Documents;
- Equipment;
- Finance;
- Notifications;
- Reports;
- Administration.

### 5.2 Instructor

Основная навигация:

- Dashboard;
- Calendar;
- My Groups;
- Events;
- Trips;
- Members;
- Attendance;
- Achievements;
- Knowledge Base;
- Documents;
- Equipment;
- Notifications.

Разделы и действия внутри них ограничиваются scopes.

### 5.3 Member

Основная навигация:

- Home;
- My Calendar;
- My Profile;
- My Trips;
- My Achievements;
- My Skills / Qualifications;
- Knowledge Base;
- Documents;
- Notifications.

### 5.4 Guardian

Основная навигация:

- Home;
- Children;
- Calendar;
- Events;
- Trips;
- Documents;
- Notifications;
- Payments, если finance permissions/relationship policy разрешает доступ.

Для нескольких детей должен существовать child switcher без повторной авторизации.

---

## 6. Responsive navigation

### Desktop

Используется persistent sidebar с группировкой разделов.

### Tablet

Sidebar может быть collapsible.

### Mobile

Используется:

- top bar;
- contextual back navigation;
- bottom navigation для основных разделов либо компактное меню;
- overflow actions для второстепенных операций.

Ключевые действия текущего пользователя должны быть доступны максимум за 2 уровня навигации от home/dashboard.

---

## 7. Authentication screens

### 7.1 Login

Поля:

- identifier/email;
- password.

Действия:

- Login;
- Forgot password;
- Registration, если регистрация разрешена;
- invitation flow через token/link.

Состояния:

- idle;
- submitting;
- invalid credentials;
- account pending;
- account blocked;
- success.

Не раскрывать существование конкретной учётной записи через различающиеся сообщения там, где это создаёт account enumeration risk.

### 7.2 Registration

Шаги:

1. учетные данные;
2. Person data;
3. подтверждение контакта;
4. membership/club context;
5. подтверждение заявки;
6. ожидание approval, если требуется.

### 7.3 Invitation acceptance

При наличии invitation token часть данных может быть предварительно заполнена и недоступна для изменения.

### 7.4 Password reset

Отдельный request и confirmation flow с одноразовым токеном.

---

## 8. Dashboard

Dashboard адаптируется по роли.

### Administrator widgets

- members count;
- active groups;
- upcoming events;
- attendance alerts;
- expiring documents;
- financial summary;
- equipment issues;
- pending registrations;
- notification delivery failures;
- recent audit/security events при соответствующем permission.

### Instructor widgets

- today's events;
- next events;
- my groups;
- attendance tasks;
- pending member information;
- active trips;
- equipment assigned to instructor/events.

### Member widgets

- next event;
- upcoming trips;
- attendance summary;
- achievements;
- expiring own documents;
- unread notifications.

### Guardian widgets

- next child event;
- child attendance summary, если разрешено;
- upcoming trips;
- required documents/consents;
- pending payments if applicable;
- unread notifications.

---

## 9. People module

### 9.1 Member list

Desktop:

- searchable table;
- filters by group, status, age range, membership type;
- sorting;
- pagination;
- export where permitted.

Mobile:

- searchable card/list view;
- compact filter sheet;
- sort sheet.

### 9.2 Person profile

Profile must be visually segmented:

1. identity;
2. contacts;
3. club membership;
4. guardians;
5. groups history;
6. participation history;
7. trips;
8. tourist profile;
9. achievements;
10. qualifications;
11. documents;
12. finance;
13. audit, if authorized.

PII and sensitive information must not be mixed into unrestricted summary cards.

### 9.3 Edit profile

Fields grouped by domain ownership. System-calculated values are readonly.

Changes to protected fields may require elevated permission or audit event.

### 9.4 Guardian management

Administrator/authorized users can establish or revoke guardian relationships.

Guardian sees only currently authorized children.

### 9.5 Group management

Screens:

- group list;
- group detail;
- group members;
- instructors;
- group schedule;
- membership history.

---

## 10. Events and Calendar UX

### 10.1 Calendar views

Supported:

- month;
- week;
- day;
- agenda/list.

Mobile default: agenda/list or compact week depending on screen width.

### 10.2 Event detail

Sections:

- title/status;
- date/time/timezone;
- location;
- description;
- instructors/leaders;
- groups;
- participants;
- attendance;
- documents;
- related trip/competition/tour_slet;
- finance summary where allowed;
- notification status where allowed.

### 10.3 Create/edit event

Form supports:

- type;
- title;
- description;
- date/time;
- timezone;
- location;
- series/recurrence;
- groups;
- instructors;
- capacity where applicable;
- registration policy;
- notification settings.

### 10.4 Recurring event UX

The user must explicitly choose whether an edit applies to:

- this occurrence only;
- this and future occurrences;
- entire series.

The UI must clearly display the effect before confirmation.

### 10.5 Cancellation/reschedule

Cancellation requires reason.

Reschedule shows:

- current date/time;
- new date/time;
- affected occurrence;
- affected participants;
- notification impact.

---

## 11. Attendance UX

Instructor/authorized leader can mark attendance for an occurrence.

Fast mode:

- participant list;
- Present;
- Absent;
- Late/other configured statuses;
- bulk actions.

Reason field appears when status requires explanation.

Attendance edits after lock/close require elevated permission and are audited.

Mobile UI must support rapid marking without opening each participant detail screen.

---

## 12. Trips UX

### Trip list

Filters:

- tourism type;
- date;
- region;
- status;
- difficulty;
- leader.

### Trip detail

Tabs/sections:

- overview;
- route;
- participants;
- roles;
- GPX;
- statistics;
- documents;
- expenses;
- results;
- audit/history.

### Route editor

Supports:

- route metadata;
- points;
- GPX upload;
- map preview;
- distance/elevation summary;
- route version/history.

For mobile, editing the exact route geometry may be restricted to simplified workflows; viewing must remain supported.

---

## 13. Tourist Profile UX

Profile dashboard displays:

- trip count;
- confirmed distance;
- tourism types;
- qualifications;
- skills;
- achievements;
- experience history.

Derived metrics must show their source or allow navigation to source facts when the user has permission.

Example:

`Confirmed distance: 342 km`

Clicking metric should navigate to contributing trips where permitted.

---

## 14. Achievements, Skills and Qualifications

### Achievement catalog

Administrator manages achievement definitions.

### Member achievements

User can view:

- earned achievements;
- dates;
- issuer/source;
- description;
- evidence when available.

### Automatic achievement

UI must explain that achievement is system-generated and show the triggering rule/category, without exposing implementation details unnecessarily.

### Rating

If feature enabled:

- ranking page;
- own position;
- scoring explanation;
- period/filter controls.

If disabled, ranking must disappear from navigation and dashboards without breaking other modules.

---

## 15. Documents and Consents

### Document list

Filters:

- type;
- owner;
- status;
- expiration;
- related object.

### Document detail

Shows metadata first. Download/view is separate action subject to permission.

### Expiring document UX

Use warning states:

- normal;
- expiring soon;
- expired.

Exact thresholds come from system settings/business rules.

### Consent

Consent history shows:

- consent type;
- version;
- status;
- date;
- signer;
- evidence.

UI must never imply that a consent exists merely because a document upload exists.

---

## 16. Equipment UX

### Inventory

Searchable list with:

- inventory number;
- name;
- category;
- condition;
- status;
- storage location.

### Equipment detail

Sections:

- current state;
- metadata;
- issue history;
- repairs;
- related events;
- documents.

### Issue/return flow

Optimized for mobile and optional QR/barcode future integration.

Workflow:

`Select equipment → select recipient/event → confirm issue → later return → condition after → close transaction`.

---

## 17. Finance UX

### Financial dashboard

Shows, subject to permission:

- income;
- expenses;
- outstanding balances;
- event budgets;
- period comparison.

### Payment

Creation/edit follows domain validation. A payment cannot be silently reassigned after reconciliation without explicit correction workflow.

### Event finance

Event detail may show financial summary but not full club accounting data unless authorized.

### Member/guardian finance

Shows only records related to the authorized person/children and only according to policy.

---

## 18. Notifications and Communications UX

### Notification center

- unread/read;
- category;
- timestamp;
- source event;
- delivery status where permitted.

### Notification preferences

Per user/channel/category settings.

### Bulk messaging

Administrator/authorized instructor can select audience by allowed scope.

Before send, UI must show:

- recipient count;
- channels;
- message preview;
- potentially excluded users;
- confirmation.

Sensitive data must not be inserted into a message unless explicitly allowed by template policy.

---

## 19. Knowledge Base UX

### Home

- search;
- categories;
- recent;
- popular/featured when implemented;
- related content.

### Article

Sections:

- title;
- summary;
- body;
- tags;
- version;
- status;
- attachments;
- related events/skills/trips.

### Editing

Draft/published workflow with version history.

---

## 20. Administration UX

Administrator-only or permission-controlled screens:

- users;
- roles;
- permissions;
- feature settings;
- club settings;
- reference data;
- integrations;
- audit log;
- system health where exposed.

Sensitive administrative operations require explicit confirmation.

---

## 21. Search and filtering

Global search should search only resources permitted for the current user.

Search result categories may include:

- people;
- events;
- trips;
- knowledge articles;
- documents;
- equipment.

Each list screen must define:

- default sort;
- available filters;
- pagination;
- empty state;
- no-results state.

---

## 22. Forms

Common form requirements:

- explicit labels;
- required fields marked;
- inline validation;
- server-side validation errors mapped to fields;
- unsaved changes warning where appropriate;
- disabled submit during request;
- safe retry behavior;
- keyboard accessibility.

Forms handling dates/times must display timezone clearly when ambiguity is possible.

---

## 23. Loading / empty / error states

Every async screen must define:

### Loading

Skeleton or equivalent content placeholder.

### Empty

Explain what is empty and offer next action when appropriate.

### No results

Differentiate from empty dataset.

### Permission denied

Explain that access is restricted without revealing unauthorized data.

### Validation error

Show actionable corrections.

### Network/server error

Provide retry and preserve entered data where safe.

---

## 24. Destructive operations

Delete, archive, revoke, cancel, correction and financial reversal operations must:

1. explain the consequence;
2. identify affected object;
3. require explicit confirmation;
4. display additional reason field when business rule requires it;
5. produce audit event.

Where soft-delete/archive is the canonical behavior, the UI must not label the action as irreversible delete.

---

## 25. Accessibility

Minimum requirements:

- keyboard navigation;
- visible focus state;
- semantic form labels;
- adequate text contrast;
- errors associated with controls;
- no critical information conveyed by color alone;
- touch targets suitable for mobile use;
- screen-reader-friendly names for icon-only controls.

Target: WCAG 2.2 AA where technically applicable.

---

## 26. Localization

Primary UI language: Russian.

Architecture should be localization-ready.

Backend and domain identifiers remain language-neutral English names.

Dates, times, numbers and currency use locale-aware formatting.

---

## 27. Confirmation and feedback patterns

Use consistent patterns for:

- saved successfully;
- operation queued;
- operation failed;
- notification sent;
- document uploaded;
- import completed;
- import partially completed.

Success messages must not claim completion before backend confirms the operation.

---

## 28. Permission-aware UX

The UI should distinguish:

- hidden because action is not applicable;
- unavailable because user lacks permission;
- disabled because object state forbids operation.

Do not render editable controls and rely solely on backend rejection as the normal UX.

However, the backend must validate every request independently.

---

## 29. Mobile-specific requirements

Critical actions optimized for mobile:

- attendance marking;
- opening event detail;
- event confirmation;
- viewing next event;
- document upload;
- equipment issue/return;
- trip participant check;
- notifications.

Sticky bottom action bar may be used for one primary action on long forms.

Long forms should use sections or steps rather than a single uninterrupted page.

---

## 30. Cross-module navigation

Objects should expose contextual links:

- Member → trips/events/achievements/documents;
- Event → participants/trip/documents/expenses;
- Trip → route/participants/experience/documents;
- Equipment → issuing event;
- Knowledge article → related skill/event/trip;
- Achievement → source event/trip when available.

Links must respect permissions and not leak unauthorized existence through UI metadata.

---

## 31. UX telemetry

No analytics event should include raw sensitive PII unless explicitly justified and documented.

Technical telemetry may include:

- screen/view identifier;
- action identifier;
- latency;
- success/failure;
- non-sensitive error class.

---

## 32. Acceptance Criteria

1. Every role has a documented navigation model.
2. Every top-level domain has defined screens and key actions.
3. Mobile behavior is specified for all critical workflows.
4. Loading/empty/error/permission states are defined as first-class UI states.
5. Recurring event operations clearly distinguish occurrence vs series changes.
6. Attendance can be performed efficiently on mobile.
7. Sensitive information is separated by domain and permission.
8. Cross-module navigation does not bypass authorization.
9. Feature settings can hide optional modules/features without invalid navigation.
10. All destructive and corrective operations have explicit confirmation and audit requirements.
11. The UI is localization-ready with Russian as primary language.
12. UX specification is compatible with the API and domain model documents.
