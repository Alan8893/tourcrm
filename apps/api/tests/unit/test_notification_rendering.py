"""Unit tests for the safe Telegram template renderer (Issue #336,
ADR-0049 §2.5/§2.7): escaping, bold markup, required/optional variables,
links from the validated public base URL, raw-placeholder prevention and
the length limit after rendering. Pure — no database."""

import uuid

import pytest

from app.core.config import ConfigurationError, validate_public_base_url
from app.notifications.catalog import CATALOG, template_variables
from app.notifications.rendering import (
    MESSAGE_TOO_LONG,
    TEMPLATE_RENDER_FAILED,
    LinkSpec,
    TemplateRenderError,
    TemplateVariables,
    render_telegram_html,
    template_placeholders,
)

BASE = "https://crm.example.org"
EVENT_ID = "6f1c2b8e-9a4d-4c3e-8b1a-2d3e4f5a6b7c"
VARS = TemplateVariables(
    required=frozenset({"title"}),
    optional={"reason": "не указана", "excerpt": ""},
    links={"url": LinkSpec(path="/events?event={id}", id_variable="event_id")},
)


def _render(template: str, context: dict[str, str], base: str | None = BASE) -> str:
    return render_telegram_html(template, variables=VARS, context=context, public_base_url=base)


def _error(template: str, context: dict[str, str]) -> TemplateRenderError:
    with pytest.raises(TemplateRenderError) as info:
        _render(template, context)
    return info.value


def test_values_are_html_escaped_and_never_become_markup() -> None:
    title = '<script>x</script> & "q" **bold**'
    text = _render("Событие **{{title}}**", {"title": title, "event_id": EVENT_ID})
    assert text == (
        "Событие <b>&lt;script&gt;x&lt;/script&gt; &amp; \"q\" **bold**</b>"
    )


def test_literal_text_of_the_template_is_escaped_too() -> None:
    assert _render("a <i>b</i> & c", {"title": "t", "event_id": EVENT_ID}) == (
        "a &lt;i&gt;b&lt;/i&gt; &amp; c"
    )


def test_braces_in_values_are_entities_so_no_raw_placeholder_can_be_sent() -> None:
    text = _render("{{title}}", {"title": "{{title}} }}", "event_id": EVENT_ID})
    assert "{{" not in text and "}}" not in text
    assert text == "&#123;&#123;title&#125;&#125; &#125;&#125;"


@pytest.mark.parametrize("title", [None, "", "   "])
def test_missing_or_blank_required_variable_is_a_permanent_render_error(
    title: str | None,
) -> None:
    context = {"event_id": EVENT_ID}
    if title is not None:
        context["title"] = title
    error = _error("{{title}}", context)
    assert error.code == TEMPLATE_RENDER_FAILED
    assert error.safe_message == "variable title is missing"


def test_required_link_id_is_checked_even_if_the_link_is_unused() -> None:
    assert _error("{{title}}", {"title": "t"}).code == TEMPLATE_RENDER_FAILED


@pytest.mark.parametrize(
    "template",
    [
        "{{unknown}}",
        "{{Title}}",
        "{{ title",
        "title }}",
        "{{}}",
        "{{title}} {{",
        "**unbalanced",
    ],
)
def test_undeclared_or_malformed_template_fails(template: str) -> None:
    assert _error(template, {"title": "t", "event_id": EVENT_ID}).code == TEMPLATE_RENDER_FAILED


def test_optional_value_uses_its_fallback() -> None:
    context = {"title": "t", "event_id": EVENT_ID}
    assert _render("Причина: {{reason}}.", context) == "Причина: не указана."
    assert _render("Причина: {{reason}}.", {**context, "reason": "дождь"}) == "Причина: дождь."


def test_omitted_optional_value_removes_one_adjacent_space() -> None:
    context = {"title": "T", "event_id": EVENT_ID}
    assert _render("**{{title}}**. {{excerpt}} {{url}}", context, base=None) == "<b>T</b>."
    assert _render("**{{title}}**. {{excerpt}} {{url}}", {**context, "excerpt": "E"}, None) == (
        "<b>T</b>. E"
    )


def test_link_is_built_from_the_base_url_and_the_uuid_only() -> None:
    text = _render("{{url}}", {"title": "t", "event_id": EVENT_ID.upper()})
    assert text == f"{BASE}/events?event={EVENT_ID}"


def test_link_is_omitted_without_a_base_url() -> None:
    assert _render("{{title}} {{url}}", {"title": "t", "event_id": EVENT_ID}, base=None) == "t"


@pytest.mark.parametrize("bad_id", ["javascript:alert(1)", "../admin", "1"])
def test_link_never_uses_a_non_uuid_value(bad_id: str) -> None:
    error = _error("{{url}}", {"title": "t", "event_id": bad_id})
    assert error.code == TEMPLATE_RENDER_FAILED
    assert bad_id not in error.safe_message


def test_length_is_checked_on_visible_text_after_rendering() -> None:
    four_k = "я" * 4096
    assert _render("{{title}}", {"title": four_k, "event_id": EVENT_ID}) == four_k
    # Entities and tags do not count: "&" renders as one character.
    amp = "&" * 4096
    assert _render("{{title}}", {"title": amp, "event_id": EVENT_ID}) == "&amp;" * 4096
    too_long = _error("**{{title}}**!", {"title": four_k, "event_id": EVENT_ID})
    assert too_long.code == MESSAGE_TOO_LONG


