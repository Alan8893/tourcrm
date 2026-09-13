"""Event persistence/domain foundation (Issue #36).

Persistence: app.db.events (Event — per docs/04-modules/events-and-schedule.md
§5, ADR-0019). See app.events.lifecycle for the reusable, FastAPI-independent
lifecycle/data-integrity validation.

This package is deliberately independent of app.authorization: it does not
implement or perform authorization/permission/scope checks, and it never
treats a client-supplied value as an authorization decision.
"""
