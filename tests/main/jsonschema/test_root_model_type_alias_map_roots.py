"""End-to-end coverage for the root models of object maps under ``--use-root-model-type-alias``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import RootModel, TypeAdapter, ValidationError

from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

KEEP_CONSTRAINED_MAP_ROOTS_AS_CLASSES = "--keep-constrained-map-roots-as-classes"
USE_ROOT_MODEL_TYPE_ALIAS = "--use-root-model-type-alias"
MAP_ROOTS_PATH = JSON_SCHEMA_DATA_PATH / "root_model_type_alias_map_roots"
REQUEST_RESPONSE = ("--read-only-write-only-model-type", "request-response")
SCHEMA_VALIDATORS = "--generate-schema-validators"
TWO_ITEMS = {"a": {"name": "n"}, "b": {"name": "m"}}
THREE_ITEMS = {**TWO_ITEMS, "c": {"name": "o"}}
TWO_LABELS = {"a": "x", "b": "y"}
FOUR_BRANCHES = {key: {} for key in "abcd"}
MAP_PAYLOADS: dict[str, dict[str, object]] = {
    "Labels": {"valid one key": {"a": "x"}, "invalid two keys": TWO_LABELS},
    "MinLabels": {"valid one key": {"a": "x"}, "invalid no key": {}},
    "ItemMap": {"valid two items": TWO_ITEMS, "invalid three items": THREE_ITEMS},
    "CountOnly": {"valid one key": {"a": 1}, "invalid two keys": {"a": 1, "b": 2}},
    "AnyValues": {"valid one key": {"a": [1]}, "invalid two keys": {"a": 1, "b": None}},
    "LookaroundValues": {"valid y value": {"a": "y"}, "invalid x value": {"a": "x"}},
    "RefLabels": {"valid one key": {"a": "x"}, "invalid two keys": TWO_LABELS},
    "PlainMap": {"valid two keys": TWO_LABELS},
    "KeyPatternCount": {"valid two keys": TWO_LABELS, "invalid three keys": {**TWO_LABELS, "c": "z"}},
    "PatternKeys": {
        "valid two keys": {"x1": "a", "x2": "b"},
        "invalid three keys": {"x1": "a", "x2": "b", "x3": "c"},
        "invalid other key": {"y": "a"},
    },
    "NullableMap": {"valid null": None, "invalid two keys": TWO_LABELS},
}
PAYLOADS: dict[str, dict[str, dict[str, object]]] = {
    "maps": MAP_PAYLOADS,
    "cycle": {
        "Tree": {
            "valid two levels": {"a": {"tree": {"b": {}}}},
            "invalid four branches": FOUR_BRANCHES,
        },
        "Forest": {
            "valid nested tree": {"branch": {"tree": {"a": {}}}},
            "invalid nested tree": {"branch": {"tree": FOUR_BRANCHES}},
        },
    },
    "root": {"Settings": {"valid two keys": {"a": 1, "b": 2}, "invalid three keys": {"a": 1, "b": 2, "c": 3}}},
    "other_document": {
        "Labels": MAP_PAYLOADS["Labels"],
        "MinLabels": MAP_PAYLOADS["MinLabels"],
    },
    "request_response": {
        "ItemMapRequest": {"valid two items": TWO_ITEMS, "invalid three items": THREE_ITEMS},
        "ItemMapResponse": {"valid two items": TWO_ITEMS, "invalid three items": THREE_ITEMS},
        "PatternItemsRequest": {
            "valid two items": {"x1": {"name": "n"}, "x2": {"name": "m"}},
            "invalid three items": {"x1": {"name": "n"}, "x2": {"name": "m"}, "x3": {"name": "o"}},
        },
    },
    "openapi": {"Labels": MAP_PAYLOADS["Labels"]},
}
"""The payloads each model of an input parses, by model name: the label says whether the schema accepts it."""


def _case(input_name: str, case_id: str, *extra_args: str, changes: bool = True, payloads: bool = True) -> object:
    """Return a case of ``test_constrained_map_roots_as_classes``."""
    return pytest.param(input_name, list(extra_args), changes, payloads, id=f"{input_name}-{case_id}")


def _type_name(value: object) -> str:
    """Return the name of a value's class, ``alias`` for a bare ``RootModel[...]``, whose name varies by Python."""
    value_type = type(value)
    if getattr(value_type, "__pydantic_generic_metadata__", {}).get("origin") is RootModel:
        return "alias"
    return value_type.__name__


