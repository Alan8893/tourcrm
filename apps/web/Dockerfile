# Development image for the TourCRM web frontend (Issue #8).
#
# This runs the Vite dev server with hot reload — not a production build/
# static file server (see docs/08-infrastructure/infrastructure-and-devops.md
# §6.3). A production image is a separate, later concern.

FROM node:20-slim

WORKDIR /app

# UID/GID are build args (default 1000) rather than hardcoded — see
# docker-compose.yml's `build.args`/`user:` and HOST_UID/HOST_GID in the
# root README. This is what the reported EACCES came from: node:20-slim's
# built-in "node" account is fixed at uid/gid 1000, but ./apps/web is a bind
# mount, so it shows the HOST's actual ownership inside the container
# regardless of the image — if that owner isn't 1000, "node" cannot create
# Vite's `vite.config.ts.timestamp-*.mjs` in /app. Re-numbering the existing
# account (rather than adding a second one) avoids a uid/gid collision when
# the default (1000) is used, which is the common case.
ARG UID=1000
ARG GID=1000

RUN userdel -r node 2>/dev/null || true \
    && groupdel node 2>/dev/null || true \
    && groupadd --gid "${GID}" node \
    && useradd --uid "${UID}" --gid node --shell /bin/bash --create-home node

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN chown -R node:node /app

USER node

EXPOSE 5173

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD node -e "fetch('http://localhost:5173').then(()=>process.exit(0)).catch(()=>process.exit(1))"

CMD ["npm", "run", "dev"]
