import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from pydantic_settings import BaseSettings, SettingsError


SettingsT = TypeVar("SettingsT", bound=BaseSettings)
FIELD_NAME_PATTERN = re.compile(r'field "(?P<field>[^"]+)"')
ENVIRONMENT_NAME_PATTERN = re.compile(r"\b[A-Z][A-Z0-9]*(?:__[A-Z0-9_]+)+\b")


class ConfigurationError(ValueError):
    """A settings error formatted for command-line users."""


def load_settings(settings_type: type[SettingsT]) -> SettingsT:
    try:
        return settings_type()
    except (SettingsError, ValidationError) as exc:
        raise ConfigurationError(
            format_configuration_error(settings_type, exc)
        ) from None


def format_configuration_error(
    settings_type: type[BaseSettings],
    exc: SettingsError | ValidationError,
) -> str:
    if isinstance(exc, SettingsError):
        match = FIELD_NAME_PATTERN.search(str(exc))
        field = match.group("field").upper() if match else "SETTINGS"
        issues = [(field, "could not parse the nested setting")]
    else:
        issues = []
        for error in exc.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        ):
            location = tuple(str(part) for part in error["loc"])
            locations = [location]
            if error["type"] == "missing":
                expanded = _expand_required_fields(settings_type, location)
                if expanded:
                    locations = expanded

            message = _format_issue_message(error["type"], error["msg"])
            explicit_name = ENVIRONMENT_NAME_PATTERN.search(message)
            if explicit_name:
                name = explicit_name.group(0)
                issues.append((name, _remove_environment_name(message, name)))
            else:
                issues.extend((_environment_name(item), message) for item in locations)

    rendered = ["Configuration error:"]
    rendered.extend(f"- {name}: {message}" for name, message in _deduplicate(issues))
    return "\n".join(rendered)


def _expand_required_fields(
    settings_type: type[BaseSettings],
    location: tuple[str, ...],
) -> list[tuple[str, ...]]:
    model_type: type[BaseModel] = settings_type
    field = None
    for part in location:
        field = model_type.model_fields.get(part)
        if field is None:
            return []
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            model_type = annotation

    if field is None:
        return []
    annotation = field.annotation
    if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
        return []

    return _required_leaf_locations(annotation, location)


def _required_leaf_locations(
    model_type: type[BaseModel],
    prefix: tuple[str, ...],
) -> list[tuple[str, ...]]:
    locations = []
    for name, field in model_type.model_fields.items():
        if not field.is_required():
            continue
        annotation = field.annotation
        location = (*prefix, name)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            locations.extend(_required_leaf_locations(annotation, location))
        else:
            locations.append(location)
    return locations


def _format_issue_message(error_type: str, message: str) -> str:
    if error_type == "missing":
        return "required"
    if error_type == "bool_parsing":
        return "expected a valid boolean"
    if error_type == "path_type":
        return "expected a valid path"
    if message.startswith("Value error, "):
        return message.removeprefix("Value error, ")
    if message.startswith("Input should be "):
        return "expected " + message.removeprefix("Input should be ")
    return message


def _environment_name(location: tuple[str, ...]) -> str:
    return "__".join(part.upper() for part in location)


def _remove_environment_name(message: str, name: str) -> str:
    if message.startswith(f"{name} is "):
        return message.removeprefix(f"{name} is ")
    if message.startswith(f"{name} "):
        return message.removeprefix(f"{name} ")
    return message


def _deduplicate(issues: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return list(dict.fromkeys(issues))
