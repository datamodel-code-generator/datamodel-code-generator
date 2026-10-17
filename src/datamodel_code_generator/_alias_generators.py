"""Alias generators matching Pydantic 2.8+ ``pydantic.alias_generators``, independent of the installed Pydantic.

The functions reproduce ``to_pascal``, ``to_camel`` and ``to_snake`` from Pydantic 2.8 through 2.13, so generated
aliases are identical on every installed Pydantic, with the case rules of the target Python version Pydantic runs on.
Pydantic is MIT licensed, Copyright (c) 2017 to present Pydantic
Services Inc. and individual contributors.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._target_unicode import lower, title

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import PythonVersion

_PASCAL_UNDERSCORE: Final = re.compile(r"([0-9A-Za-z])_(?=[0-9A-Z])")
_CAMEL_ALREADY: Final = re.compile(r"^[a-z]+[A-Za-z0-9]*$")
_DIGIT_LOWER: Final = re.compile(r"\d[a-z]")
_LEADING_UPPER: Final = re.compile(r"(^_*[A-Z])")
_SNAKE_STEPS: Final = (
    re.compile(r"([A-Z]+)([A-Z][a-z])"),
    re.compile(r"([a-z])([A-Z])"),
    re.compile(r"([0-9])([A-Z])"),
    re.compile(r"([a-z])([0-9])"),
)


def to_pascal(snake: str, target_python_version: PythonVersion | None = None) -> str:
    """Convert a snake_case string to PascalCase."""
    return _PASCAL_UNDERSCORE.sub(lambda match: match.group(1), title(snake, target_python_version))


def to_camel(snake: str, target_python_version: PythonVersion | None = None) -> str:
    """Convert a snake_case string to camelCase, keeping camelCase input without a digit before a lowercase letter."""
    if _CAMEL_ALREADY.match(snake) and not _DIGIT_LOWER.search(snake):
        return snake
    return _LEADING_UPPER.sub(lambda match: match.group(1).lower(), to_pascal(snake, target_python_version))


def to_snake(camel: str, target_python_version: PythonVersion | None = None) -> str:
    """Convert a PascalCase, camelCase, or kebab-case string to snake_case."""
    snake = camel
    for step in _SNAKE_STEPS:
        snake = step.sub(lambda match: f"{match.group(1)}_{match.group(2)}", snake)
    return lower(snake.replace("-", "_"), target_python_version)