def _outcome(model: type[object], payload: object) -> str:
    """Return the value a payload parses to and its type, or the types of the errors Pydantic reports for it."""
    adapter = TypeAdapter(model)
    try:
        parsed = adapter.validate_python(payload)
    except ValidationError as error:
        return "rejected: " + ", ".join(sorted({item["type"] for item in error.errors()}))
    return f"accepted {adapter.dump_python(parsed)!r} as {_type_name(parsed)}"


def _outcomes(output_path: Path, input_name: str, name: str) -> str:
    """Return the outcomes of the payloads of every model of an input, and the field types of ``Model()``."""
    outcomes = f"{name}\n"
    for model_name, cases in PAYLOADS[input_name].items():
        with _generated_model(output_path, f"map_roots_{name}", model_name) as model:
            outcomes += "".join(
                f"{model_name} {label}: {_outcome(model, payload)}\n" for label, payload in cases.items()
            )
    if input_name == "maps":
        with _generated_model(output_path, f"map_roots_{name}", "Model") as model:
            empty = model()
            outcomes += f"Model() field types: {[_type_name(getattr(empty, field)) for field in model.model_fields]}\n"
    return outcomes


@pytest.mark.cli_doc(
    options=[KEEP_CONSTRAINED_MAP_ROOTS_AS_CLASSES],
    option_description="""Keep the root model of a constrained object map a class under `--use-root-model-type-alias`.

`--use-root-model-type-alias` writes a root model as an alias, `Tags = RootModel[list[str]]`, and keeps a class where
the alias cannot write what the root holds: field constraints, a pattern that needs the `python-re` regex engine, or the
rules of `--generate-schema-validators`. The root model of an object map, an object without properties whose
`additionalProperties` is a schema or that sets only `minProperties`, `maxProperties` or `propertyNames`, stays an
alias anyway, as in 0.104.0, so `Labels = RootModel[dict[str, str]]` drops `maxProperties: 1` and accepts any number
of keys, while an `allOf` of the same map is a class that keeps it. With this option such a map root is a class as
every other constrained root is: `class Labels(RootModel[dict[str, str]])` with `root: dict[str, str] = Field(...,
max_length=1)`. A map whose root holds no such constraint stays an alias. Pydantic v2 only.""",
    input_schema="jsonschema/root_model_type_alias_map_roots/maps.json",
    cli_args=[USE_ROOT_MODEL_TYPE_ALIAS, KEEP_CONSTRAINED_MAP_ROOTS_AS_CLASSES],
    golden_output="main/jsonschema/root_model_type_alias_map_roots/constrained_map_roots_as_classes[maps-alias].option.py",
    comparison_output=(
        "main/jsonschema/root_model_type_alias_map_roots/constrained_map_roots_as_classes[maps-alias].default.py"
    ),
    related_options=[USE_ROOT_MODEL_TYPE_ALIAS, SCHEMA_VALIDATORS],
)
@pytest.mark.parametrize(
    ("input_name", "extra_args", "changes", "payloads"),
    [
        _case("maps", "alias", USE_ROOT_MODEL_TYPE_ALIAS),
        _case("maps", "schema-validators", USE_ROOT_MODEL_TYPE_ALIAS, SCHEMA_VALIDATORS),
        _case("maps", "collapse", USE_ROOT_MODEL_TYPE_ALIAS, "--collapse-root-models"),
        _case("maps", "field-constraints", USE_ROOT_MODEL_TYPE_ALIAS, "--field-constraints"),
        _case(
            "maps",
            "validate-default",
            USE_ROOT_MODEL_TYPE_ALIAS,
            "--use-validate-default-for-root-model-type-aliases",
        ),
        _case("maps", "preset", USE_ROOT_MODEL_TYPE_ALIAS, "--preset", "standard-py312-20261127"),
        _case(
            "maps",
            "custom-template",
            USE_ROOT_MODEL_TYPE_ALIAS,
            "--custom-template-dir",
            str(DATA_PATH / "templates/root_alias_constraints"),
            changes=False,
            payloads=False,
        ),
        _case("maps", "type-alias", "--use-type-alias", changes=False),
        _case("maps", "class", changes=False),
        _case("maps", "class-schema-validators", SCHEMA_VALIDATORS, changes=False),
        _case(
            "maps",
            "dataclass",
            USE_ROOT_MODEL_TYPE_ALIAS,
            "--output-model-type",
            "dataclasses.dataclass",
            changes=False,
            payloads=False,
        ),
        _case(
            "maps",
            "typed-dict",
            USE_ROOT_MODEL_TYPE_ALIAS,
            "--output-model-type",
            "typing.TypedDict",
            changes=False,
            payloads=False,
        ),
        _case(
            "maps",
            "msgspec",
            USE_ROOT_MODEL_TYPE_ALIAS,
            "--output-model-type",
            "msgspec.Struct",
            changes=False,
            payloads=False,
        ),
        _case(
            "maps",
            "pydantic-dataclass",
            USE_ROOT_MODEL_TYPE_ALIAS,
            "--output-model-type",
            "pydantic_v2.dataclass",
            changes=False,
            payloads=False,
        ),
        _case("root", "alias", USE_ROOT_MODEL_TYPE_ALIAS),
        _case("cycle", "alias", USE_ROOT_MODEL_TYPE_ALIAS),
        _case("other_document", "alias", USE_ROOT_MODEL_TYPE_ALIAS),
        _case("request_response", "alias", USE_ROOT_MODEL_TYPE_ALIAS, *REQUEST_RESPONSE),
        _case("request_response", "schema-validators", USE_ROOT_MODEL_TYPE_ALIAS, *REQUEST_RESPONSE, SCHEMA_VALIDATORS),
        _case("openapi", "alias", USE_ROOT_MODEL_TYPE_ALIAS),
    ],
)
def test_constrained_map_roots_as_classes(
    tmp_path: Path, input_name: str, extra_args: list[str], *, changes: bool, payloads: bool
) -> None:
    """Keep constrained object map roots classes under ``--use-root-model-type-alias``, opt-in (#7619).

    Each case generates without ``--keep-constrained-map-roots-as-classes`` (``default``) and with it (``option``);
    a case the option does not change compares the two runs under one name. By default every map root is an alias that
    drops its ``minProperties``/``maxProperties`` (``Labels``, ``MinLabels``, ``ItemMap``, ``CountOnly``,
    ``AnyValues``, the document root ``Settings``, a map another document references, both request and response
    maps, an OpenAPI map, ``Tree`` whose values name it again), the pattern of its values that needs ``python-re``
    (``LookaroundValues``) and, under ``--generate-schema-validators``, the patternProperties rules and the count
    (``PatternKeys``, ``PatternKeysBeside``, ``PatternItemsRequest``), as in 0.104.0, so the invalid payloads are
    accepted. With the option each is a class, as without ``--use-root-model-type-alias``, and rejects them; the
    valid payloads parse to the same values, and ``Model().labels`` becomes a ``Labels`` unless
    ``--use-validate-default-for-root-model-type-aliases`` already validates it. A map whose root holds no such
    constraint (``PlainMap``, ``ShortValues``, ``KeyPattern``) stays an alias; ``RefLabels``, ``KeyPatternCount``
    and the maps of ``--use-single-dict-for-object-maps`` (the preset) were classes already, and a patternProperties
    or ``type: [object, null]`` map without that option counts nothing on its root, so stays an alias. A custom
    ``RootModelTypeAlias`` template, ``--use-type-alias``, root model classes and the other output model types are
    not changed.
    """
    input_path = (
        OPEN_API_DATA_PATH / "root_model_type_alias_map_roots.yaml"
        if input_name == "openapi"
        else (MAP_ROOTS_PATH / f"{input_name}.json")
    )
    outcomes = ""
    for name, option_args in (("default", []), ("option", [KEEP_CONSTRAINED_MAP_ROOTS_AS_CLASSES])):
        output_path = tmp_path / f"{name}.py"
        run_main_and_assert(
            input_path=input_path,
            output_path=output_path,
            input_file_type="openapi" if input_name == "openapi" else "jsonschema",
            extra_args=[
                *("--output-model-type", "pydantic_v2.BaseModel"),
                *extra_args,
                *option_args,
                *("--formatters", "builtin", "--disable-timestamp"),
            ],
            name=name if changes else "model",
        )
        if payloads:
            outcomes += _outcomes(output_path, input_name, name)
    if payloads:
        assert_output(outcomes, name="outcomes")
