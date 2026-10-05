"""Achievement Domain persistence (Issue #220).

Canonical source: docs/04-modules/achievements-and-norms.md (§2-§7,
§16-§28, decisions A1-A12).

The domain is separated exactly as §2 requires:

- `AchievementDefinition` — the stable identity of an achievement
  (`source`, `award_method`, `repeatability`, `active`/`inactive`
  lifecycle, A1/A3). Never physically deleted (no DELETE path exists).
- `AchievementRuleVersion` — an immutable-from-creation version of the
  executable Requirement / Rule of one Definition (A4/A5/A11/A13). The
  structured condition tree is stored as data (`condition`), never as
  code. At most one Rule Version per Definition is `active`
  (`uq_achievement_rule_versions_one_active`), so "exactly one current
  active Rule Version" (A11) is a database invariant.
- `AchievementNormativeSet` / `AchievementNormativeSetVersion` — the
  versioned external normative requirement sets with their source
  metadata (§4/§5). A version starts `inactive`; nothing is ever seeded,
  so the FSTR reference snapshot (§8) is never promoted to an active
  version by the system.
- `AchievementAward` — the historical record that one Person received
  one Definition (§2.3, A2). Provenance columns record the exact Rule
  Version and Normative Set Version used, the award method, the award
  time and, for manual awards, the verifying Administrator.

Database-level invariants (in addition to the service layer):

- `non_repeatable` (A3): `uq_achievement_awards_non_repeatable` — at
  most one Award per (Definition, Person) across the whole Award
  history, revoked Awards included. The Definition's `repeatability` is
  carried onto the Award through the composite FK
  `(definition_id, definition_repeatability) -> achievement_definitions
  (id, repeatability)` (ON UPDATE RESTRICT), so it cannot drift from the
  Definition and cannot change once a Definition has Awards.
- A9: `ck_achievement_awards_no_automatic_repeatable` — the automatic
  Engine never creates an Award for a `repeatable` Definition.
- A Rule Version referenced by an Award belongs to the Award's
  Definition: composite FK `(rule_version_id, definition_id)`.
- Automatic vs manual provenance shape and the revoked-state fields are
  CHECK constraints (`ck_achievement_awards_*`).
"""

import uuid
from datetime import date, datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.achievements.vocabulary import (
    AWARD_METHOD_AUTOMATIC,
    AWARD_METHOD_MANUAL,
    AWARD_STATUS_ACTIVE,
    AWARD_STATUS_REVOKED,
    CANONICAL_AWARD_METHODS,
    CANONICAL_AWARD_STATUSES,
    CANONICAL_DEFINITION_AWARD_METHODS,
    CANONICAL_EVALUATION_TRIGGERS,
    CANONICAL_LIFECYCLE_STATUSES,
    CANONICAL_REPEATABILITIES,
    CANONICAL_SOURCES,
    REPEATABILITY_NON_REPEATABLE,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
)
from app.db.base import Base


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)



# Constraint names the service layer recognizes.
NON_REPEATABLE_AWARD_INDEX = "uq_achievement_awards_non_repeatable"
ONE_ACTIVE_RULE_VERSION_INDEX = "uq_achievement_rule_versions_one_active"
DEFINITION_CODE_UNIQUE = "uq_achievement_definitions_code"
NORMATIVE_SET_CODE_UNIQUE = "uq_achievement_normative_sets_code"


class AchievementNormativeSet(Base):
    """A named external normative requirement set (§4), e.g. conceptually
    «ФСТР — знаки отличия детско-юношеского туризма». Its content lives
    in its versions."""

    __tablename__ = "achievement_normative_sets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("code", name=NORMATIVE_SET_CODE_UNIQUE),
        sa.CheckConstraint("length(btrim(code)) > 0", name="ck_achievement_normative_sets_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_achievement_normative_sets_name"),
    )


