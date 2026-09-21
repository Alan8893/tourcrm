"""FileStorage infrastructure boundary (TH-0117.2 / Issue #158; ADR-0040 §3).

Stores and retrieves binary content addressed only by an opaque
`storage_key` string. This package knows nothing about `Person`,
`Document`, `Event`, HTTP, FastAPI, authorization, permissions, audit, or
PostgreSQL — those all sit on the caller's side of the `FileStorage`
boundary defined in `app.storage.file_storage`.
"""
