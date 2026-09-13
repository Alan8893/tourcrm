"""Canonical audit infrastructure (Issue #59, ADR-0024).

See app.audit.vocabulary for the closed action/actor/outcome vocabularies,
app.audit.security for the secret-prohibition boundary applied to
`details`, and app.audit.service for the reusable write boundary
(`record_audit_event`) every domain module must use instead of inserting
`AuditLog` rows directly.
"""
