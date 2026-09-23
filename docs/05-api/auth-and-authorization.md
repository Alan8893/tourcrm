# TourCRM — Authentication & Authorization Contract

## 1. Цель

Документ задаёт обязательное поведение аутентификации и авторизации TourCRM. Он является частью security contract для frontend, backend и будущих внешних клиентов.

## 2. Identity model

Система разделяет:

- `Person` — физическое лицо;
- `User` — учётную запись;
- `ClubMembership` — членство в клубе;
- `RoleAssignment` — назначение роли;
- `GuardianRelationship` — связь законного представителя и ребёнка.

У пользователя может быть несколько ролей и несколько permission scopes.

## 3. Authentication methods

На первом этапе основной способ:

- email/логин + password.

Система должна быть спроектирована так, чтобы позже можно было добавить внешний identity provider без пересмотра доменной модели.

Пароли хранятся только в виде безопасного password hash. Plaintext password не сохраняется, не возвращается API и не попадает в logs/audit.

## 4. Account states

Состояния User, согласованные с identity foundation:

`pending` → аккаунт создан, но ещё не активирован;

`active` → обычный доступ;

`locked` → вход временно/политически запрещён;

`suspended` → доступ приостановлен административно или по policy;

`disabled` → доступ отключён административно;

`archived` → историческая запись, обычный вход невозможен.

Переходы между состояниями должны контролироваться backend.

## 5. Registration flows

Поддерживаются три основных пути.

### 5.1 Self-registration

```text
Registration request
→ validation
→ pending user
→ email/verification step if enabled
→ admin approval
→ active user
```

Самостоятельная регистрация не должна автоматически выдавать privileged role.

### 5.2 Invitation

```text
Admin creates invitation
→ secure one-time token
→ user opens invitation
→ token validation
→ registration/account linking
→ membership/role activation
```

Invitation token:

- одноразовый;
- имеет срок действия;
- не хранится в plaintext, если архитектура token persistence позволяет hash storage;
- после использования инвалидируется;
- должен быть защищён от enumeration.

### 5.3 Import

Импорт всегда создаёт `Person` вместе с `User` в рамках подтверждённого применения import batch.

Правила account provisioning:

- если в импортируемой строке есть `email`, создаётся `User.status = active` с `login_identifier = normalized(email)`; выдача first-access credential выполняется по каноническому account-provisioning flow;
- если `email` отсутствует, создаётся `pending-stub User`: `status = pending`, `login_identifier = NULL`, `password_hash = NULL`, без first-access credential;
- фиктивные login/email значения для обхода отсутствующего email запрещены;
- отсутствие email не является основанием для создания `Person` без `User`;
- `duplicate_exact` из preview не означает merge, update, overwrite или автоматическое переиспользование существующего `Person`/`User`;
- добавление email и последующая активация `pending-stub` выполняются существующим account-management flow.

Импорт не создаёт `User` отдельно от `Person`: эти identity/account изменения применяются согласованно в рамках import execution transaction.

## 6. Login

Login flow:

```text
credentials
→ rate limit
→ credential validation
→ account state check
→ session/token issue
→ audit/security event
```

При неуспешной попытке система не должна раскрывать, существует ли конкретный аккаунт.

## 7. Session strategy

Конкретный механизм session/token фиксируется `ADR-0009` на уровне базовой архитектуры; конкретные библиотеки и версии остаются implementation detail.

Требования:

- credentials должны иметь ограниченный срок действия;
- logout должен инвалидировать текущую session;
- сервер должен иметь возможность принудительно инвалидировать sessions пользователя;
- privileged changes должны учитывать revocation;
- хранение токенов в browser должно соответствовать выбранной security model;
- sensitive tokens не помещаются в URL.

Для browser-first приложения используется сервер-контролируемая session model с безопасной cookie policy; конкретная реализация определяется при реализации authentication в соответствии с `ADR-0009`.

## 8. Password policy

Минимальная политика должна включать:

- минимальную длину;
- блокировку известных скомпрометированных/слабых паролей, если это поддерживает выбранный механизм;
- отсутствие plaintext storage;
- rate limit на login.

Не требовать регулярной принудительной смены пароля без security justification.

## 9. Password reset

```text
request reset
→ rate limited response
→ one-time expiring token
→ new password
→ invalidate relevant existing sessions
→ audit security event
```

Response на запрос восстановления не должен раскрывать существование аккаунта.

## 10. Email verification

Если email используется как идентификатор или канал критичных уведомлений, система должна поддерживать verification state.

Пока email не подтверждён, привязанные к подтверждённому адресу функции могут оставаться ограниченными.

## 11. Authorization model

TourCRM использует RBAC + permission scopes.

Условие доступа:

```text
Authenticated User
AND required Permission
AND allowed Scope
AND resource relationship/ownership
AND resource state allows operation
```

Frontend visibility не является security boundary.

## 12. Base roles

### admin

Полное административное управление в пределах инсталляции/клуба, за исключением действий, которые отдельной policy явно ограничены.

### instructor

Рабочие операции с группами, занятиями, посещаемостью, походами и участниками в разрешённом scope.

### member

Доступ к собственному профилю, мероприятиям, своим результатам и другим данным в соответствии с privacy policy.

### guardian

Доступ к данным связанных детей в рамках действующей GuardianRelationship и разрешённых permissions.

## 13. Scopes

