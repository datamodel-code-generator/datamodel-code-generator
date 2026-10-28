"""Write a ``--type-overrides`` value with brackets, such as ``typing.Literal[1, 2]``, as the annotation it spells."""

from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING

import msgspec
import pytest
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    GRAPHQL_DATA_PATH,
    _generated_model,
    _model_json_validator,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

_OVERRIDES = {
    "annotations": {
        "Level": "typing.Literal[1, 2]",
        "Model.mode": "typing.Optional[typing.Literal['a', 'b']]",
        "Model.code": "typing.Optional[Code]",
    },
    "members": {
        "Level": "typing.Literal[http.HTTPStatus.OK, http.HTTPStatus.CREATED]",
        "Model.mode": "typing.Literal[0.5, 1.5]",
    },
}


@pytest.mark.parametrize(
    ("variant", "output_model", "extra_args"),
    [
        ("annotations", "pydantic_v2.BaseModel", []),
        ("annotations", "pydantic_v2.BaseModel", ["--field-constraints"]),
        ("annotations", "pydantic_v2.dataclass", ["--field-constraints"]),
        ("annotations", "dataclasses.dataclass", []),
        ("annotations", "typing.TypedDict", []),
        ("annotations", "msgspec.Struct", []),
        ("members", "pydantic_v2.BaseModel", []),
        ("members", "pydantic_v2.dataclass", ["--field-constraints"]),
        ("members", "dataclasses.dataclass", []),
        ("members", "typing.TypedDict", []),
    ],
)
def test_graphql_type_overrides_literal(
    variant: str, output_model: str, extra_args: list[str], output_file: Path
) -> None:
    """Write a scalar override and a field override that are annotations as the annotation, imported by its names.

    Up to 0.86.0 the value was split at its last dot as a class path, so the module imported ``Literal[1, 2]`` from
    ``typing`` and failed to import. msgspec is left out of the members: its ``Literal`` holds no float and no enum
    member. A ``typing.TypedDict`` is not validated: Pydantic builds one only from ``typing_extensions`` before
    Python 3.12.
    """
    name = "type_overrides_literal"
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / f"{name}.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[
            "--output-model-type",
            output_model,
            *extra_args,
            "--type-overrides",
            json.dumps(_OVERRIDES[variant]),
            "--target-python-version",
            "3.11",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    if output_model == "typing.TypedDict":
        return
    error_type: type[Exception] = msgspec.ValidationError
    verdicts = []
    with _generated_model(output_file, f"graphql_{name}", "Model") as model:
        validate = partial(msgspec.json.decode, type=model)
        if output_model != "msgspec.Struct":
            validate = _model_json_validator(model)
            error_type = ValidationError
        for kind, payloads in json.loads((DATA_PATH / "payloads" / f"{name}_graphql.json").read_text())[
            variant
        ].items():
            for payload in payloads:
                try:
                    validate(json.dumps(payload))
                except error_type:
                    verdict = "rejected"
                else:
                    verdict = "accepted"
                verdicts.append(f"{kind} {json.dumps(payload)}: {verdict}\n")
    assert_output("".join(verdicts), name="payloads")
