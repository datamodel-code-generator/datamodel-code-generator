"""``--distribute-allof-over-object-branches``: an allOf over a union gives a complete variant per object branch.

An allOf whose member is an ``anyOf`` or ``oneOf`` gives one union variant per branch: the branch's class with the
class of the other members. Up to 0.100.0 a variant takes a name another member requires as its bases hold it,
optional where its branch declares it optional and ``Any`` where only the other members' class holds it (#6867), and
a branch that is itself a union, named by a ``$ref`` or written there, gives a variant without its fields or none at
all (#6869).
"""

from __future__ import annotations

import contextlib
import re
import warnings
from typing import TYPE_CHECKING

import msgspec
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OPTION = "--distribute-allof-over-object-branches"
DISTRIBUTE = "--distribute-allof-over-unions"
JSON_SCHEMA_PATH = JSON_SCHEMA_DATA_PATH / "allof_union_object_branches"
OPEN_API_PATH = OPEN_API_DATA_PATH / "allof_union_object_branches"
NAMED_VALID = (
    '{"named": {"meow": true, "name": "a"}, "beside_first": {"bark": true, "name": "a"}, '
    '"beside_last": {"meow": true, "name": "a"}, "vip": {"email": "a@example.com", "id": 1, "level": 1}, '
    '"owned": {"tag": "a", "owner": "b"}, "tagged": {"label": "a", "name": "b", "meow": true}}'
)
NAME_MISSING = '{"named": {"meow": true}}'
NAME_NOT_A_STRING = '{"beside_first": {"bark": true, "name": 5}}'
NAME_TOO_LONG = '{"beside_first": {"bark": true, "name": "abcdefghi"}}'
ID_MISSING = '{"vip": {"email": "a@example.com", "level": 1}}'
OWNER_MISSING = '{"owned": {"tag": "a"}}'
SITE_NAME_MISSING = '{"tagged": {"label": "a", "meow": true}}'
BRANCHES_VALID = (
    '{"owned": {"tiny": true, "owner": "a"}, "inline": {"small": true, "owner": "a"}, '
    '"keywords": {"name": "a", "wild": true, "owner": "a"}, "aliased": {"meow": true, "owner": "a"}, '
    '"tamed": {"small": true, "tamer": "a", "owner": "a"}, "layered": {"tiny": true, "owner": "a"}, '
    '"labelled": {"label": "a", "tiny": true, "owner": "a"}, "tagged": {"meow": true, "tag": "a", "owner": "a"}, '
    '"badged": {"badge": "a", "small": true, "owner": "a"}, "coached": {"small": true, "coach": "a", "owner": "a"}, '
    '"lion": {"mane": true, "tag": "a", "owner": "a"}}'
)
NO_PET = '{"owned": {"owner": "a"}}'
INLINE_NO_PET = '{"inline": {"owner": "a"}}'
NO_CAT_FIELDS = '{"aliased": {"owner": "a"}}'
NO_TAMER = '{"tamed": {"small": true, "owner": "a"}}'
NO_LABEL = '{"labelled": {"tiny": true, "owner": "a"}}'
NO_TAG = '{"tagged": {"meow": true, "owner": "a"}}'
BADGE_NO_PET = '{"badged": {"badge": "a", "owner": "a"}}'
COACH_NO_PET = '{"coached": {"coach": "a", "owner": "a"}}'
LION_NO_TAG = '{"lion": {"mane": true, "owner": "a"}}'
LION_NO_PET = '{"lion": {"tag": "a", "owner": "a"}}'


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Give an allOf over a union a complete variant per object branch.

