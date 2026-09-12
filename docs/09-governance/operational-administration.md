# TourCRM — Operational Administration Specification

## 1. Назначение

Определяет административные функции, необходимые для эксплуатации системы после deployment.

## 2. Bootstrap

При первичном развёртывании должен существовать безопасный способ создать первого administrator без hardcoded credentials.

Bootstrap credentials не должны попадать в source control или image.

После успешного bootstrap временный bootstrap mechanism должен быть disabled/rotated согласно security policy.

## 3. Reference data

Administrative interface должна позволять управлять документированными справочниками, например:

- event types;
- tourism types;
- difficulty categories;
- equipment categories;
- achievement categories;
- document types;
- absence reasons.

Критические справочники, влияющие на historical meaning, должны быть version-aware либо изменяемы только через controlled workflow.

## 4. Feature settings

Администратор с соответствующим permission должен иметь доступ к feature settings согласно feature-settings specification.

Изменения должны быть audit-able.

## 5. Integrations

Operations UI должна отображать состояние настроенных integrations:

- enabled/disabled;
- configuration validity;
- last successful operation;
- last failure;
- safe diagnostics.

Secrets не показываются в открытом виде.

## 6. Maintenance mode

Должен существовать controlled maintenance mode с:

- explicit activation;
- audit;
- user-facing maintenance message;
- ability to restrict non-administrative traffic;
- safe recovery.

## 7. Background jobs

Operations должны видеть:

- queued/running/failed/completed state;
- retry count;
- created/started/completed timestamps;
- safe error information;
- ability to retry/requeue разрешённых задач.

Manual replay должен быть idempotent-aware.

## 8. Backup status

Администратор/оператор должен видеть:

- latest successful backup;
- age;
- failure state;
- restore-test status, если доступно;
- storage health.

## 9. System health

Operations dashboard должен агрегировать:

- application readiness;
- DB health;
- worker health;
- storage health;
- notification integration health;
- certificate status, если доступно;
- disk/resource thresholds.

## 10. Audit access

Просмотр AuditLog требует отдельного permission. Export аудита является privileged action и сам подлежит аудиту.

## 11. Dangerous operations

Следующие действия должны иметь дополнительную защиту:

- bulk data deletion/archive;
- user disable;
- reprocessing large batches;
- restore-related operations;
- migration/destructive maintenance;
- changing security-critical settings.

## 12. Acceptance criteria

- [ ] безопасный bootstrap administrator;
- [ ] reference data administration;
- [ ] feature settings management;
- [ ] integrations status;
- [ ] maintenance mode;
- [ ] background job management;
- [ ] backup health visibility;
- [ ] system health dashboard;
- [ ] privileged audit access;
- [ ] dangerous operations имеют safeguards.
