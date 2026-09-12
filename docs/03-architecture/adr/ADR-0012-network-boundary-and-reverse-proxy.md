# ADR-0012 — Network boundary and reverse proxy

## Status

Accepted.

## Context

TourCRM must work both on a trusted local network and through the Internet. The application stack contains frontend, API, database, background workers and storage. Internal infrastructure must not be directly exposed to clients.

## Decision

Use a dedicated reverse-proxy/network gateway as the only public application entry point.

Traffic model:

```text
Client
  |
  v
Reverse Proxy / TLS termination
  |
  +--> Frontend web application
  |
  +--> /api/* --> Backend API
```

Internal services remain on a private Docker/network segment:

```text
Backend --> PostgreSQL
Backend --> Redis
Worker  --> PostgreSQL / Redis
Backend --> Object Storage
```

PostgreSQL and Redis must not publish ports to the public network in production. Administrative interfaces must be protected separately and are not part of the public application surface.

For Internet deployments, HTTPS is mandatory. HTTP may only be used as a controlled local-development/LAN option when documented; production public traffic must not transmit credentials or protected data over plaintext HTTP.

The exact reverse-proxy implementation (for example, Nginx, Caddy or another maintained component) is an implementation decision and must be recorded before production deployment.

## Rationale

This creates a clean security boundary, centralizes TLS and routing, and keeps internal service topology private.

## Consequences

- health endpoints must distinguish internal diagnostics from public liveness checks;
- WebSocket/streaming support, if later required, must be explicitly configured at the proxy boundary;
- upload limits and request timeouts are part of the proxy/API contract;
- LAN and Internet DNS/base URLs must be configurable rather than hard-coded.