An `allOf` whose member is an `anyOf` or `oneOf` (`NamedPet: {allOf: [Pet, {required: [name]}]}` with `Pet` a `oneOf`
of `Cat` and `Dog`) gives one union variant per branch, the branch's class with the class of the other members.
Without this option a variant takes a name another member requires as its bases hold it: optional where its branch
declares it optional, `Any` where only the class of the other members holds it, and for a name required beside the
`allOf` the field of whichever base was generated first, so a payload without the name, or with a value its branch
rejects, is accepted, as in 0.100.0. A branch that is itself a union, named by a `$ref` (`Dog: {oneOf: [BigDog,
SmallDog]}`) or written there, gives a variant without any field of that union, which accepts payloads no branch
does, or no variant at all, and an alias branch gives a variant without the fields of the schema it names. With
`--distribute-allof-over-object-branches` each variant requires every name a part of the `allOf` requires, typed as
the field it takes from its branch, and a branch that is a union, or an allOf or a `$ref` with sibling keywords over
one, gives one variant per object branch of it, as when the union lists those branches itself. A branch whose union
leads back to the allOf (a parent's `oneOf` of children that are an `allOf` of that parent) stays as it is. Without the
option such a branch warns.""",
    input_schema="jsonschema/allof_union_object_branches/required_names.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/allof_union_object_branches/required_names_in_union_variants[on].py",
    comparison_output="main/jsonschema/allof_union_object_branches/required_names_in_union_variants[off].py",
    related_options=[DISTRIBUTE, "--read-allof-parents-as-their-models"],
)
@pytest.mark.parametrize(
    ("extra_args", "accepted", "rejected", "error"),
    [
        pytest.param((), NAME_MISSING, '{"named": {"meow": 5}}', "bool_parsing", id="off"),
        pytest.param((OPTION,), NAMED_VALID, NAME_MISSING, "missing", id="on"),
        pytest.param((OPTION,), NAMED_VALID, NAME_NOT_A_STRING, "string_type", id="on-beside"),
        pytest.param((OPTION,), NAMED_VALID, NAME_TOO_LONG, "missing", id="on-branch-bound"),
        pytest.param((OPTION,), NAMED_VALID, ID_MISSING, "missing", id="on-parent-declares"),
        pytest.param((OPTION,), NAMED_VALID, OWNER_MISSING, "missing", id="on-branch-requires"),
        pytest.param((OPTION,), NAMED_VALID, SITE_NAME_MISSING, "missing", id="on-site-parts"),
        pytest.param(
            ("--force-optional", OPTION),
            NAME_MISSING,
            '{"named": 5}',
            "model_type",
            id="on-force-optional",
        ),
    ],
)
def test_required_names_in_union_variants(
    output_file: Path, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Have each variant of an allOf over a union require what any part requires, typed from its branch (#6867).

    ``NamedPet`` requires ``name`` in a member, ``BesideFirst`` and ``BesideLast`` beside the allOf, written before and
    after ``Pet``; ``Cat`` and ``Dog`` declare ``name`` optional, ``Dog`` with ``maxLength``. ``VipContact`` requires
    ``id``, which only the branches of ``Contact``'s anyOf declare; a branch of ``Tagged`` requires ``owner``, which
    only the other member of ``OwnedPet`` declares; ``TaggedPet`` requires ``label`` beside its oneOf of ``$ref``
    branches, whose classes take it. Without the option the variants hold ``name`` optional, or ``Any``
    in ``BesideFirst``, and ``id`` and ``owner`` optional; with it each variant writes them required, typed and bounded
    as its branch declares them, whatever the order of the definitions.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "required_names.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_union_object_branches",
        model_name="Model",
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type=error,
    )


@pytest.mark.parametrize(
    "output_model_type",
    [
        pytest.param("dataclasses.dataclass", id="dataclass"),
        pytest.param("pydantic_v2.dataclass", id="pydantic-dataclass"),
        pytest.param("typing.TypedDict", id="typed-dict"),
        pytest.param("msgspec.Struct", id="msgspec"),
    ],
)
def test_required_names_in_union_variant_outputs(output_file: Path, output_model_type: str) -> None:
    """Write the names a part requires into each variant in every output, which takes a field from either base (#6867).

    msgspec warns that it cannot decode the untagged unions, with or without the option; its ``NamedPet2`` Struct
    rejects a payload without ``name``.
    """
    with (
        pytest.warns(UserWarning, match="msgspec cannot decode the union|numbered collisions")
        if output_model_type == "msgspec.Struct"
        else contextlib.nullcontext()
    ):
        run_main_and_assert(
            input_path=JSON_SCHEMA_PATH / "required_names.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--formatters", "builtin", "--output-model-type", output_model_type, OPTION],
        )
    if output_model_type != "msgspec.Struct":
        return
    with _generated_model(output_file, "allof_union_object_branches", "NamedPet2") as model:
        msgspec.json.decode('{"meow": true, "name": "a"}', type=model)
        with pytest.raises(msgspec.ValidationError, match=re.escape("missing required field `name`")):
            msgspec.json.decode('{"meow": true}', type=model)


@pytest.mark.parametrize(
    ("extra_args", "accepted", "rejected", "error"),
    [
        pytest.param((), NO_PET, '{"owned": 5}', "model_type", id="off"),
        pytest.param((OPTION,), BRANCHES_VALID, NO_PET, "missing", id="on"),
        pytest.param((OPTION,), BRANCHES_VALID, INLINE_NO_PET, "missing", id="on-inline"),
        pytest.param((OPTION,), BRANCHES_VALID, NO_CAT_FIELDS, "missing", id="on-alias"),
        pytest.param((OPTION,), BRANCHES_VALID, NO_TAMER, "missing", id="on-ref-siblings"),
        pytest.param((OPTION, DISTRIBUTE), BRANCHES_VALID, NO_PET, "missing", id="on-distribute"),
        pytest.param((OPTION,), BRANCHES_VALID, NO_LABEL, "missing", id="on-site-parts"),
        pytest.param((OPTION, DISTRIBUTE), BRANCHES_VALID, NO_LABEL, "missing", id="on-site-parts-distribute"),
        pytest.param((OPTION,), BRANCHES_VALID, NO_TAG, "missing", id="on-alias-siblings"),
        pytest.param((OPTION,), BRANCHES_VALID, BADGE_NO_PET, "missing", id="on-site-parts-all-of"),
        pytest.param(
            (OPTION, DISTRIBUTE), BRANCHES_VALID, BADGE_NO_PET, "missing", id="on-site-parts-all-of-distribute"
        ),
        pytest.param((OPTION,), BRANCHES_VALID, COACH_NO_PET, "missing", id="on-inline-all-of"),
        pytest.param((OPTION, DISTRIBUTE), BRANCHES_VALID, COACH_NO_PET, "missing", id="on-inline-all-of-distribute"),
        pytest.param((OPTION,), BRANCHES_VALID, LION_NO_TAG, "missing", id="on-cycle-siblings"),
        pytest.param((OPTION,), BRANCHES_VALID, LION_NO_PET, "missing", id="on-cycle-siblings-no-pet"),
        pytest.param((OPTION, DISTRIBUTE), BRANCHES_VALID, LION_NO_PET, "missing", id="on-cycle-siblings-distribute"),
        pytest.param(
            (OPTION, "--name-allof-union-members-after-branches"),
            BRANCHES_VALID,
            NO_PET,
            "missing",
            id="on-named-after-branches",
        ),
    ],
)
def test_union_branch_of_union(
    output_file: Path, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Give an allOf over a union a variant per object branch where a branch is itself a union (#6869).

    ``Pet``'s branch ``Dog`` is an anyOf of ``BigDog`` and ``Puppy``, a oneOf of two more; ``InlinePet`` writes a oneOf
    in its union; ``KeywordPet``'s branch declares ``name`` beside its anyOf of a ``$ref`` and an inline object;
    ``AliasedPet``'s branch is an alias of ``Cat``; ``TamedDog`` is a ``$ref`` to ``Puppy`` beside ``tamer``;
    ``TrainedDog`` an allOf of ``Puppy`` and ``trainer``; ``Labelled`` requires ``label`` beside its oneOf of ``Cat``
    and ``Puppy``; ``Looping`` names itself. ``TaggedPet``'s branch is ``CatAlias`` beside a required ``tag``,
    ``BadgedPet`` requires ``badge`` beside its oneOf of ``Cat`` and ``TrainedDog``, and ``CoachedPet``'s inline oneOf
    has an allOf requiring ``coach`` beside it; ``TaggedLion``'s branch is ``Lion`` beside a required ``tag``, an allOf
    of ``Animal``, whose oneOf lists ``Lion``. Without the option those branches give a variant of the other member
    alone, so a payload that is no pet is accepted, and the inline oneOf gives no variant; with it each object branch
    has a variant, those of ``TaggedPet`` and ``TaggedLion`` keep ``tag``, and ``Looping``'s own branch stays as it is.
    """
    with (
        contextlib.nullcontext()
        if OPTION in extra_args
        else pytest.warns(UserWarning, match="--distribute-allof-over-object-branches gives it|numbered collisions")
    ):
        run_main_and_assert(
            input_path=JSON_SCHEMA_PATH / "union_branches.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--formatters", "builtin", *extra_args],
        )
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_union_object_branches",
        model_name="Model",
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type=error,
    )


