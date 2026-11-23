"""Write str for the formats msgspec cannot decode under --use-standard-primitive-types, from an OpenAPI document."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import msgspec
import pytest

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = OPEN_API_DATA_PATH / "msgspec_standard_primitive_types.yaml"
DOCUMENTS = {
    "valid": {
        "address": "192.0.2.1",
        "address_v6": "2001:db8::1",
        "network": "10.0.0.0/8",
        "network_v6": "2001:db8::/32",
        "location": "/etc/hosts",
        "gateway": None,
        "addresses": ["::1"],
        "either_address": "192.0.2.2",
        "id": "123e4567-e89b-12d3-a456-426614174000",
    },
    "address is a number": {"address": 1},
    "address of a list is a number": {"address": "192.0.2.1", "addresses": [1]},
    "id is no UUID": {"address": "192.0.2.1", "id": "not-a-uuid"},
}


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param((), id="released"),
        pytest.param(("--use-str-for-formats-msgspec-cannot-decode",), id="option"),
    ],
)
def test_msgspec_standard_primitive_types_openapi(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Write ``str`` for the formats msgspec cannot decode in OpenAPI schemas under the option, also nullable ones.

    ``Host`` holds each format as a property, a nullable property, list items through ``Address`` and a union
    member. Without the option it keeps the standard library classes, which only a ``dec_hook`` decodes; with it
    ``Host`` decodes the documents of the schema; ``id`` stays ``UUID``, which msgspec checks.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp"),
            *("--target-python-version", "3.11", "--use-standard-primitive-types", "--strict-nullable"),
            *extra_args,
        ],
    )
    with _generated_model(output_file, "msgspec_standard_primitive_types_openapi_model", "Host") as model:
        verdicts = "".join(f"{label}: {_verdict(model, document)}\n" for label, document in DOCUMENTS.items())
    assert_output(verdicts, name="payloads")


def _verdict(model: type, document: object) -> str:
    """Return whether the generated model decodes a JSON document: a refused type is a ``TypeError``."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "refused"
    return "accepted"
