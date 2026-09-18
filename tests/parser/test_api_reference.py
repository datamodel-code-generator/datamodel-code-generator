"""Exercise canonical addresses through the existing parser factory integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import SchemaParseError
from datamodel_code_generator.parser._api_reference import ApiModelResolver
from datamodel_code_generator.parser.openapi import OpenAPIParser
from tests.conftest import assert_output

if TYPE_CHECKING:
    from datamodel_code_generator._source import YamlValue

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/api_scope"
EXPECTED = DATA / "expected/main/generation_platform/api_scope"


class ReferenceParser(OpenAPIParser):
    """Use the opt-in resolver through the established S01 factory."""

    _model_resolver_factory = staticmethod(ApiModelResolver)
    model_resolver: ApiModelResolver

    def _parse_specification(self, specification: dict[str, YamlValue], path_parts: list[str]) -> None:
        self.model_resolver.loaded_documents["/".join(path_parts)] = specification
        super()._parse_specification(specification, path_parts)

    def _get_ref_body(self, resolved_ref: str) -> dict[str, YamlValue]:
        raw = super()._get_ref_body(resolved_ref)
        self.model_resolver.loaded_documents[resolved_ref] = raw
        return raw


def test_canonical_declaration_generation() -> None:
    """Reserve one name for alternate pointer spellings and preserve compiler paths."""
    parser = ReferenceParser(SOURCE / "reference-declarations.json", formatters=[])
    try:
        assert_output(parser.parse(), EXPECTED / "reference.py")
        references = parser.model_resolver.references
        assert_output(
            json.dumps(
                {
                    "foo_keys": [key for key in references if key.endswith("/Foo")],
                    "stable_paths": all(
                        parser.model_resolver.join_path((reference.path,)) == reference.path
                        for reference in references.values()
                    ),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "reference-state.txt",
        )
    finally:
        parser.dispose()


@pytest.mark.parametrize("case", ["percent", "utf8", "escape", "tilde", "ambiguous"])
def test_invalid_pointer_generation(case: str) -> None:
    """Reject invalid URI pointers during real reference acquisition."""
    parser = ReferenceParser(SOURCE / f"pointer-{case}.json", formatters=[])
    try:
        with pytest.raises(SchemaParseError, match="API_REF_NONCANONICAL_POINTER"):
            parser.parse()
    finally:
        parser.dispose()


def test_named_reference_generation() -> None:
    """Delegate named IDs and empty root pointers to the existing resolver."""
    parser = ReferenceParser(SOURCE / "anchors.json", formatters=[])
    try:
        assert_output(parser.parse(), EXPECTED / "reference-anchors.py")
    finally:
        parser.dispose()
