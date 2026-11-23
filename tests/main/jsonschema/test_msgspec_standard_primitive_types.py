"""Write str for the formats msgspec cannot decode under --use-standard-primitive-types, on request (#7342)."""

from __future__ import annotations

import json
import typing
from contextlib import nullcontext
from typing import TYPE_CHECKING

import msgspec
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema import ValidationError as SchemaValidationError

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_standard_primitive_types.json"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_standard_primitive_types.json"
ARGS = ("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp")
OPTION = "--use-str-for-formats-msgspec-cannot-decode"
OPTION_SETS = {
    "released": ("--target-python-version", "3.11", "--use-standard-primitive-types"),
    "option": ("--target-python-version", "3.11", "--use-standard-primitive-types", OPTION),
    "without_option": ("--target-python-version", "3.11"),
    "preset_20260619": ("--preset", "standard-py311-20260619"),
    "preset_20261123": ("--preset", "standard-py311-20261123"),
}
WARNING_SETS = ("released", "preset_20260619")
"""The option sets that write the classes msgspec cannot decode: ``short_address`` loses ``maxLength``."""
WARNING = r"msgspec\.Meta does not take max_length on IPv4Address; .* is generated without this constraint"


@pytest.mark.cli_doc(
    options=["--use-str-for-formats-msgspec-cannot-decode"],
    option_description="""Write str for the formats msgspec cannot decode under `--use-standard-primitive-types`.

msgspec has no decoder for `IPv4Address`, `IPv6Address`, `IPv4Network`, `IPv6Network` and `Path`, so a Struct that
holds one of them (`format: ipv4`, `ipv6`, `ipv4-network`, `ipv6-network` or `path`) under
`--use-standard-primitive-types` decodes only with a `dec_hook`, and a union of such types is refused when the Struct
is built. With this option the field is `str`, as without `--use-standard-primitive-types`, and a document that holds
it decodes; `UUID`, `Decimal` and the date types keep their classes, which msgspec decodes. A union that joins such a
format with one of those classes (`str | UUID`) is still refused; `--merge-msgspec-str-like-union-members` writes it as
one `str`. The other output model types keep the standard library classes.""",
    input_schema="jsonschema/msgspec_standard_primitive_types.json",
    cli_args=[
        "--output-model-type",
        "msgspec.Struct",
        "--target-python-version",
        "3.11",
        "--use-standard-primitive-types",
        "--use-str-for-formats-msgspec-cannot-decode",
    ],
    golden_output="main/jsonschema/msgspec_standard_primitive_types/msgspec_standard_primitive_types[option].py",
    comparison_output=(
        "main/jsonschema/msgspec_standard_primitive_types/msgspec_standard_primitive_types[released].py"
    ),
    related_options=["--use-standard-primitive-types"],
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_standard_primitive_types(output_file: Path, option_set: str) -> None:
    """Write ``str`` for the formats whose standard library type msgspec cannot decode, under the option.

    msgspec has no decoder for ``IPv4Address``, ``IPv6Address``, ``IPv4Network``, ``IPv6Network`` and ``Path``, so
    ``--use-standard-primitive-types`` (which every preset turns on for msgspec) writes Structs that reject every
    document holding such a field unless the caller decodes it with a ``dec_hook``, and unions that msgspec refuses
    to build. ``released`` keeps those classes, as in 0.100.0: its documents decode only with the hook, which the
    payload file records beside the plain verdict. With the option (and with the 20261123 presets, which set it)
    ``Model`` holds each format as a property, nullable, with a default, as list items, map values, pattern property
    values, the items of a ``prefixItems`` array, union members, a type alias and a nested Struct, and beside
    ``maxLength``; all are ``str`` as without ``--use-standard-primitive-types``, so the documents decode and the JSON
    types still reject, while ``UUID`` keeps its check.
    """
    with pytest.warns(UserWarning, match=WARNING) if option_set in WARNING_SETS else nullcontext():
        run_main_and_assert(
            input_path=INPUT,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*ARGS, *OPTION_SETS[option_set]],
        )
    payloads = json.loads(PAYLOADS.read_text())
    validator = Draft202012Validator(json.loads(INPUT.read_text()), format_checker=FormatChecker())
    for payload in payloads["valid"]:
        validator.validate(payload)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"][0], **change})
    documents = [
        *(("valid", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(change)}", {**payloads["valid"][0], **change}) for change in payloads["invalid"]),
    ]
    with _generated_model(output_file, "msgspec_standard_primitive_types_model", "Model") as model:
        verdicts = "".join(
            f"{label}: {_verdict(model, document)}, with dec_hook {_verdict(model, document, dec_hook=_construct)}\n"
            for label, document in documents
        )
        hints = typing.get_type_hints(model, include_extras=True)
        verdicts += "".join(
            f"field {name}: {_field_verdict(hints[name], value)}, "
            f"with dec_hook {_field_verdict(hints[name], value, dec_hook=_construct)}\n"
            for name, value in payloads["valid"][1].items()
        )
    assert_output(verdicts, name="payloads")


def _field_verdict(hint: object, value: object, **options: object) -> str:
    """Return whether msgspec converts a JSON value to the type of one field: a refused type is a ``TypeError``."""
    try:
        msgspec.convert(value, hint, **options)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "refused"
    return "accepted"


def _construct(type_: type, value: object) -> object:
    """Build the class msgspec has no decoder for from its JSON value."""
    return type_(value)


def _verdict(model: type, document: object, **options: object) -> str:
    """Return whether the generated model decodes a JSON document: a refused type is a ``TypeError``."""
    try:
        msgspec.json.decode(json.dumps(document), type=model, **options)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "refused"
    return "accepted"