class AchievementNormativeSetVersion(Base):
    """One version of a normative requirement set with the §4 source
    metadata. Immutable once an Award references it (§5) — enforced by
    app.achievements.service; lifecycle (`active`/`inactive`) may still be
    switched because it does not change the version's content."""

    __tablename__ = "achievement_normative_set_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    normative_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    source_organization: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    document_title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    document_version: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    publication_date: Mapped[Optional[date]] = mapped_column(sa.Date, nullable=True)
    effective_from: Mapped[date] = mapped_column(sa.Date, nullable=False)
    effective_to: Mapped[Optional[date]] = mapped_column(sa.Date, nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default=STATUS_INACTIVE)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["normative_set_id"],
            ["achievement_normative_sets.id"],
            name="fk_achievement_normative_set_versions_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_achievement_normative_set_versions_created_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "normative_set_id",
            "version_number",
            name="uq_achievement_normative_set_versions_number",
        ),
        sa.CheckConstraint(
            "version_number > 0", name="ck_achievement_normative_set_versions_number"
        ),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_LIFECYCLE_STATUSES)})",
            name="ck_achievement_normative_set_versions_status",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_achievement_normative_set_versions_effective_range",
        ),
        sa.CheckConstraint(
            "length(btrim(source_organization)) > 0 AND length(btrim(document_title)) > 0 "
            "AND length(btrim(source_url)) > 0 AND length(btrim(document_version)) > 0",
            name="ck_achievement_normative_set_versions_metadata",
        ),
    )


class AchievementDefinition(Base):
    """The stable identity of an achievement (§2.1, A1, A3, A4)."""

    __tablename__ = "achievement_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    source: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    award_method: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    repeatability: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default=STATUS_INACTIVE)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.UniqueConstraint("code", name=DEFINITION_CODE_UNIQUE),
        # Target of the Award's composite FK carrying `repeatability`.
        sa.UniqueConstraint("id", "repeatability", name="uq_achievement_definitions_id_repeat"),
        sa.CheckConstraint("length(btrim(code)) > 0", name="ck_achievement_definitions_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_achievement_definitions_name"),
        sa.CheckConstraint(
            f"source IN ({_in(CANONICAL_SOURCES)})", name="ck_achievement_definitions_source"
        ),
        sa.CheckConstraint(
            f"award_method IN ({_in(CANONICAL_DEFINITION_AWARD_METHODS)})",
            name="ck_achievement_definitions_award_method",
        ),
        sa.CheckConstraint(
            f"repeatability IN ({_in(CANONICAL_REPEATABILITIES)})",
            name="ck_achievement_definitions_repeatability",
        ),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_LIFECYCLE_STATUSES)})",
            name="ck_achievement_definitions_status",
        ),
    )


class AchievementRuleVersion(Base):
    """One version of a Definition's executable Requirement / Rule (A4/A13).

    `condition` is the structured nested `AND`/`OR` condition tree (A5),
    validated by app.achievements.conditions against the approved metric
    catalog (A8) before it is stored. Content (`condition`,
    `normative_set_version_id`) is immutable from creation (A13): the
    service layer offers no operation that changes it; only `status`
    (lifecycle) changes."""

    __tablename__ = "achievement_rule_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    normative_set_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default=STATUS_INACTIVE)
    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["definition_id"],
            ["achievement_definitions.id"],
            name="fk_achievement_rule_versions_definition",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["normative_set_version_id"],
            ["achievement_normative_set_versions.id"],
            name="fk_achievement_rule_versions_normative_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_achievement_rule_versions_created_by",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "definition_id", "version_number", name="uq_achievement_rule_versions_number"
        ),
        # Target of the Award's composite FK `(rule_version_id, definition_id)`.
        sa.UniqueConstraint("id", "definition_id", name="uq_achievement_rule_versions_id_def"),
        sa.CheckConstraint("version_number > 0", name="ck_achievement_rule_versions_number"),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_LIFECYCLE_STATUSES)})",
            name="ck_achievement_rule_versions_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(condition) = 'object'", name="ck_achievement_rule_versions_condition"
        ),
        sa.Index(
            ONE_ACTIVE_RULE_VERSION_INDEX,
            "definition_id",
            unique=True,
            postgresql_where=sa.text(f"status = '{STATUS_ACTIVE}'"),
        ),
    )


