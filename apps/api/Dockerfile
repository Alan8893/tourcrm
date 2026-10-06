# Development image for the TourCRM API (Issue #8).
#
# This is NOT a production image: it installs dev/test dependencies for
# convenience (e.g. `docker compose exec backend pytest`) and runs uvicorn
# with --reload. A hardened production image is a separate, later concern
# (see docs/08-infrastructure/infrastructure-and-devops.md §6.3).

FROM python:3.12-slim

# Bytecode isn't written back to the bind-mounted source tree at runtime,
# so the non-root user below never needs write access to it for that.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# UID/GID are build args (default 1000, the common single-user-Linux
# convention) rather than hardcoded, so this account's numeric id can be made
# to match whichever host account owns the bind-mounted ./apps/api directory
# (see docker-compose.yml's `build.args`/`user:`, and HOST_UID/HOST_GID in
# the root README) — needed because a bind mount always shows host-side
# ownership, so the image's baked-in id must line up with it for the
# non-root process to have write access where it genuinely needs it (e.g.
# `docker compose exec backend pytest` writing __pycache__/.pytest_cache).
ARG UID=1000
ARG GID=1000

RUN groupadd --gid "${GID}" app \
    && useradd --uid "${UID}" --gid app --shell /bin/bash --create-home app

# Scheduler for periodic system jobs (Issue #289, ADR-0044): supercronic,
# pinned to one release and verified against SHA-256 checksums fixed here,
# so every build installs byte-identical binaries (the build fails on any
# mismatch). Installed at build time, never downloaded at container start,
# and outside /app so the development bind mount cannot hide it. The same
# image runs the API (default CMD) and, with
# `supercronic -json /app/scheduler/crontab` as its command, the scheduler.
# The checksums were computed from the official release assets of
# https://github.com/aptible/supercronic/releases/tag/v0.2.49; update the
# version and both checksums together.
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256_AMD64=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ARG SUPERCRONIC_SHA256_ARM64=02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "${arch}" in \
        amd64) sha256="${SUPERCRONIC_SHA256_AMD64}" ;; \
        arm64) sha256="${SUPERCRONIC_SHA256_ARM64}" ;; \
        *) echo "supercronic: unsupported architecture ${arch}" >&2; exit 1 ;; \
    esac; \
    python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" \
        "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${arch}" \
        /usr/local/bin/supercronic; \
    echo "${sha256}  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/supercronic

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
RUN chown -R app:app /app

USER app

EXPOSE 8000

# Liveness only (Issue #10) — deliberately not /health/ready: a Docker
# container healthcheck answers "is this process alive", not "is its
# PostgreSQL dependency also up", so it must not fail merely because the
# database had a blip.
# The scheduler serves no HTTP, so its service disables this healthcheck
# (see the `scheduler` service in docker-compose.yml).
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=2)" || exit 1

# alembic upgrade head is additive/idempotent (Issue #5) — never destructive.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
