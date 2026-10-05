"""Achievement Domain vocabulary (Issue #220).

Canonical source: docs/04-modules/achievements-and-norms.md §2, §16-§28
(decisions A1-A12). Every value here is a closed canonical set; the
persistence layer mirrors each one as a CHECK constraint
(app.db.achievements).
"""

# §2.1: where an Achievement Definition comes from.
SOURCE_CLUB = "club"
SOURCE_FSTR = "fstr"
CANONICAL_SOURCES: tuple[str, ...] = (SOURCE_CLUB, SOURCE_FSTR)

# §2.1: how a Definition may be awarded.
AWARD_METHOD_AUTOMATIC = "automatic"
AWARD_METHOD_MANUAL = "manual"
AWARD_METHOD_BOTH = "both"
CANONICAL_DEFINITION_AWARD_METHODS: tuple[str, ...] = (
    AWARD_METHOD_AUTOMATIC,
    AWARD_METHOD_MANUAL,
    AWARD_METHOD_BOTH,
)
# A Definition's `award_method` values that allow each way of awarding.
AUTOMATIC_AWARD_METHODS: frozenset[str] = frozenset({AWARD_METHOD_AUTOMATIC, AWARD_METHOD_BOTH})
MANUAL_AWARD_METHODS: frozenset[str] = frozenset({AWARD_METHOD_MANUAL, AWARD_METHOD_BOTH})
# §2.3: an Award itself was issued either automatically or manually —
# `both` is a Definition capability, never an Award's method.
CANONICAL_AWARD_METHODS: tuple[str, ...] = (AWARD_METHOD_AUTOMATIC, AWARD_METHOD_MANUAL)

# §18 (A3).
REPEATABILITY_NON_REPEATABLE = "non_repeatable"
REPEATABILITY_REPEATABLE = "repeatable"
CANONICAL_REPEATABILITIES: tuple[str, ...] = (
    REPEATABILITY_NON_REPEATABLE,
    REPEATABILITY_REPEATABLE,
)

# §16 (A1) Definition lifecycle; also the active/inactive state of a Rule
# Version (§26, A11: "Rule Versions active at the time of evaluation") and
# of a Normative Requirement Set Version (§4/§6: lifecycle/status,
# activating/deactivating a version).
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
CANONICAL_LIFECYCLE_STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_INACTIVE)

# §17 (A2) Award lifecycle.
AWARD_STATUS_ACTIVE = "active"
AWARD_STATUS_REVOKED = "revoked"
CANONICAL_AWARD_STATUSES: tuple[str, ...] = (AWARD_STATUS_ACTIVE, AWARD_STATUS_REVOKED)

# §22 (A7): which Engine path created an automatic Award (provenance).
TRIGGER_EVENT = "event"
TRIGGER_RECONCILIATION = "reconciliation"
CANONICAL_EVALUATION_TRIGGERS: tuple[str, ...] = (TRIGGER_EVENT, TRIGGER_RECONCILIATION)

# §25 (A10): the only two achievement permissions besides the read one.
PERMISSION_MANAGE = "achievement.manage"
PERMISSION_AWARD = "achievement.award"
PERMISSION_READ = "achievement.read"

# §27 (A12): the canonical system role (ADR-0039 §3, "member — club
# participant") whose holders are Achievement recipients.
MEMBER_ROLE_CODE = "member"

__all__ = [
    "SOURCE_CLUB",
    "SOURCE_FSTR",
    "CANONICAL_SOURCES",
    "AWARD_METHOD_AUTOMATIC",
    "AWARD_METHOD_MANUAL",
    "AWARD_METHOD_BOTH",
    "CANONICAL_DEFINITION_AWARD_METHODS",
    "AUTOMATIC_AWARD_METHODS",
    "MANUAL_AWARD_METHODS",
    "CANONICAL_AWARD_METHODS",
    "REPEATABILITY_NON_REPEATABLE",
    "REPEATABILITY_REPEATABLE",
    "CANONICAL_REPEATABILITIES",
    "STATUS_ACTIVE",
    "STATUS_INACTIVE",
    "CANONICAL_LIFECYCLE_STATUSES",
    "AWARD_STATUS_ACTIVE",
    "AWARD_STATUS_REVOKED",
    "CANONICAL_AWARD_STATUSES",
    "TRIGGER_EVENT",
    "TRIGGER_RECONCILIATION",
    "CANONICAL_EVALUATION_TRIGGERS",
    "PERMISSION_MANAGE",
    "PERMISSION_AWARD",
    "PERMISSION_READ",
    "MEMBER_ROLE_CODE",
]