Канонический набор scopes определяется `ADR-0013` и используется без альтернативных значений:

- `all` — все объекты клуба;
- `self` — только собственные данные;
- `children` — данные связанных детей;
- `own_groups` — участники/мероприятия групп, за которые пользователь отвечает;
- `own_events` — мероприятия, где пользователь является ответственным/назначенным;
- `none` — право отсутствует.

`assigned_events` не является отдельным scope и рассматривается как alias `own_events`. `own_records` не является каноническим scope.

Дополнительные scopes не должны вводиться без отдельного архитектурного/продуктового решения.

## 14. Guardian access

Наличие связи GuardianRelationship является необходимым, но не всегда достаточным условием доступа.

Проверяются:

1. активность связи;
2. permission `guardian_relationship.read` (или `guardian_relationship.manage` для изменяющих операций) — ADR-0025 §2;
3. scope `children`;
4. принадлежность ребёнка к тому же Club;
5. privacy restrictions.

При отзыве связи доступ должен прекращаться для новых запросов.

## 15. Multiple roles

Один User может иметь одновременно несколько ролей.

Например:

`admin + instructor + guardian`.

Effective permissions вычисляются как объединение разрешённых role assignments с учётом scope и resource policies.

Explicit deny policy допускается, если она будет введена отдельным ADR; по умолчанию role permissions additive.

## 16. Role assignment

Назначение роли должно быть отдельным административным действием и попадать в audit.

Изменение ролей не должно менять исторические записи о действиях пользователя.

При снятии privileged role активные sessions могут потребовать принудительной инвалидации в зависимости от security policy.

## 17. Permission naming

Канонический каталог permissions определяется `docs/02-requirements/roles-and-permissions.md` §4. Формат:

`<resource>.<action>`

Канонические permissions:

- `person.read`;
- `person.update`;
- `membership.read`;
- `membership.manage`;
- `group.read`;
- `group.manage`;
- `event.read`;
- `event.create`;
- `event.update`;
- `event.cancel`;
- `event.manage`;
- `attendance.read`;
- `attendance.update`;
- `trip.read`;
- `trip.manage`;
- `achievement.read`;
- `achievement.award`;
- `knowledge.read`;
- `knowledge.manage`;
- `document.read`;
- `document.manage`;
- `consent.read`;
- `consent.manage`;
- `equipment.read`;
- `equipment.manage`;
- `finance.read`;
- `finance.manage`;
- `notification.read`;
- `notification.manage`;
- `audit.read`;
- `settings.manage`;
- `role.manage`.

Этот список является каноническим каталогом. Примеры из старых версий этого документа (`member.read`, `member.update`, `equipment.issue`, `document.download`) не являются permissions TourCRM и не должны использоваться в реализации.

## 18. Authorization failure

Если пользователь не аутентифицирован: `401`.

Если пользователь аутентифицирован, но не имеет права: `403`.

Если ресурс по privacy policy должен выглядеть несуществующим для данного пользователя, допускается безопасное использование `404`, но правило должно быть единообразным по классу ресурсов.

## 19. Login abuse protection

Security-sensitive endpoints должны иметь rate limiting и защиту от brute force.

Минимум:

- login;
- password reset request;
- registration;
- invitation acceptance;
- verification.

Точные лимиты — deployment configuration, а не hardcoded business rule.

## 20. Security events

Минимально аудитируются:

- successful login;
- failed login policy event;
- logout, если необходимо для расследований;
- password change;
- password reset;
- account state changes;
- role changes;
- permission policy changes;
- invitation create/revoke/use;
- consent/security-sensitive changes.

Не хранить в security events пароли, access tokens или другие секреты.

## 21. Administrative impersonation

Имперсонация администратора не является частью MVP.

Если потребуется, она должна быть отдельным security feature с:

- явным включением;
- ограниченным сроком;
- полным audit trail;
- невозможностью скрыть исходного администратора.

## 22. CSRF / browser security

Если используется cookie-based browser session, backend должен реализовать CSRF protection по выбранному framework pattern.

CORS не используется как механизм authorization.

Security headers и cookie flags (`Secure`, `HttpOnly`, `SameSite`) определяются production deployment profile.

## 23. Privacy boundaries

Родители не получают автоматически все данные клуба о ребёнке. Каждая категория данных должна иметь documented access policy.

Особенно отдельно должны контролироваться:

- документы;
- согласия;
- медицинские/чувствительные данные;
- финансовая информация;
- instructor notes;
- audit data.

## 24. API requirements

Каждый защищённый endpoint должен декларировать required permission/scope в backend contract.

Authorization checks должны выполняться до mutation и до выдачи защищённых данных.

Frontend не должен получать privileged fields только потому, что пользователь скрывает соответствующий UI элемент.

## 25. Testing requirements

Обязательные группы тестов:

- login success/failure;
- locked/disabled account;
- password reset;
- invitation expiry/reuse;
- self-registration approval;
- role combination;
- permission allow/deny;
- scope isolation;
- guardian child access;
- revoked guardian access;
- instructor group isolation;
- admin-only operations;
- direct API access without UI;
- session invalidation after security changes.

## 26. Non-goals for MVP

Не входят в обязательный MVP:

- WebAuthn/passkeys;
- MFA;
- social login;
- enterprise SSO;
- admin impersonation.

Архитектура не должна препятствовать их добавлению позднее.
