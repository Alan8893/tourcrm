# ADR-0005: Identity и access control

## Статус
Accepted

## Решение

Identity разделяется на `Person` и `User`. Membership и role assignments не встраиваются в authentication account.

Authorization строится как RBAC + scope:

`User -> Role -> Permission -> Scope`.

Доступ к данным, относящимся к детям, дополнительно проверяется через активную `GuardianRelationship` и соответствующий permission/scope.

Основной web authentication механизм должен использовать безопасные серверно-контролируемые сессии или эквивалентный механизм с ротацией credentials; конкретный протокол и библиотека фиксируются в security implementation spec до реализации.

Пароли хранятся только как современные password hashes; plaintext/password-equivalent secrets не сохраняются.

## Причины

Такая модель поддерживает многоролевого человека, родителей с несколькими детьми и разграничение прав без дублирования Person records.

## Consequences

Authorization должна выполняться server-side на каждом защищённом endpoint. Скрытие UI элемента не является механизмом безопасности.

Изменения ролей, статусов аккаунта, login credentials и критичных security settings должны попадать в audit/security log.