def test_empty_message_is_rejected() -> None:
    assert _error("{{excerpt}}", {"title": "t", "event_id": EVENT_ID}).code == (
        TEMPLATE_RENDER_FAILED
    )


def test_template_placeholders_lists_names_and_rejects_malformed_syntax() -> None:
    assert template_placeholders("a {{x}} b {{ y_1 }}") == ["x", "y_1"]
    with pytest.raises(TemplateRenderError):
        template_placeholders("a {{x")


def test_declarations_are_validated() -> None:
    with pytest.raises(ValueError):
        TemplateVariables(required=frozenset({"a"}), optional={"a": ""})
    with pytest.raises(ValueError):
        TemplateVariables(required=frozenset({"Bad"}))
    with pytest.raises(ValueError):
        LinkSpec(path="https://evil.example/{id}", id_variable="x")
    with pytest.raises(ValueError):
        LinkSpec(path="/events/{id}")


# --- Catalog §4 templates ------------------------------------------------------

# The catalog §4 message texts (docs/04-modules/notification-event-catalog.md),
# membership without a link (PO decision 9).
CATALOG_TEMPLATES = {
    "event.created": "Новое событие: **{{event_title}}**. Дата: {{event_datetime}}. {{event_url}}",
    "event.updated": "Обновление события **{{event_title}}**: {{change_summary}}. {{event_url}}",
    "event.cancelled": (
        "Событие **{{event_title}}** отменено. Дата: {{event_datetime}}. "
        "Причина: {{cancellation_reason}}. {{event_url}}"
    ),
    "event.rescheduled": (
        "Событие **{{event_title}}** перенесено: {{old_event_datetime}} → "
        "{{new_event_datetime}}. {{event_url}}"
    ),
    "registration.created": (
        "Вы зарегистрированы на **{{event_title}}**. Дата: {{event_datetime}}. {{event_url}}"
    ),
    "registration.cancelled": "Ваша регистрация на **{{event_title}}** отменена. {{event_url}}",
    "attendance.changed": (
        "Обновлена отметка посещаемости для **{{event_title}}**: {{attendance_status}}. "
        "{{event_url}}"
    ),
    "news.published": "Опубликована новость: **{{news_title}}**. {{news_excerpt}} {{news_url}}",
    "achievement.awarded": (
        "Вам присвоено достижение **{{achievement_title}}**. {{achievement_url}}"
    ),
    "membership.approved": "Ваша заявка на вступление в туристский клуб одобрена.",
}


def _sample_context(event_type: str) -> dict[str, str]:
    variables = CATALOG[event_type].variables
    context = {name: f"<{name}>" for name in variables.required}
    for link in variables.links.values():
        if link.id_variable is not None:
            context[link.id_variable] = str(uuid.uuid4())
    return context


@pytest.mark.parametrize("event_type", sorted(CATALOG_TEMPLATES))
def test_every_catalog_template_renders_with_its_declared_variables(event_type: str) -> None:
    template = CATALOG_TEMPLATES[event_type]
    code = CATALOG[event_type].template_code
    variables = template_variables(code)
    assert set(template_placeholders(template)) <= set(variables.required) | set(
        variables.optional
    ) | set(variables.links)
    for base in (BASE, None):
        text = render_telegram_html(
            template, variables=variables, context=_sample_context(event_type),
            public_base_url=base,
        )
        assert "{{" not in text and "}}" not in text
        assert "<" not in text.replace("<b>", "").replace("</b>", "")
        if base is None:
            assert "http" not in text
    if event_type == "event.cancelled":
        text = render_telegram_html(
            template, variables=variables, context=_sample_context(event_type),
            public_base_url=None,
        )
        assert "Причина: не указана." in text


def test_template_outside_the_catalog_declares_no_variables() -> None:
    variables = template_variables("plain.text")
    assert render_telegram_html(
        "Сбор в 8:00", variables=variables, context={}, public_base_url=BASE
    ) == "Сбор в 8:00"
    with pytest.raises(TemplateRenderError):
        render_telegram_html("{{x}}", variables=variables, context={"x": "1"}, public_base_url=None)


# --- APP_PUBLIC_BASE_URL -----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("https://crm.example.org", "https://crm.example.org"),
        ("https://crm.example.org/", "https://crm.example.org"),
        ("https://CRM.example.org:8443/app/", "https://crm.example.org:8443/app"),
        ("http://localhost:5173", "http://localhost:5173"),
    ],
)
def test_public_base_url_is_normalized(value: str, normalized: str) -> None:
    assert validate_public_base_url(value) == normalized


@pytest.mark.parametrize(
    "value",
    [
        "",
        "crm.example.org",
        "ftp://crm.example.org",
        "javascript:alert(1)",
        "https://user:pass@crm.example.org",
        "https://crm.example.org/?a=1",
        "https://crm.example.org/#x",
        "https://crm.example.org /x",
        "https://crm.example.org:99999",
        "https:///path",
    ],
)
def test_invalid_public_base_url_is_rejected(value: str) -> None:
    with pytest.raises(ConfigurationError):
        validate_public_base_url(value)


def test_public_base_url_env_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_public_base_url

    monkeypatch.delenv("APP_PUBLIC_BASE_URL", raising=False)
    assert get_public_base_url() is None
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://crm.example.org/")
    assert get_public_base_url() == "https://crm.example.org"
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "not a url")
    with pytest.raises(ConfigurationError):
        get_public_base_url()
