"""Unit tests for the News lifecycle/content/audience rules (TH-0120 /
Issue #227; docs/04-ux/news.md §2/§5/§6). Pure domain — no DB/HTTP."""

import pytest

from app.news.lifecycle import (
    InvalidNewsAudienceError,
    InvalidNewsContentError,
    InvalidNewsStatusTransitionError,
    NewsArchivedError,
    ensure_editable,
    normalize_body,
    normalize_location,
    normalize_title,
    validate_audience,
    validate_status_transition,
)
from app.news.vocabulary import CANONICAL_NEWS_STATUSES, CREATABLE_NEWS_STATUSES


def test_status_vocabulary_is_closed() -> None:
    assert CANONICAL_NEWS_STATUSES == {"draft", "published", "archived"}
    assert CREATABLE_NEWS_STATUSES == {"draft", "published"}


@pytest.mark.parametrize(
    ("current", "target"),
    [("draft", "published"), ("draft", "archived"), ("published", "archived")],
)
def test_allowed_transitions(current: str, target: str) -> None:
    validate_status_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("published", "published"),
        ("published", "draft"),
        ("archived", "published"),
        ("archived", "draft"),
        ("archived", "archived"),
        ("draft", "draft"),
    ],
)
def test_rejected_transitions(current: str, target: str) -> None:
    with pytest.raises(InvalidNewsStatusTransitionError):
        validate_status_transition(current, target)


def test_archived_news_is_not_editable() -> None:
    ensure_editable("draft")
    ensure_editable("published")
    with pytest.raises(NewsArchivedError):
        ensure_editable("archived")


def test_title_and_body_are_required() -> None:
    assert normalize_title("  Поход  ") == "Поход"
    with pytest.raises(InvalidNewsContentError):
        normalize_title("   ")
    with pytest.raises(InvalidNewsContentError):
        normalize_title("x" * 256)
    assert normalize_body("Текст") == "Текст"
    with pytest.raises(InvalidNewsContentError):
        normalize_body(" \n ")


def test_location_is_optional_and_trimmed() -> None:
    assert normalize_location(None) is None
    assert normalize_location("  ") is None
    assert normalize_location(" Парк ") == "Парк"
    with pytest.raises(InvalidNewsContentError):
        normalize_location("x" * 256)


def test_audience_rules() -> None:
    validate_audience("club", 0)
    validate_audience("groups", 2)
    with pytest.raises(InvalidNewsAudienceError):
        validate_audience("groups", 0)
    with pytest.raises(InvalidNewsAudienceError):
        validate_audience("club", 1)
    with pytest.raises(InvalidNewsAudienceError):
        validate_audience("role", 0)
