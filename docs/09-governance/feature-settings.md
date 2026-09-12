# TourCRM — Feature Settings Governance

## 1. Назначение

Определяет правила системных и feature-настроек TourCRM. Настройки управляют доступностью необязательной функциональности, но никогда не заменяют security authorization.

## 2. Levels

Поддерживаемые уровни:

- system — глобальная настройка инсталляции;
- club — настройка конкретного клуба;
- role — настройка поведения для роли, если это имеет смысл;
- user — персональная пользовательская настройка.

Не все настройки обязаны поддерживать все уровни. Для каждой настройки разрешённый scope фиксируется в каталоге settings.

## 3. Setting schema

Каждая setting definition должна иметь:

- key;
- description;
- data type;
- allowed values/range;
- default value;
- scope;
- owner;
- who may change;
- whether change requires audit;
- whether change requires elevated privilege;
- activation/deactivation semantics;
- dependencies;
- backward compatibility notes.

## 4. Feature flags

Примеры:

- achievements.enabled;
- rating.enabled;
- finance.enabled;
- telegram.enabled;
- max.enabled;
- email.enabled;
- knowledge_base.enabled;
- equipment.enabled.

Feature flags должны применяться одинаково на API и UI.

## 5. Security rule

Feature setting не может предоставить permission, который отсутствует у пользователя.

Пример:

`finance.enabled = true` не даёт инструктору право просмотра finance, если у него отсутствует `finance.read`.

## 6. Defaults

Production default должен быть безопасным и консервативным.

Если интеграция не настроена, её feature flag не должен приводить к ошибкам рабочих сценариев.

## 7. Changes

Изменение настроек должно:

1. валидироваться;
2. записываться атомарно;
3. попадать в audit log, если настройка определена как auditable;
4. иметь predictable propagation semantics;
5. не оставлять приложение в частично валидном состоянии.

## 8. Cached settings

При использовании cache должны быть определены:

- TTL или invalidation strategy;
- поведение после рестарта;
- consistency expectations;
- fallback на canonical database value.

## 9. Configuration vs feature setting

Secrets, connection strings и deployment-specific credentials не являются feature settings.

Build/deployment configuration не должен храниться в обычном пользовательском settings UI.

## 10. Acceptance criteria

- [ ] существует единый каталог settings;
- [ ] для каждой настройки определён scope;
- [ ] default value определено;
- [ ] ACL на изменение определён;
- [ ] изменения валидируются и аудируются согласно политике;
- [ ] feature flags не обходят authorization;
- [ ] disabled feature не ломает unrelated modules;
- [ ] settings имеют deterministic behavior after restart.
