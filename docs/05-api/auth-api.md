# TourCRM — Authentication API

## 1. Назначение

Документ определяет детальный API-контракт домена аутентификации. Он является обязательным источником требований для реализации backend, frontend и автоматических security-тестов.

Общие правила API описаны в `docs/05-api/api-contract.md`, а общие правила безопасности — в `docs/05-api/auth-and-authorization.md`.

## 2. Базовый префикс

Все endpoint'ы версии 1 публикуются под:

`/api/v1`

Authentication namespace:

`/api/v1/auth`

## 3. Общие требования

- Все даты/времена передаются в ISO 8601.
- Сервер является источником истины для времени жизни сессий и одноразовых токенов.
- Пароли никогда не возвращаются API.
- Секреты, password reset tokens и verification tokens не возвращаются в обычных response body.
- Ошибки используют единый формат из общего API-контракта.
- Все изменения security state должны создавать `AuditLog` / security event.
- Endpoint'ы, изменяющие состояние, должны быть защищены от replay там, где операция является одноразовой.

## 4. Регистрация

### POST `/api/v1/auth/register`

Создаёт заявку на регистрацию нового пользователя/Person.

### Request

Минимально:

```json
{
  "email": "user@example.org",
  "password": "...",
  "first_name": "Иван",
  "last_name": "Иванов",
  "middle_name": "Иванович",
  "birth_date": "2012-03-15"
}
```

Дополнительные поля профиля могут быть добавлены в соответствии с People API.

### Правила

1. Email/identifier должен быть нормализован.
2. Password должен пройти policy validation.
3. Если email уже связан с active user, создаётся `409 conflict`.
4. Если существующая Person совпадает по подтверждённому идентификатору, должна использоваться существующая Person, а не дублироваться автоматически.
5. Для новой регистрации создаётся пользователь со статусом `pending`, если политика клуба требует подтверждения администратора.
6. Администратор должен иметь способ одобрить заявку.
7. Должно быть возможно зарегистрироваться по invitation без прохождения стандартного публичного approval flow, если приглашение действительно и соответствует политике клуба.
8. Регистрация не выдаёт повышенные роли.

### Response `201`

Возвращает безопасное представление созданной заявки/учётной записи без credentials.

## 5. Проверка email

### POST `/api/v1/auth/verify-email`

Подтверждает одноразовый verification token.

### Rules

- token одноразовый;
- token имеет срок действия;
- повторное использование возвращает детерминированный безопасный результат;
- истёкший token не может активировать учётную запись;
- security event фиксируется.

## 6. Повторная отправка подтверждения

### POST `/api/v1/auth/resend-verification`

Принимает email/identifier.

Нельзя раскрывать существование учётной записи через различающиеся ответы для существующего и несуществующего email.

## 7. Логин

### POST `/api/v1/auth/login`

Аутентифицирует пользователя.

### Request

```json
{
  "identifier": "user@example.org",
  "password": "..."
}
```

### Response

```json
{
  "user": {...},
  "session": {
    "expires_at": "..."
  }
}
```

При cookie-based session access credential не помещается в JSON.

### Rules

- при неверных credentials используется безопасная унифицированная ошибка;
- статус `pending`, `blocked`, `inactive`, `archived` обрабатываются отдельно на уровне доменной политики, но не должны раскрывать лишние сведения атакующему;
- после успешного входа обновляется `last_login_at`;
- фиксируется security audit event;
- применяется rate limiting/brute-force protection.

## 8. Logout

### POST `/api/v1/auth/logout`

Инвалидирует текущую сессию.

Операция идемпотентна.

Security event должен фиксироваться как logout, если сессия существовала.

## 9. Текущая сессия

### GET `/api/v1/auth/me`

Возвращает текущего authenticated User, Person, активные роли и контекст клуба.

Нельзя возвращать секреты или внутренние security credentials.

## 10. Список активных сессий

### GET `/api/v1/auth/sessions`

Доступно пользователю для просмотра собственных активных сессий.

Администратору отдельный административный endpoint может дать расширенный доступ при наличии permission.

Минимальные данные сессии:

- session id / безопасный идентификатор;
- created_at;
- last_seen_at;
- expires_at;
- approximate client metadata;
- current flag.

