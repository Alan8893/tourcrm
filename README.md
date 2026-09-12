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

Подробнее: `docs/02-architecture/`.

## Среда

TourCRM должна работать:

- в локальной сети клуба;
- через Internet;
- на desktop, tablet и mobile web.

## Правило разработки

Работа выполняется по GitHub Issues:

`Issue → implementation → automated tests → CI → review → close Issue → next Issue`

Issue не закрывается при невыполненном acceptance criteria или неуспешном автоматическом тестировании.

## Документация

- `docs/01-product/` — продуктовая концепция;
- `docs/02-architecture/` — архитектура и технологии;
- `docs/03-requirements/` — требования;
- `docs/04-modules/` — документация модулей;
- `docs/05-api/` — API;
- `docs/06-database/` — БД и ER-модель;
- `docs/07-development/` — правила разработки;
- `docs/08-roadmap/` — roadmap и backlog;
- `docs/02-architecture/adr/` — Architecture Decision Records.