@pytest.mark.parametrize(
    ("input_path", "input_file_type", "model_name", "accepted", "rejected"),
    [
        pytest.param(
            JSON_SCHEMA_PATH / "other_document" / "model.json",
            "jsonschema",
            "OwnedPet",
            '{"small": true, "owner": "a"}',
            '{"owner": "a"}',
            id="other-document",
        ),
        pytest.param(
            OPEN_API_PATH / "discriminated.yaml",
            "openapi",
            "OwnedPet",
            '{"kind": "dog", "small": true, "owner": "a", "name": "b"}',
            '{"kind": "dog", "small": true, "owner": "a"}',
            id="openapi-discriminated",
        ),
    ],
)
@pytest.mark.parametrize("option", [pytest.param((), id="off"), pytest.param((OPTION,), id="on")])
def test_union_branch_of_union_sources(
    output_file: Path,
    option: tuple[str, ...],
    input_path: Path,
    input_file_type: str,
    model_name: str,
    accepted: str,
    rejected: str,
) -> None:
    """Read a branch that is a union in another document, and in an OpenAPI union with a discriminator (#6867, #6869).

    ``pets.json`` holds ``Dog``, a oneOf of its own ``BigDog`` and ``SmallDog``. In ``discriminated.yaml`` ``OwnedPet``
    also requires ``name``, which every branch declares optional. Without the option the dog variant is the other
    member alone and accepts a payload without ``name``.
    """
    with (
        contextlib.nullcontext()
        if option
        else pytest.warns(UserWarning, match="--distribute-allof-over-object-branches gives it")
    ):
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=input_file_type,
            extra_args=["--formatters", "builtin", *option],
        )
    if not option:
        return
    assert_generated_model_json_validation(
        output_file,
        module_name="allof_union_object_branches",
        model_name=model_name,
        valid_json=accepted,
        invalid_json=rejected,
        expected_error_type="missing",
    )


