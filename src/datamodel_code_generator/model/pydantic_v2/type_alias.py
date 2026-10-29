"""Compatibility aliases for older Pydantic dataclass runtimes."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import ClassVar

from typing_extensions import override

from datamodel_code_generator.imports import IMPORT_TYPE_ALIAS, IMPORT_TYPE_ALIAS_TYPE, Import
from datamodel_code_generator.model.type_alias import TypeAliasTypeBackport


class TypeAlias(TypeAliasTypeBackport):
    """Avoid named reference aliases that Pydantic before 2.10 cannot resolve."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "PydanticV2TypeAlias.jinja2"
    _WRITES_REFERENCE_ALIAS_TYPE: ClassVar[bool] = False

    @cached_property
    @override
    def template_file_path(self) -> Path:
        """Keep the existing custom alias template ahead of the builtin fallback."""
        template_path = super().template_file_path
        if self.custom_template_dir is None or template_path != Path(self.TEMPLATE_FILE_PATH):
            return template_path
        return self._custom_template_path(TypeAliasTypeBackport.TEMPLATE_FILE_PATH) or template_path

    @property
    @override
    def imports(self) -> tuple[Import, ...]:
        """Import the alias syntax selected by the compatibility template."""
        imports = super().imports
        if self._aliases_reference() and self.template_file_path.name != TypeAliasTypeBackport.TEMPLATE_FILE_PATH:
            return (*(value for value in imports if value != IMPORT_TYPE_ALIAS_TYPE), IMPORT_TYPE_ALIAS)
        return imports
