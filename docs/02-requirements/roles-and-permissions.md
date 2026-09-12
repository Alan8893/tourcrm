# TourCRM — Roles and Permissions

## 1. Назначение

Документ определяет модель авторизации TourCRM. Он является нормативным для backend authorization и основанием для ограничения интерфейса.

## 2. Принципы

1. Role — набор permissions.
2. Permission — атомарное право на действие над ресурсом.
3. Scope — область действия permission.
4. Наличие роли не является достаточным условием доступа к конкретному объекту.
5. Backend является единственным источником истины для authorization.
6. Frontend может скрывать недоступные действия, но никогда не заменяет backend enforcement.

## 3. Базовые роли

### admin

Полное управление клубом в рамках системной модели.

Назначение: руководитель/администратор клуба.

### instructor

Операционная работа с группами, занятиями, мероприятиями, посещаемостью и туристской деятельностью.

### member

Участник клуба. Работа преимущественно со своими данными, мероприятиями и достижениями.

### guardian

Родитель/законный представитель. Доступ к данным связанных детей в пределах разрешённой политики.

## 4. Permission naming

Канонический формат:

`<resource>.<action>`

Примеры:

- `person.read`
- `person.update`
- `membership.read`
- `membership.manage`
- `group.read`
- `group.manage`
- `event.read`
- `event.create`
- `event.update`
- `event.cancel`
- `event.manage`
- `attendance.read`
- `attendance.update`
- `trip.read`
- `trip.manage`
- `achievement.read`
- `achievement.award`
- `knowledge.read`
- `knowledge.manage`
- `document.read`
- `document.manage`
- `consent.read`
- `consent.manage`
- `equipment.read`
- `equipment.manage`
- `finance.read`
- `finance.manage`
- `notification.read`
- `notification.manage`
- `audit.read`
- `settings.manage`
- `role.manage`

## 5. Scope model

Минимальные scopes:

- `all` — все объекты клуба;
- `own_groups` — участники/мероприятия групп, за которые пользователь отвечает;
- `self` — только собственные данные;
- `children` — данные связанных детей;
- `own_events` — мероприятия, где пользователь является ответственным/назначенным;
- `none` — право отсутствует.

В дальнейшем допускаются scopes на базе ownership/relationship и специализированные политики.

## 6. Authorization evaluation

Доступ к операции определяется минимум по следующим условиям:

`Authenticated User` + `Permission` + `Scope` + `Object relationship` + `Object status` + `Feature setting`.

Feature setting не может расширить permissions.

Пример:

`finance.enabled = false` означает, что финансовый домен недоступен функционально. `finance.enabled = true` не предоставляет `finance.manage` пользователю, у которого такого permission нет.

## 7. Базовая матрица ролей

| Ресурс/действие | admin | instructor | member | guardian |
|---|---:|---:|---:|---:|
| Auth/self account | ✅ | ✅ | ✅ | ✅ |
| Свой Person | ✅ | ✅ | ✅ | ✅ |
| Любой Person | ✅ | по scope | ❌ | ❌ |
| Управление membership | ✅ | ограниченно | ❌ | ❌ |
| Группы: чтение | ✅ | assigned | ограниченно | ограниченно |
| Группы: управление | ✅ | ❌ | ❌ | ❌ |
| Event: чтение | ✅ | ✅ | ✅ | ✅ |
| Event: создание | ✅ | ✅ | ❌ | ❌ |
| Event: изменение | ✅ | assigned/owned | ❌ | ❌ |
| Event: отмена | ✅ | по permission | ❌ | ❌ |
| Attendance: чтение | ✅ | assigned/owned | self | children |
| Attendance: изменение | ✅ | assigned/owned | ❌ | ❌ |
| Trip: чтение | ✅ | ✅ | self | children |
| Trip: управление | ✅ | assigned/owned | ❌ | ❌ |
| Achievement: чтение | ✅ | ✅ | self | children |
| Achievement: выдача | ✅ | ✅ | ❌ | ❌ |
| Knowledge: чтение | ✅ | ✅ | ✅ | ✅ |
| Knowledge: управление | ✅ | по permission | ❌ | ❌ |
| Documents: чтение | ✅ | по scope | self | children |
| Documents: управление | ✅ | по scope | ограниченно | ограниченно |
| Consent: чтение | ✅ | по необходимости | self | children |
| Consent: управление | ✅ | по policy | ❌ | ограниченно |
| Equipment: чтение | ✅ | по назначению | ❌ | ❌ |
| Equipment: управление | ✅ | по permission | ❌ | ❌ |
| Finance: чтение | ✅ | по permission | self-related | self-related |
| Finance: управление | ✅ | по permission | ❌ | ❌ |
| Audit: чтение | ✅ | ❌ по умолчанию | ❌ | ❌ |
| System settings | ✅ | ❌ | ❌ | ❌ |
| Roles/permissions | ✅ | ❌ | ❌ | ❌ |

Матрица является базовой. Для чувствительных данных действуют дополнительные объектные ограничения.

## 8. Sensitive data

К sensitive domain относятся как минимум:

- документы;
- согласия;
- медицинская информация;
- финансовая информация;
- контактные данные несовершеннолетних;
- экстренные контакты.

Доступ к ним должен проверяться отдельно. Нельзя считать, что доступ к Person автоматически означает доступ ко всем дочерним объектам Person.

## 9. Self access

`self` применяется только к данным, которые пользователь имеет право видеть о себе. Например, member может видеть свой профиль, свои мероприятия, свои достижения и свою историю посещения, но не получает право просматривать другого member через подмену идентификатора ресурса.

## 10. Guardian access

`children` не означает доступ к любому ребёнку в клубе. Сервис должен вычислять допустимых детей через активные GuardianRelationship.

Удалённая/неактивная связь автоматически прекращает актуальный доступ, если отдельное правило не требует сохранения read-only исторического доступа.

## 11. Instructor scope

Инструктор получает доступ только к объектам, для которых он назначен ответственным или которые принадлежат его группам, в соответствии с конкретным permission.

Роль instructor не должна автоматически давать доступ ко всем членам клуба.

## 12. Admin

Admin обладает расширенными правами клуба, но системные операции уровня инфраструктуры/операционной системы не являются частью application admin и не должны имитироваться внутри CRM.

## 13. Multiple roles

Один пользователь может иметь несколько ролей.

Итоговые permissions формируются объединением permissions назначенных ролей с последующей проверкой scope и object-level policies.

Будущие explicit denies допускаются только после отдельного ADR, поскольку неверная реализация deny поверх role union может сделать модель трудно предсказуемой.

## 14. Audit requirements

Изменения следующих прав должны аудироваться:

- назначение/снятие роли;
- изменение permissions;
- изменение системных/feature settings;
- доступ к чувствительным административным функциям, если это будет предусмотрено policy.

## 15. UI requirements

Frontend должен:

- не показывать пользователю заведомо недоступные действия, где это улучшает UX;
- корректно обрабатывать HTTP 401/403;
- не полагаться на скрытие кнопок как на security mechanism;
- предотвращать случайные действия вне scope через форму и навигацию.

## 16. Future roles

Архитектура должна допускать появление ролей:

- club_manager;
- senior_instructor;
- trainee_instructor;
- finance_manager;
- document_manager;
- medical_responsible;

без переписывания модели authorization.