@pytest.mark.parametrize(
    ("input_path", "input_file_type", "extra_args"),
    [
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "allof_union_through_ref_files" / "leaf.json",
            "jsonschema",
            (),
            id="through-ref-member",
        ),
        pytest.param(JSON_SCHEMA_PATH / "other_document" / "model.json", "jsonschema", (), id="nested-branch"),
        pytest.param(OPEN_API_PATH / "polymorphism.yaml", "openapi", (), id="polymorphism"),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "allof_union_through_ref_files" / "leaf.json",
            "jsonschema",
            (DISTRIBUTE, OPTION),
            id="with-options",
        ),
    ],
)
def test_union_shape_warnings(
    output_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_path: Path,
    input_file_type: str,
    extra_args: tuple[str, ...],
) -> None:
    """Warn where a kept row drops what a union of an allOf checks, naming the option that keeps it (#5305, #6869).

    ``leaf.json`` names an allOf over a union, which only --distribute-allof-over-unions reads in place, and
    ``model.json`` a union branch that is a union itself. The usual polymorphism of OpenAPI, a parent whose oneOf lists
    children that are an allOf of that parent, is left as it is with every option, so it does not warn, and neither
    does ``leaf.json`` with both options.
    """
    monkeypatch.delenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", raising=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=input_file_type,
            extra_args=["--formatters", "builtin", *extra_args],
        )
    assert_output(
        "".join(sorted({f"{item.message}\n" for item in caught if "--distribute-allof-over-" in str(item.message)})),
        name="warnings",
    )
