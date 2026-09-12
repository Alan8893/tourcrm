# ADR-0017 — Identity and authorization documentation canonicalization

## Status

Accepted

## Context

После реализации identity foundation были обнаружены несколько расхождений между логической моделью данных, физической схемой и security contract. Эти расхождения создавали риск того, что последующие implementation issues будут трактовать один и тот же контракт по-разному.

## Decision

Зафиксировать следующие правила:

1. В `data-model.md` общее требование стабильного `id` относится к самостоятельным доменным сущностям. Чистые association/junction tables без самостоятельной доменной идентичности могут иметь составной первичный ключ, если это явно определено физической database specification.
2. `Person` не имеет отдельного `status` в canonical identity schema. Жизненный цикл учётной записи определяется `User.status`, а членства — `ClubMembership.status`.
3. `roles-and-permissions.md` является каноническим источником каталога permissions. `auth-and-authorization.md` не вводит альтернативные permission codes.
4. Канонический scope vocabulary определяется `ADR-0013`: `all`, `self`, `children`, `own_groups`, `own_events`, `none`. `assigned_events` является alias `own_events`, а `own_records` не является scope.
5. Термины состояний `User` в security contract должны соответствовать identity foundation: `pending`, `active`, `locked`, `suspended`, `disabled`, `archived`.
6. Базовая архитектура authentication уже принята `ADR-0009`; ODR-001 не является открытым решением. Конкретные библиотеки и версии остаются implementation detail.

## Consequences

- Canonical documents no longer contain conflicting identity/authorization terminology.
- Future implementation issues may reference a single permission catalog and scope vocabulary.
- Concrete role→permission grants remain a separate product/authorization decision and are not inferred by this ADR.
