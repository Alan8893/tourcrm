"""Group domain service (Issue #41 / ADR-0022).

See app.groups.service for the canonical, shared Club-ownership
validation mechanism for GroupMembership and GroupInstructorAssignment
writes. This package does not implement Group API endpoints,
authorization/permission checks, or any Event-to-Group relationship —
all explicit non-goals of the issues that produced it.
"""
