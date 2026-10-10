"""Safe Telegram rendering of notification templates (Issue #336,
ADR-0049 §2.5/§2.7).

Pure Python — no ORM/HTTP import. The Telegram adapter renders a
Notification's template from the `render_context` snapshot stored in the
business transaction; nothing is re-read from business tables at send time.

Template syntax (the only syntax a stored `body_template` may use):

- `{{name}}` — a variable of the template's declared `TemplateVariables`
  (lower-case letters, digits, underscore, starting with a letter);
- `**text**` — bold. Markers must be balanced across the whole template;
- everything else is literal text.

Output is Telegram `parse_mode=HTML`:

- literal text of the template is HTML-escaped, so a template can never
  inject markup other than the `<b>` produced from `**`;
- every value is HTML-escaped and its `{`/`}` are emitted as numeric
  entities, so neither a value nor the template can leave a literal `{{`/
  `}}` in the message; values never pass through bold processing;
- a required variable missing or blank, an undeclared or malformed
  placeholder, or unbalanced `**` raise TemplateRenderError
  (`template_render_failed`) — a permanent failure: the same template and
  context can never succeed, so it is not retried;
- an optional variable missing or blank takes its declared fallback; an
  empty fallback omits it together with one adjacent space;
- a link variable is built from the deployment's validated public base URL
  and a fixed path containing at most one UUID taken from the context —
  never from a free-form value. Without a base URL the link is omitted;
- the visible text (after entity parsing, as Telegram counts it) must be
  1..4096 characters, else `message_too_long` / `template_render_failed`.
"""

import html
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Optional

TEMPLATE_RENDER_FAILED = "template_render_failed"
MESSAGE_TOO_LONG = "message_too_long"

# Bot API sendMessage: 1-4096 characters of text after entity parsing.
TELEGRAM_MESSAGE_MAX_LENGTH = 4096
TELEGRAM_PARSE_MODE_HTML = "HTML"

_NAME = r"[a-z][a-z0-9_]{0,63}"
_PLACEHOLDER = re.compile(r"\{\{\s*(" + _NAME + r")\s*\}\}")
_NAME_PATTERN = re.compile(_NAME)
# A placeholder whose value was omitted; never present in an escaped value.
_OMITTED = "\x00"
_TAGS = re.compile(r"</?b>")


class TemplateRenderError(ValueError):
    """A template cannot be rendered. `code` and `safe_message` are safe to
    persist: they name at most a variable, never a value."""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class LinkSpec:
    """A link to an existing frontend page: `path` is a fixed path that may
    contain one `{id}`, filled from the context variable `id_variable`,
    which must hold a UUID."""

    path: str
    id_variable: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.path.startswith("/") or "//" in self.path:
            raise ValueError("a link path must be an absolute single-slash path")
        if ("{id}" in self.path) != (self.id_variable is not None):
            raise ValueError("{id} and id_variable must be given together")


