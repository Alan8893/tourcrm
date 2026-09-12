"""Application-managed authentication (Issue #33).

Persistence: app.db.authentication (AuthenticatedSession,
EmailVerificationChallenge, PasswordResetChallenge — per
docs/03-architecture/authentication-persistence.md).

This package is deliberately independent of app.authorization: this Issue
establishes *who the caller is* (authentication); it does not change *what
they may do* (authorization/RBAC), which remains entirely Issue #29's
concern.
"""
