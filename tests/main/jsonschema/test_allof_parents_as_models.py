"""``--read-allof-parents-as-their-models``: a model reads each allOf parent as the parent's model holds it.

A parent written as a ``$ref`` beside sibling keywords is one model built from the merge of both, but up to 0.100.0 a
model inheriting it reads its sibling keywords alone: written before that parent, it types a property it only
requires ``Any``, a property it narrows ``Any``, and leaves optional a property the ``$ref`` target requires (#6868).
A property that one parent declares optional and another requires stays as the class takes it from its bases, so
``allOf: [PetBase, Named]`` accepts a payload without the name ``Named`` requires (#7331). An allOf of a ``$ref``
alias of an object model and a member that adds only a type or required names is a root model of a dict, without the
alias target's fields, also where ``--intersect-combined-schema-keywords`` follows alias members (#7309).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import msgspec
import pytest

from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OPTION = "--read-allof-parents-as-their-models"
COMBINED = "--intersect-combined-schema-keywords"
JSON_SCHEMA_PATH = JSON_SCHEMA_DATA_PATH / "allof_parents_as_models"
OPEN_API_PATH = OPEN_API_DATA_PATH / "allof_parents_as_models"
REF_SIBLING_VALID = (
    '{"required_name": {"name": "abc"}, "narrowed": {"name": "abc"}, "target_requires": {"name": "abc"}, '
    '"chain": {"name": "abc", "more": 1}, "inline": {"name": "abc"}, "target_parents": {"level": 1}}'
)
LEVEL_NOT_AN_INTEGER = '{"target_parents": {"level": "x"}}'
NAME_NOT_A_STRING = '{"required_name": {"name": 5}}'
TARGET_NAME_MISSING = '{"target_requires": {"extra": "x"}}'
EXTRA_NOT_A_STRING = '{"narrowed": {"extra": 5}}'


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Read each allOf parent as its model holds it.

A parent written as a `$ref` beside sibling keywords (`Parent: {$ref: Base, properties: {extra: ...}}`) is one model
built from the merge of both. Without this option a model that names it in an `allOf` reads only its sibling keywords
when it is written before that parent: a property it only requires is typed `Any`, a property it narrows is `Any`, and a
property the `$ref` target requires stays optional, as in 0.100.0. With `--read-allof-parents-as-their-models` the
parent is read as its merge, so the model is the same wherever it is written. A property one parent declares optional
and another requires (`allOf: [PetBase, Named]` with only `Named` requiring `name`) is written again in the model as
required, as if the model listed it in `required`; without the option the class takes it from its bases, optional
where the first base Pydantic or dataclasses read declares it optional, and a TypedDict takes the last base. Under
`--intersect-combined-schema-keywords`, which reads an alias member as the schema it names, an allOf of an alias of an
object model and a member that adds only a type or required names keeps the fields of that schema too, instead of
becoming a root model of a dict. A field whose allOf names one model and adds only `required` names (`inline` in the
example) stays that model, which does not require them, with or without this option; that site is decided by
`--intersect-combined-schema-keywords`, which writes a model of the field that requires them. Apart from such a field,
a payload the schema rejects for a required or narrowed inherited property is rejected. A payload valid for the schema
parses to the same values, but where a union of variants takes its discriminator only because a variant now requires
its tag: a variant that leaves the optional tag out is then read without the tag value it took as a default before.""",
    input_schema="jsonschema/allof_parents_as_models/ref_sibling_parents.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/allof_parents_as_models/ref_sibling_parents[on].py",
    comparison_output="main/jsonschema/allof_parents_as_models/ref_sibling_parents[off].py",
    related_options=[COMBINED, "--read-inherited-property-schemas-in-mro-order", "--read-allof-parents-in-mro-order"],
)
@pytest.mark.parametrize(
    ("extra_args", "accepted", "rejected", "error"),
    [
        pytest.param((), NAME_NOT_A_STRING, EXTRA_NOT_A_STRING, "string_type", id="off"),
        pytest.param((OPTION,), REF_SIBLING_VALID, NAME_NOT_A_STRING, "string_type", id="on"),
        pytest.param((OPTION,), REF_SIBLING_VALID, TARGET_NAME_MISSING, "missing", id="on-target-requires"),
        pytest.param((OPTION,), REF_SIBLING_VALID, LEVEL_NOT_AN_INTEGER, "int_parsing", id="on-target-parents"),
        pytest.param((COMBINED, OPTION), REF_SIBLING_VALID, NAME_NOT_A_STRING, "string_type", id="on-combined"),
    ],
)
def test_allof_parents_as_models_ref_sibling_parents(
    output_file: Path, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Read a ``$ref``-with-siblings parent with the schema its ``$ref`` names, written after its children (#6868).

    ``RequiredName`` only requires ``name``, which ``Base`` declares; ``Narrowed`` adds ``minLength`` to it;
    ``TargetRequires`` declares it again where ``RequiringBase`` requires it; ``Chain`` inherits through a ``$ref``
    with siblings whose target is one too; ``inline`` is an allOf at a property; ``TargetParents`` requires ``level``,
    which the target of its ``$ref`` parent inherits from ``Grand``. Without the option ``name`` is ``Any`` in the first
    two and ``Chain``, optional in ``TargetRequires``, and ``level`` is ``Any``; with it each takes the parents' type,
    the bound and the requirement, also under ``--intersect-combined-schema-keywords``. ``inline`` adds only
    ``required`` to ``Parent``, so it is ``Parent`` unless ``--intersect-combined-schema-keywords`` writes ``Inline``,
    which with the option requires ``name: str``.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "ref_sibling_parents.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_parents_as_models",
        model_name="Model",
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type=error,
    )


@pytest.mark.parametrize(
    ("fixture", "extra_args", "decode"),
    [
        pytest.param("ref_sibling_parents.json", ("--output-model-type", "msgspec.Struct"), True, id="msgspec"),
        pytest.param(
            "ref_sibling_parents.json", ("--output-model-type", "dataclasses.dataclass"), False, id="dataclass"
        ),
        pytest.param("ref_sibling_parents_draft07.json", (), False, id="draft07"),
        pytest.param(
            "ref_sibling_parents_draft07.json",
            ("--ignore-ref-siblings-by-schema-version",),
            False,
            id="draft07-ignored",
        ),
    ],
)
@pytest.mark.parametrize("option", [pytest.param((), id="off"), pytest.param((OPTION,), id="on")])
def test_allof_parents_as_models_ref_sibling_outputs(
    output_file: Path, option: tuple[str, ...], fixture: str, extra_args: tuple[str, ...], decode: bool
) -> None:
    """Read a ``$ref``-with-siblings parent as its model in every output and schema version (#6868).

    msgspec rejects a ``TargetRequires`` without ``name`` with the option. A draft-07 parent merges its siblings as a
    2020-12 one does; where ``--ignore-ref-siblings-by-schema-version`` drops them, the parent is an alias of ``Base``
    and the option reads it as its model reads it, so nothing moves.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / fixture,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args, *option],
    )
    if not decode:
        return
    with _generated_model(output_file, "allof_parents_as_models", "Model") as model:
        msgspec.json.decode(REF_SIBLING_VALID, type=model)
        if option:
            with pytest.raises(msgspec.ValidationError, match=re.escape("missing required field `name`")):
                msgspec.json.decode(TARGET_NAME_MISSING, type=model)
        else:
            msgspec.json.decode(TARGET_NAME_MISSING, type=model)


@pytest.mark.parametrize("option", [pytest.param((), id="off"), pytest.param((OPTION,), id="on")])
def test_allof_parents_as_models_ref_sibling_other_document(output_file: Path, option: tuple[str, ...]) -> None:
    """Read a ``$ref``-with-siblings parent of another document in that document (#6868).

    ``parents.json`` holds ``Parent``, a ``$ref`` to its own ``Base`` beside ``extra``; ``Base`` requires ``name``.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "other_document" / "model.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *option],
    )


@pytest.mark.parametrize(
    ("extra_args", "accepted", "rejected", "error"),
    [
        pytest.param((), "{}", '{"name": 5}', "string_type", id="off"),
        pytest.param((OPTION,), '{"name": "a"}', "{}", "missing", id="on"),
        pytest.param(("--force-optional", OPTION), "{}", '{"name": 5}', "string_type", id="on-force-optional"),
    ],
)
def test_allof_parents_as_models_required_names(
    output_file: Path, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Keep a property required that one allOf parent requires and another declares optional (#7331).

    ``NewPet`` and ``NewPetFirst`` (written before its parents) inherit ``name`` from ``PetBase``, optional, and from
    ``Named``, which requires it; ``NamedFirst`` lists ``Named`` first; ``Owner.pet`` is an inline allOf. With the
    option each writes ``name`` again as required, as if it listed it in ``required``, and rejects a payload without
    it.
    """
    run_main_and_assert(
        input_path=OPEN_API_PATH / "required_names.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_parents_as_models",
        model_name="NewPet",
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type=error,
    )


@pytest.mark.parametrize(
    "output_model_type",
    [
        pytest.param("typing.TypedDict", id="typed-dict"),
        pytest.param("dataclasses.dataclass", id="dataclass"),
        pytest.param("msgspec.Struct", id="msgspec"),
    ],
)
@pytest.mark.parametrize("option", [pytest.param((), id="off"), pytest.param((OPTION,), id="on")])
def test_allof_parents_as_models_required_names_outputs(
    output_file: Path, option: tuple[str, ...], output_model_type: str
) -> None:
    """Keep a property some allOf parent requires required in TypedDict, dataclasses and msgspec output (#7331).

    A TypedDict takes a key from its last base, so ``NamedFirst`` loses the requirement there without the option; with
    it each model writes ``name`` again (a TypedDict then declares every key itself). msgspec writes the bases' fields
    into a Struct whose bases conflict and requires a name any of them requires, so its output for this document does
    not move.
    """
    run_main_and_assert(
        input_path=OPEN_API_PATH / "required_names.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, *option],
    )
    if output_model_type == "msgspec.Struct":
        with (
            _generated_model(output_file, "allof_parents_as_models", "NewPet") as model,
            pytest.raises(msgspec.ValidationError, match=re.escape("missing required field `name`")),
        ):
            msgspec.json.decode("{}", type=model)


@pytest.mark.parametrize(
    ("extra_args", "accepted", "rejected", "error"),
    [
        pytest.param((COMBINED,), '{"child": {"p": 5}, "short": "abcd"}', '{"child": 5}', "dict_type", id="off"),
        pytest.param(
            (COMBINED, OPTION), '{"child": {"p": "a", "q": true}}', '{"child": {"q": true}}', "missing", id="on"
        ),
        pytest.param((COMBINED, OPTION), '{"short": "abc"}', '{"short": "abcd"}', "string_too_long", id="on-short"),
        pytest.param(
            (OPTION,), '{"child": {"p": 5}, "short": "abcd"}', '{"child": 5}', "dict_type", id="on-as-written"
        ),
    ],
)
def test_allof_parents_as_models_alias_parent(
    output_file: Path, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Keep the fields of the schema an alias parent names beside a member that adds a type and required names (#7309).

    ``Child`` is an allOf of ``Parent``, an alias of the object ``Base``, and ``{type: object, required: [p]}``. Under
    ``--intersect-combined-schema-keywords``, which follows alias members, it is a dict root model without the option
    and ``Child(Base)`` requiring ``p`` with it. Without that option the alias is read as written, as before.
    ``Short``, an allOf of an alias of a string and ``maxLength``, is a root model of ``Any`` without the option and
    of the bounded string with it, as the alias member names the string.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "alias_parent.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_parents_as_models",
        model_name="Model",
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type=error,
    )


@pytest.mark.parametrize(
    ("extra_args", "accepted", "rejected", "error"),
    [
        pytest.param((), '{"thing": {"b": "x"}}', '{"thing": {"kind": "x"}}', "literal_error", id="off"),
        pytest.param((OPTION,), '{"thing": {"kind": "d"}}', '{"thing": {"b": "x"}}', "union_tag_not_found", id="on"),
    ],
)
def test_allof_parents_as_models_diamond_variant(
    output_file: Path, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Require the tag of a diamond variant whose second base requires it, so its union takes the discriminator (#7331).

    ``D`` is an allOf of ``B`` and ``C``, both below ``E``, which declares ``kind`` optional; only ``C`` requires it.
    Without the option ``D`` takes ``B``'s optional ``kind`` and the union accepts a variant payload without it; with
    it ``D`` requires ``kind`` and the payload is rejected.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH
        / "discriminator_nested_variant_diamond"
        / "optional_tag_from_the_first_base.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_parents_as_models",
        model_name="Holder",
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type=error,
    )
