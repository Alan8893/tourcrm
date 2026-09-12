# TourCRM

CRM и информационная система школьного туристского клуба.

## Назначение

TourCRM объединяет управление участниками клуба, родителями, инструкторами, группами, занятиями, походами, достижениями, документами, финансами, снаряжением, базой знаний и внешними мероприятиями.

## Основные пользователи

- администратор клуба;
- инструктор;
- участник клуба;
- родитель / законный представитель.

Один пользователь может иметь несколько ролей.

## Архитектура

Начальная архитектура — модульный монолит:

- frontend: React + TypeScript + Vite;
- backend: Python + FastAPI + Pydantic + SQLAlchemy;
- database: PostgreSQL;
- optional infrastructure: Redis;
- deployment: Docker Compose на Linux;
- CI: GitHub Actions.

Подробнее: `docs/03-architecture/`.

## Среда

TourCRM должна работать:

- в локальной сети клуба;
- через Internet;
- на desktop, tablet и mobile web.

## Правило разработки

Работа выполняется по GitHub Issues:

`Issue → implementation → automated tests → CI → review → close Issue → next Issue`

Issue не закрывается при невыполненном acceptance criteria или неуспешном автоматическом тестировании.

## Структура репозитория (application skeleton)

Monorepo с явными границами frontend/backend (Issue #4):

```text
apps/
├── web/   # frontend skeleton — React + TypeScript + Vite, см. apps/web/README.md
└── api/   # backend skeleton — FastAPI + Python, см. apps/api/README.md
tests/     # repository-level smoke checks (структура репозитория)
```

Запуск backend и frontend в development-режиме, а также минимальные smoke
checks описаны в `apps/api/README.md` и `apps/web/README.md`.

## Документация

- `docs/01-product/` — продуктовая концепция;
- `docs/02-requirements/` — требования и бизнес-правила;
- `docs/03-architecture/` — архитектура, технология и ADR;
- `docs/04-modules/` — документация модулей;
- `docs/05-api/` — API;
- `docs/06-ui/` — UI/UX и дизайн-система;
- `docs/07-security/` — безопасность и приватность;
- `docs/08-infrastructure/` — инфраструктура и DevOps;
- `docs/09-governance/` — governance, roadmap, аудит и трассировка требований;
- `docs/DOCUMENTATION-MAP.md` — карта и иерархия источников документации;
- `docs/03-architecture/adr/` — Architecture Decision Records.