class AchievementAward(Base):
    """A historical record that one Person received one Definition (§2.3,
    A2). Never deleted and never rewritten: the only state change is the
    single `active -> revoked` transition, which keeps every provenance
    field intact (app.achievements.service.revoke_award)."""

    __tablename__ = "achievement_awards"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    definition_repeatability: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    award_method: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    rule_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    normative_set_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    awarded_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    # Manual awards: the Administrator who verified and issued the Award.
    awarded_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    verification_note: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    # Automatic awards: which Engine path created the Award and the metric
    # values the Rule was evaluated against.
    evaluation_trigger: Mapped[Optional[str]] = mapped_column(sa.String(32), nullable=True)
    evaluated_metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default=AWARD_STATUS_ACTIVE
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    revoked_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    revocation_reason: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["definition_id", "definition_repeatability"],
            ["achievement_definitions.id", "achievement_definitions.repeatability"],
            name="fk_achievement_awards_definition",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id", "definition_id"],
            ["achievement_rule_versions.id", "achievement_rule_versions.definition_id"],
            name="fk_achievement_awards_rule_version",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["normative_set_version_id"],
            ["achievement_normative_set_versions.id"],
            name="fk_achievement_awards_normative_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"], ["persons.id"], name="fk_achievement_awards_person", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["awarded_by_user_id"],
            ["users.id"],
            name="fk_achievement_awards_awarded_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_achievement_awards_revoked_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"award_method IN ({_in(CANONICAL_AWARD_METHODS)})",
            name="ck_achievement_awards_award_method",
        ),
        sa.CheckConstraint(
            f"status IN ({_in(CANONICAL_AWARD_STATUSES)})", name="ck_achievement_awards_status"
        ),
        sa.CheckConstraint(
            "evaluation_trigger IS NULL OR evaluation_trigger IN "
            f"({_in(CANONICAL_EVALUATION_TRIGGERS)})",
            name="ck_achievement_awards_evaluation_trigger",
        ),
        # §3: automatic and manual Awards stay distinguishable — an
        # automatic Award always names its Rule Version and Engine path and
        # never an issuing user; a manual Award always names its issuer.
        sa.CheckConstraint(
            f"(award_method = '{AWARD_METHOD_AUTOMATIC}' AND rule_version_id IS NOT NULL "
            "AND evaluation_trigger IS NOT NULL AND awarded_by_user_id IS NULL "
            "AND verification_note IS NULL) "
            f"OR (award_method = '{AWARD_METHOD_MANUAL}' AND awarded_by_user_id IS NOT NULL "
            "AND evaluation_trigger IS NULL AND evaluated_metrics IS NULL)",
            name="ck_achievement_awards_method_provenance",
        ),
        # A9: no automatic Award for a repeatable Definition.
        sa.CheckConstraint(
            f"award_method <> '{AWARD_METHOD_AUTOMATIC}' "
            f"OR definition_repeatability = '{REPEATABILITY_NON_REPEATABLE}'",
            name="ck_achievement_awards_no_automatic_repeatable",
        ),
        # A2: a revocation always carries who, when and an explicit reason.
        sa.CheckConstraint(
            f"(status = '{AWARD_STATUS_ACTIVE}' AND revoked_at IS NULL "
            "AND revoked_by_user_id IS NULL AND revocation_reason IS NULL) "
            f"OR (status = '{AWARD_STATUS_REVOKED}' AND revoked_at IS NOT NULL "
            "AND revoked_by_user_id IS NOT NULL AND revocation_reason IS NOT NULL "
            "AND length(btrim(revocation_reason)) > 0)",
            name="ck_achievement_awards_revocation",
        ),
        sa.Index(
            NON_REPEATABLE_AWARD_INDEX,
            "definition_id",
            "person_id",
            unique=True,
            postgresql_where=sa.text(
                f"definition_repeatability = '{REPEATABILITY_NON_REPEATABLE}'"
            ),
        ),
        sa.Index("ix_achievement_awards_person_id", "person_id"),
        sa.Index("ix_achievement_awards_rule_version_id", "rule_version_id"),
        sa.Index("ix_achievement_awards_normative_set_version_id", "normative_set_version_id"),
    )


__all__ = [
    "AchievementNormativeSet",
    "AchievementNormativeSetVersion",
    "AchievementDefinition",
    "AchievementRuleVersion",
    "AchievementAward",
    "NON_REPEATABLE_AWARD_INDEX",
    "ONE_ACTIVE_RULE_VERSION_INDEX",
    "DEFINITION_CODE_UNIQUE",
    "NORMATIVE_SET_CODE_UNIQUE",
]
