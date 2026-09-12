# Development image for the TourCRM web frontend (Issue #8).
#
# This runs the Vite dev server with hot reload — not a production build/
# static file server (see docs/08-infrastructure/infrastructure-and-devops.md
# §6.3). A production image is a separate, later concern.

FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN chown -R node:node /app

USER node

EXPOSE 5173

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD node -e "fetch('http://localhost:5173').then(()=>process.exit(0)).catch(()=>process.exit(1))"

CMD ["npm", "run", "dev"]
