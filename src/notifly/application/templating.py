"""Template variable validation and content rendering.

Validation applies template ``VariableDef`` declarations to the variables a
caller supplies, applying defaults and surfacing type/required/unknown errors.
Rendering uses a sandboxed Jinja2 environment with ``StrictUndefined`` so a
missing variable is a loud error rather than a silent empty string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jinja2 import StrictUndefined, TemplateError, UndefinedError
from jinja2.sandbox import SandboxedEnvironment

from notifly.domain.enums import ChannelType, VariableType
from notifly.domain.errors import TemplateRenderingError, VariableValidationError
from notifly.domain.models.template import Template, VariableDef


@dataclass(frozen=True)
class RenderedContent:
    subject: str | None
    body: str


_STRICT_ENVIRONMENT = SandboxedEnvironment(undefined=StrictUndefined)


def _type_error(name: str, expected: str, value: Any) -> str:
    return f"variable '{name}' must be a {expected}, got {type(value).__name__}"


def _check_type(definition: VariableDef, name: str, value: Any, errors: list[str]) -> None:
    if definition.type is VariableType.STRING:
        if not isinstance(value, str):
            errors.append(_type_error(name, "string", value))
    elif definition.type is VariableType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(_type_error(name, "number", value))
    elif definition.type is VariableType.BOOLEAN and not isinstance(value, bool):
        errors.append(_type_error(name, "boolean", value))


def validate_variables(definitions: list[VariableDef], provided: dict[str, Any]) -> dict[str, Any]:
    """Validate supplied variables against the declarations and apply defaults.

    Returns a fully-resolved variable map ready for rendering. Unknown
    variables are rejected so typos surface immediately.
    """
    declared: dict[str, VariableDef] = {}
    errors: list[str] = []
    for definition in definitions:
        if definition.name in declared:
            errors.append(f"duplicate variable declaration '{definition.name}'")
            continue
        declared[definition.name] = definition

    unknown = sorted(set(provided) - set(declared))
    if unknown:
        errors.append(f"unknown variables: {', '.join(unknown)}")

    resolved: dict[str, Any] = {}
    for name, definition in declared.items():
        if name in provided:
            value = provided[name]
            _check_type(definition, name, value, errors)
            resolved[name] = value
            continue
        if definition.required:
            if definition.default is not None:
                resolved[name] = definition.default
            else:
                errors.append(f"missing required variable '{name}'")
            continue
        if definition.default is not None:
            resolved[name] = definition.default
        # Optional without a default stays undefined: rendering succeeds unless
        # the template actually references it (StrictUndefined then errors).

    if errors:
        raise VariableValidationError("; ".join(errors))
    return resolved


def render_template(
    template: Template, variables: dict[str, Any]
) -> dict[ChannelType, RenderedContent]:
    """Render every channel's content with the given (resolved) variables."""
    rendered: dict[ChannelType, RenderedContent] = {}
    for channel_type, content in template.channels.items():
        try:
            subject = None
            if content.subject:
                subject = _STRICT_ENVIRONMENT.from_string(content.subject).render(**variables)
            body = _STRICT_ENVIRONMENT.from_string(content.body).render(**variables)
        except (UndefinedError, TemplateError) as exc:
            raise TemplateRenderingError(
                f"Failed to render '{channel_type}' content: {exc}"
            ) from exc
        rendered[channel_type] = RenderedContent(subject=subject, body=body)
    return rendered
