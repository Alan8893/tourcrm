# Architecture Overview

## Architectural principles

1. Prefer a modular monolith for the core CRM.
2. Keep business logic independent from HTTP/UI concerns.
3. Treat PostgreSQL as the source of truth for transactional data.
4. Make integrations explicit through stable contracts.
5. Design for responsive web on desktop, tablet and mobile.
6. Keep deployment portable between the club's own server and a VPS.
7. Preserve auditability for sensitive and important business operations.
8. Do not introduce infrastructure solely for theoretical scale; add components when a concrete requirement justifies them.

## High-level topology

```text
                           Internet / LAN
                                  |
                           Reverse Proxy
                                  |
                 +----------------+----------------+
                 |                                 |
             Web Client                         API
                 |                                 |
              React                        FastAPI application
                                                   |
                +------------------+---------------+----------------+
                |                  |                                |
            PostgreSQL           Redis                       File Storage
                |                  |                                |
           business data     jobs/cache/notifications       documents/media

                              External systems
                                      |
                  +-------------------+-------------------+
                  |                   |                   |
                Email             Telegram              MAX
                                      |
                                  TourSlet
                          (integration defined later)
```

## Core domains

The application is organized into business modules rather than technical CRUD layers.

Initial modules:

- Identity and access;
- Club and membership;
- Parents/legal representatives;
- Groups;
- Participants;
- Events and calendar;
- Attendance;
- Trips and routes;
- Tourist profile and experience;
- Achievements and ratings;
- Knowledge base;
- Documents;
- Equipment;
- Finance;
- Notifications;
- Reporting and analytics;
- Audit;
- External integrations.

## Event model

Lessons, trainings, trips, competitions, tour events and other scheduled activities share a common event abstraction. Specialized data is attached to an event type instead of creating unrelated scheduling systems.

```text
Event
  |
  +-- Event Type
  +-- Schedule
  +-- Leaders / instructors
  +-- Participants
  +-- Attendance
  +-- Documents
  +-- Notifications
  +-- Results / achievements
```

## Identity model

The following entities remain conceptually separate:

```text
User Account
   |
   +-- authentication credentials / identity

Person
   |
   +-- FIO / contacts / personal data

Club Membership
   |
   +-- membership status / roles / groups / dates
```

This separation supports parents with multiple children, children with multiple legal representatives, multiple roles per account and future changes in club membership without corrupting identity data.

## Data access

The API is the primary application boundary. The frontend does not connect to PostgreSQL directly.

Authorization is checked on the backend for every protected business operation. Frontend visibility of controls is a usability feature, not a security boundary.

## Deployment modes

### LAN

The service may be exposed only on the club's internal network. HTTPS is preferred where practical, but local deployment may use a trusted internal reverse proxy configuration.

### Internet

The public deployment uses:

- DNS;
- reverse proxy;
- HTTPS;
- hardened application configuration;
- backups;
- monitoring/logging.

## Evolution path

The architecture intentionally allows extraction of selected components later. The likely candidates are:

- notifications;
- TourSlet integration;
- file/object storage;
- high-volume reporting.

A component is extracted into a separate service only when operational or domain requirements justify it.
