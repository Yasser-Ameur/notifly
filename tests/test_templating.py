"""M4 unit tests for template variable validation and rendering."""

from __future__ import annotations

import pytest

from notifly.application.templating import render_template, validate_variables
from notifly.domain.enums import ChannelType, VariableType
from notifly.domain.errors import TemplateRenderingError, VariableValidationError
from notifly.domain.models.template import Template, TemplateChannelContent, VariableDef

APP_ID = "22222222-2222-2222-2222-222222222222"
TPL_ID = "11111111-1111-1111-1111-111111111111"


def _template(variables: list[VariableDef] | None = None) -> Template:
    return Template(
        id=TPL_ID,
        application_id=APP_ID,
        name="welcome",
        event="user_welcome",
        variables=variables or [],
        channels={
            ChannelType.EMAIL: TemplateChannelContent(
                subject="Hi {{ name }}", body="Welcome, {{ name }}!"
            )
        },
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_validate_variables_resolves_all() -> None:
    definitions = [
        VariableDef(name="name", type=VariableType.STRING),
        VariableDef(name="count", type=VariableType.NUMBER),
    ]
    resolved = validate_variables(definitions, {"name": "Alice", "count": 3})
    assert resolved == {"name": "Alice", "count": 3}


def test_validate_variables_applies_defaults() -> None:
    definitions = [
        VariableDef(name="name", type=VariableType.STRING, required=False, default="friend"),
        VariableDef(name="city", type=VariableType.STRING, required=False),
    ]
    resolved = validate_variables(definitions, {})
    assert resolved == {"name": "friend"}
    assert "city" not in resolved


def test_validate_variables_required_default_satisfies() -> None:
    definitions = [VariableDef(name="level", type=VariableType.STRING, default="info")]
    resolved = validate_variables(definitions, {})
    assert resolved == {"level": "info"}


def test_validate_variables_rejects_missing_required() -> None:
    definitions = [VariableDef(name="name", type=VariableType.STRING)]
    with pytest.raises(VariableValidationError) as exc:
        validate_variables(definitions, {})
    assert "missing required variable 'name'" in str(exc.value)


def test_validate_variables_rejects_wrong_type() -> None:
    definitions = [
        VariableDef(name="count", type=VariableType.NUMBER),
        VariableDef(name="flag", type=VariableType.BOOLEAN),
    ]
    with pytest.raises(VariableValidationError) as exc:
        validate_variables(definitions, {"count": "three", "flag": "yes"})
    message = str(exc.value)
    assert "must be a number" in message
    assert "must be a boolean" in message


def test_validate_variables_bool_not_a_number() -> None:
    definitions = [VariableDef(name="count", type=VariableType.NUMBER)]
    with pytest.raises(VariableValidationError):
        validate_variables(definitions, {"count": True})


def test_validate_variables_rejects_unknown() -> None:
    with pytest.raises(VariableValidationError) as exc:
        validate_variables([], {"typo_variable": 1})
    assert "unknown variables: typo_variable" in str(exc.value)


def test_validate_variables_rejects_duplicate_declarations() -> None:
    definitions = [
        VariableDef(name="name", type=VariableType.STRING),
        VariableDef(name="name", type=VariableType.NUMBER),
    ]
    with pytest.raises(VariableValidationError) as exc:
        validate_variables(definitions, {"name": "x"})
    assert "duplicate variable declaration 'name'" in str(exc.value)


def test_render_template_substitutes_variables() -> None:
    template = _template([VariableDef(name="name", type=VariableType.STRING)])
    rendered = render_template(template, {"name": "Alice"})
    email = rendered[ChannelType.EMAIL]
    assert email.subject == "Hi Alice"
    assert email.body == "Welcome, Alice!"


def test_render_template_without_references() -> None:
    template = Template(
        id=TPL_ID,
        application_id=APP_ID,
        name="static",
        event="static",
        channels={ChannelType.EMAIL: TemplateChannelContent(body="Hello world")},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    rendered = render_template(template, {})
    assert rendered[ChannelType.EMAIL].body == "Hello world"
    assert rendered[ChannelType.EMAIL].subject is None


def test_render_template_strict_undefined_errors() -> None:
    template = _template([VariableDef(name="name", type=VariableType.STRING)])
    with pytest.raises(TemplateRenderingError):
        render_template(template, {})


def test_render_template_sandbox_blocks_attribute_access() -> None:
    template = Template(
        id=TPL_ID,
        application_id=APP_ID,
        name="evil",
        event="evil",
        channels={ChannelType.EMAIL: TemplateChannelContent(body="{{ ''.__class__.__mro__ }}")},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    with pytest.raises(TemplateRenderingError):
        render_template(template, {})


def test_render_template_multiple_channels() -> None:
    template = Template(
        id=TPL_ID,
        application_id=APP_ID,
        name="multi",
        event="multi",
        channels={
            ChannelType.EMAIL: TemplateChannelContent(body="e"),
            ChannelType.SLACK: TemplateChannelContent(body="s", subject="sub"),
        },
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    rendered = render_template(template, {})
    assert set(rendered) == {ChannelType.EMAIL, ChannelType.SLACK}
    assert rendered[ChannelType.SLACK].subject == "sub"


def test_render_template_syntax_error_raises() -> None:
    template = Template(
        id=TPL_ID,
        application_id=APP_ID,
        name="bad",
        event="bad",
        channels={ChannelType.EMAIL: TemplateChannelContent(body="{{ broken ")},
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    with pytest.raises(TemplateRenderingError):
        render_template(template, {})