Чувствительные данные, например raw token, не возвращаются.

## 11. Отзыв одной сессии

### DELETE `/api/v1/auth/sessions/{session_id}`

Пользователь может отозвать собственную сессию.

Удаление/отзыв чужой сессии требует отдельного permission.

## 12. Logout all

### POST `/api/v1/auth/logout-all`

Инвалидирует все активные сессии пользователя, включая текущую, согласно security policy.

## 13. Запрос на восстановление пароля

### POST `/api/v1/auth/password-reset/request`

Request:

```json
{
  "identifier": "user@example.org"
}
```

Ответ не раскрывает, существует ли пользователь.

При наличии подходящего пользователя система создаёт одноразовый reset challenge и инициирует разрешённый канал доставки.

## 14. Установка нового пароля

### POST `/api/v1/auth/password-reset/confirm`

Request:

```json
{
  "token": "...",
  "new_password": "..."
}
```

Rules:

- token single-use;
- token expiry enforced;
- password policy enforced;
- после успешной смены пароля ранее активные сессии по умолчанию инвалидируются, кроме новой authentication flow, если так требует security policy;
- создаётся security audit event.

## 15. Change password

### POST `/api/v1/auth/password/change`

Требует authenticated session.

Request:

```json
{
  "current_password": "...",
  "new_password": "..."
}
```

Текущий password должен быть проверен до изменения.

## 16. Invitation API

### POST `/api/v1/auth/invitations`

Создание приглашения доступно пользователю с permission `membership.invitation.create`.

Минимально invitation содержит:

- target identifier/email, если известен;
- предполагаемую роль/тип membership, если разрешено политикой;
- срок действия;
- одноразовость;
- optional group context.

Inviter не должен иметь возможность назначить себе или другому пользователю более высокие полномочия без отдельного permission.

### POST `/api/v1/auth/invitations/accept`

Принимает invitation token и создаёт/связывает User/Person/Membership согласно правилам.

Token одноразовый.

## 17. Membership approval

Registration approval относится к домену membership, но auth API должен поддерживать безопасную связь между authentication account и pending membership workflow.

Реализация не должна активировать пользователя только из-за email verification, если бизнес-правило требует административного approval.

## 18. Brute-force protection

Для login и password recovery обязательно:

- rate limiting;
- progressive delay или иное противодействие перебору;
- audit/security events;
- безопасная унификация ошибок.

Параметры throttling должны быть конфигурируемыми.

## 19. CSRF / browser security

Если используется cookie-based auth, state-changing endpoint'ы должны быть защищены от CSRF согласно выбранной реализации.

Cookie flags должны соответствовать security policy: `HttpOnly`, `Secure` в Internet deployment и корректный `SameSite`.

Если приложение должно работать в LAN по HTTP, документация deployment должна отдельно определить допустимый режим development/internal network и риски. Production internet deployment не должен рассчитывать на plain HTTP.

## 20. Authorization

Authentication сам по себе не даёт доступа к данным клуба.

После аутентификации каждый защищённый endpoint выполняет authorization check:

`User -> RoleAssignment -> Permission -> Scope -> Resource`

Роль или факт регистрации не является заменой permission check.

## 21. Аудит security events

Как минимум аудитируются:

- registration;
- email verification;
- login success/failure;
- logout;
- password reset request;
- password reset success/failure;
- password change;
- invitation create/accept/revoke;
- account block/unblock;
- session revoke.

Аудит не должен сохранять пароль, raw tokens или иные credentials.

## 22. Критерии приёмки

1. Невалидные credentials не раскрывают существование пользователя.
2. Pending account не получает доступ, который должен появиться только после approval.
3. Verification token одноразовый и ограничен по времени.
4. Reset token одноразовый и ограничен по времени.
5. Password policy применяется единообразно.
6. Logout инвалидирует сессию.
7. Logout-all отзывает все активные сессии.
8. Пользователь не может отозвать чужую сессию без permission.
9. Регистрация через invitation соблюдает scope приглашения.
10. Никакой auth endpoint не возвращает secret credentials.
11. Login/password recovery защищены от brute force.
12. Security events попадают в audit log.
13. Authorization выполняется отдельно от authentication.
14. API соответствует общей схеме ошибок и идемпотентности.
