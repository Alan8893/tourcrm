# ADR-0002: Базовый технологический стек

## Статус
Accepted

## Решение

### Frontend
- React;
- TypeScript;
- адаптивный web UI;
- API client на основе OpenAPI-контракта.

### Backend
- Python;
- FastAPI;
- Pydantic для contract/schema validation;
- SQLAlchemy для data access;
- Alembic для миграций.

### Data
- PostgreSQL как основная реляционная БД;
- object/file storage через отдельную abstraction;
- Redis для задач, которые действительно требуют очереди, кэша или coordination.

### Operations
- Linux LTS;
- Docker + Docker Compose;
- GitHub Actions;
- reverse proxy определяется отдельным инфраструктурным ADR.

## Причины

Стек подходит для типизированного API, сложной реляционной модели, адаптивного frontend и постепенного расширения модулей при небольшом стартовом сервере.

## Правило версий

Точные версии runtime и библиотек выбираются на момент реализации и фиксируются lock-файлами. Обновление major/minor версий с архитектурными последствиями требует отдельного решения.
