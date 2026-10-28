"""``--use-enum-values-in-discriminator`` writes the values of the Enum members that tag msgspec variants.

Up to 0.86.0 a msgspec variant whose tag field is typed by an Enum was written with ``Literal[<Enum>.<member>]``, which
msgspec refuses the first time it builds a decoder for the Struct ("Literal may only contain None/booleans/integers/
strings"), so every such variant failed to decode. msgspec takes no enum member in a ``Literal``, so the variant now
takes the values of its members, ``Literal['v1']``, which decode the same JSON documents.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import yaml
from jsonschema import Draft202012Validator

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = OPEN_API_DATA_PATH / "msgspec_enum_values_in_discriminator.yaml"
PAYLOADS: tuple[tuple[str, str | None, tuple[dict[str, Any], ...]], ...] = (
    ("RequestBase", None, ({"version": "v1"}, {"version": "v2"}, {"version": "v3"}, {})),
    (
        "RequestV1",
        "v1",
        ({"version": "v1", "request_id": "a"}, {"version": "v2", "request_id": "a"}, {"version": "v1"}),
    ),
    ("RequestV2", "v2", ({"version": "v2"}, {"version": "v1"}, {"version": "v3"}, {})),
)
"""The model, the ``version`` that selects it in ``Request`` and documents for it: the Enum of the base, then the
variants that its members tag."""


def test_msgspec_enum_values_in_discriminator(output_file: Path) -> None:
    """Write ``Literal['v1']`` for the variant of a base that declares the Enum, so each variant decodes.

    The base keeps the Enum field and each variant narrows it to the value of its member. A document is a variant
    when its ``version`` selects it in ``Request``: the variant decodes the documents its schema accepts then, and
    rejects the others, the value of the other variant and a value outside the Enum.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-enum-values-in-discriminator",
            "--disable-warnings",
        ],
    )
    components = yaml.safe_load(INPUT_PATH.read_text(encoding="utf-8"))["components"]
    lines: list[str] = []
    with _generated_model(output_file, "msgspec_enum_values_in_discriminator_model", "RequestV1") as variant:
        namespace = vars(sys.modules[variant.__module__])
        for name, tag, documents in PAYLOADS:
            selected = [] if tag is None else [{"properties": {"version": {"const": tag}}}]
            validator = Draft202012Validator({
                "allOf": [{"$ref": f"#/components/schemas/{name}"}, *selected],
                "components": components,
            })
            for document in documents:
                try:
                    msgspec.json.decode(json.dumps(document), type=namespace[name])
                except msgspec.ValidationError:
                    verdict = "rejects"
                else:
                    verdict = "accepts"
                schema_verdict = "accepts" if validator.is_valid(document) else "rejects"
                lines.append(f"{name} {json.dumps(document)}: schema {schema_verdict}, model {verdict}\n")
    assert_output("".join(lines), name="payloads")


def test_msgspec_enum_values_in_discriminator_keeps_members_of_other_kinds(output_file: Path) -> None:
    """Keep the members in the ``Literal`` of a tag whose Enum values msgspec holds in no ``Literal``.

    The Enums of floats stay as they were written: msgspec decodes no Enum of floats, so no form of their tags decodes.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_float_mapping.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-enum-values-in-discriminator",
            "--disable-warnings",
        ],
    )


def test_msgspec_enum_values_in_discriminator_enum_of_another_module(output_dir: Path) -> None:
    """Import nothing for the Enum of another module once its members are written as values.

    The variants of ``a.json`` inherit the ``version`` Enum of ``b.json``. Their ``Literal`` held ``b.VersionEnum.v1``
    and imported the module of the Enum, which they no longer name.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "msgspec_enum_values_in_discriminator_modules",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-enum-values-in-discriminator",
            "--disable-warnings",
        ],
    )