@dataclass(frozen=True)
class TemplateVariables:
    """What a template may reference. `optional` maps a variable to its
    fallback (an empty fallback omits the variable). `links` are computed,
    never taken from the context directly."""

    required: frozenset[str] = frozenset()
    optional: Mapping[str, str] = field(default_factory=dict)
    links: Mapping[str, LinkSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        names = [*self.required, *self.optional, *self.links]
        if len(set(names)) != len(names):
            raise ValueError("a variable is declared more than once")
        if not all(_NAME_PATTERN.fullmatch(name) for name in names):
            raise ValueError("variable names must match " + _NAME)

    @property
    def context_keys(self) -> frozenset[str]:
        """Every context name the template may read."""
        ids = {link.id_variable for link in self.links.values() if link.id_variable}
        return frozenset({*self.required, *self.optional, *ids})

    @property
    def required_context_keys(self) -> frozenset[str]:
        """Context names that must be present and non-blank."""
        ids = {link.id_variable for link in self.links.values() if link.id_variable}
        return frozenset({*self.required, *ids})


def template_placeholders(body_template: str) -> list[str]:
    """The placeholder names of `body_template`, in order; raises
    TemplateRenderError for malformed placeholder syntax."""
    names: list[str] = []
    position = 0
    for match in _PLACEHOLDER.finditer(body_template):
        _check_literal(body_template[position : match.start()])
        names.append(match.group(1))
        position = match.end()
    _check_literal(body_template[position:])
    return names


def _check_literal(text: str) -> None:
    if "{{" in text or "}}" in text:
        raise TemplateRenderError(TEMPLATE_RENDER_FAILED, "malformed placeholder")


def _escape_value(value: str) -> str:
    cleaned = value.replace(_OMITTED, "")
    return html.escape(cleaned, quote=False).replace("{", "&#123;").replace("}", "&#125;")


def _link(spec: LinkSpec, context: Mapping[str, str], public_base_url: Optional[str]) -> str:
    if public_base_url is None:
        return ""
    path = spec.path
    if spec.id_variable is not None:
        try:
            resource_id = uuid.UUID(context.get(spec.id_variable, ""))
        except ValueError:
            raise TemplateRenderError(
                TEMPLATE_RENDER_FAILED, f"variable {spec.id_variable} is not a valid id"
            ) from None
        path = path.replace("{id}", str(resource_id))
    return public_base_url + path


def _value(
    name: str,
    variables: TemplateVariables,
    context: Mapping[str, str],
    public_base_url: Optional[str],
) -> Optional[str]:
    """The variable's raw value, or None when it is omitted."""
    if name in variables.links:
        url = _link(variables.links[name], context, public_base_url)
        return url or None
    raw = context.get(name)
    present = isinstance(raw, str) and raw.strip() != ""
    if name in variables.required:
        if not present:
            raise TemplateRenderError(TEMPLATE_RENDER_FAILED, f"variable {name} is missing")
        assert isinstance(raw, str)
        return raw.strip()
    if name in variables.optional:
        if present:
            assert isinstance(raw, str)
            return raw.strip()
        return variables.optional[name] or None
    raise TemplateRenderError(TEMPLATE_RENDER_FAILED, f"variable {name} is not declared")


def render_telegram_html(
    body_template: str,
    *,
    variables: TemplateVariables,
    context: Mapping[str, str],
    public_base_url: Optional[str],
) -> str:
    """Render `body_template` to Telegram HTML; see the module docstring."""
    for name in variables.required_context_keys:
        raw = context.get(name)
        if not isinstance(raw, str) or not raw.strip():
            raise TemplateRenderError(TEMPLATE_RENDER_FAILED, f"variable {name} is missing")

    pieces: list[str] = []
    bold = False

    def literal(text: str) -> None:
        nonlocal bold
        _check_literal(text)
        for index, part in enumerate(text.split("**")):
            if index:
                pieces.append("</b>" if bold else "<b>")
                bold = not bold
            pieces.append(html.escape(part, quote=False))

    position = 0
    for match in _PLACEHOLDER.finditer(body_template):
        literal(body_template[position : match.start()])
        value = _value(match.group(1), variables, context, public_base_url)
        pieces.append(_OMITTED if value is None else _escape_value(value))
        position = match.end()
    literal(body_template[position:])
    if bold:
        raise TemplateRenderError(TEMPLATE_RENDER_FAILED, "unbalanced bold markers")

    text = re.sub(" ?" + _OMITTED, "", "".join(pieces))
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if "{{" in text or "}}" in text:
        raise TemplateRenderError(TEMPLATE_RENDER_FAILED, "unrendered placeholder")
    visible = html.unescape(_TAGS.sub("", text))
    if not visible.strip():
        raise TemplateRenderError(TEMPLATE_RENDER_FAILED, "message is empty")
    if len(visible) > TELEGRAM_MESSAGE_MAX_LENGTH:
        raise TemplateRenderError(MESSAGE_TOO_LONG, f"length={len(visible)}")
    return text


__all__ = [
    "TEMPLATE_RENDER_FAILED",
    "MESSAGE_TOO_LONG",
    "TELEGRAM_MESSAGE_MAX_LENGTH",
    "TELEGRAM_PARSE_MODE_HTML",
    "TemplateRenderError",
    "LinkSpec",
    "TemplateVariables",
    "template_placeholders",
    "render_telegram_html",
]
