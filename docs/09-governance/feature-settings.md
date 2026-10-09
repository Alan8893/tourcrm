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

Secrets, connection strings и deployment-specific credentials не являются обычными feature settings. Однако интеграционные секреты, которыми должны управлять администраторы без доступа к серверной инфраструктуре, могут предоставляться через специально защищённый административный UI по отдельному ADR. Такой UI обязан использовать write-only секретные поля, шифрование at rest, строгую авторизацию, аудит без значений секретов и никогда не возвращать сохранённые значения клиенту.


## 10. Notification settings

Notification settings are governed by ADR-0045.

The notification configuration has three distinct categories:

1. **Feature policy** — whether a channel/function is enabled for the installation.
2. **Integration configuration** — provider configuration such as SMTP host/port/from address and Telegram bot identity.
3. **Secrets** — SMTP passwords, Telegram bot tokens and other credentials.

For the current single-club installation, notification administration uses one Global Admin Policy. There is no separate Club Admin Policy API or UI. Existing nullable `club_id` fields are retained for compatibility, but the admin UI manages installation-wide rules only (`club_id = NULL`).

Integration configuration and secrets are managed through the protected administrator Settings UI under ADR-0048. Secret values are encrypted at rest, write-only after save, and never returned by API. The UI shows a masked placeholder and configured/not-configured status; replacing or clearing a secret is a separate explicit action. The encryption key is provided by deployment secret configuration and is not stored beside ciphertext.

The effective notification decision follows (ADR-0045 §2.4):

```text
Global Admin Policy
    ↓
Notification Rule
    ↓
User Preference
    ↓
Delivery
```

A narrower level can only restrict a broader one, never expand it; Global OFF cannot be overridden by a Notification Rule or User Preference.

**Admin OFF always overrides User ON.** A user preference can never re-enable a channel or event disabled by the effective administrator policy.

Global Admin Policy is the policy source of the Notification Engine, consumed through an Admin Policy port (ADR-0045 §2.10). The persistence and UI that store and edit it are the Administrator Notification Settings implementation slice (#317); the Notification Engine (#319) does not implement Settings persistence or UI. Until a policy source is connected, the Engine fails closed. Secret-management details are defined by ADR-0048.

Notification configuration changes must not grant permissions or bypass resource authorization.

For notification settings that are stored in the settings catalog, each definition must state:

- scope;
- default;
- who may change it;
- audit requirement;
- activation semantics;
- dependency on channel integration configuration.

## 11. Acceptance criteria

- [ ] существует единый каталог settings;
- [ ] для каждой настройки определён scope;
- [ ] default value определено;
- [ ] ACL на изменение определён;
- [ ] изменения валидируются и аудируются согласно политике;
- [ ] feature flags не обходят authorization;
- [ ] disabled feature не ломает unrelated modules;
- [ ] settings имеют deterministic behavior after restart.
