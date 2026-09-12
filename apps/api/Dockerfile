# Development image for the TourCRM API (Issue #8).
#
# This is NOT a production image: it installs dev/test dependencies for
# convenience (e.g. `docker compose exec backend pytest`) and runs uvicorn
# with --reload. A hardened production image is a separate, later concern
# (see docs/08-infrastructure/infrastructure-and-devops.md §6.3).

FROM python:3.12-slim

# Bytecode isn't written back to the bind-mounted source tree at runtime,
# so the non-root user below never needs write access to it.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
RUN chown -R app:app /app

USER app

EXPOSE 8000

# No dedicated health endpoint exists yet (deferred to a later Issue); this
# reuses the already-existing FastAPI-generated OpenAPI document
# (docs/05-api foundation, Issue #6) purely as an availability probe — it
# is not a readiness/liveness endpoint and exposes no domain data.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/openapi.json', timeout=2)" || exit 1

# alembic upgrade head is additive/idempotent (Issue #5) — never destructive.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
