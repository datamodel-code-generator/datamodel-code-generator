"""``--read-inherited-property-schemas-in-mro-order``: a required inherited property takes its parents' declaration.

A model that makes an inherited property required writes a field for it. When the model is written before its
parents, their classes do not exist yet and the field is built from their schemas. Up to 0.96.0 the declaration is
looked up in the members of each allOf as written, so under ``--intersect-combined-schema-keywords``, which reads an
alias member as the schema it names and the keywords beside a ``$ref`` member with it, a declaration behind such a
member is not found: the field loses its description or its constraints, or is written ``Any`` (#6849). Under the
option the declaration is read from the same members as the model's own fields, in the order of the class bases.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import msgspec
import pytest

from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "inherited_property_schemas"
OPTION = "--read-inherited-property-schemas-in-mro-order"
MSGSPEC = ("--output-model-type", "msgspec.Struct")
IN_RANGE = '{"child": {"count": 1, "name": "a"}}'
BELOW_MINIMUM = '{"child": {"count": 0, "name": "a"}}'
NO_COUNT = '{"child": {"name": "a"}}'


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Give a required inherited property the schema its allOf parents declare.

A model that makes an inherited property required (`required: [count]` beside `allOf: [{$ref: Mid}]`) writes a field
for it. When the model is written before its parents in the document, the field is built from the parents' schemas.
Without this option the declaration of the property is looked up in the members of each `allOf` as written. Under
`--intersect-combined-schema-keywords`, which reads an alias member as the schema it names and the keywords beside a
`$ref` member together with it, a declaration behind such a member is then not found: the field is written `Any`,
or keeps a type without its description or constraints, although the same model written after its parents takes the
parents' field, as in 0.96.0. With `--read-inherited-property-schemas-in-mro-order` the declaration is read from
the members the model's own fields are read from, in the order of the class bases, so a declaration the parents'
`allOf` members hold gives the same field wherever the model is written. Without `--distribute-allof-over-unions`, a
parent that names an `allOf` holding an `anyOf` or `oneOf` is no base class, and such a field is then not written. A
payload that is valid for the schema parses as before in the cases above; where the parents' field is a model of its
own (a named array or a constrained item), the field takes that model, as it does when the model is written after its
parents. The option changes nothing where every `allOf` member is read as written.""",
    input_schema="jsonschema/inherited_property_schemas/sibling.json",
    cli_args=["--intersect-combined-schema-keywords", OPTION],
    golden_output="main/jsonschema/inherited_property_schemas/required_inherited_property[sibling-on].py",
    comparison_output="main/jsonschema/inherited_property_schemas/required_inherited_property[sibling-off].py",
    related_options=["--intersect-combined-schema-keywords", "--read-allof-parents-in-mro-order"],
)
@pytest.mark.parametrize(
    ("fixture", "extra_args", "accepted", "rejected", "error"),
    [
        pytest.param("sibling.json", (), BELOW_MINIMUM, NO_COUNT, "missing", id="sibling-off"),
        pytest.param("sibling.json", (OPTION,), IN_RANGE, BELOW_MINIMUM, "greater_than_equal", id="sibling-on"),
        pytest.param("two_bases.json", (), BELOW_MINIMUM, NO_COUNT, "missing", id="two-bases-off"),
        pytest.param("two_bases.json", (OPTION,), IN_RANGE, BELOW_MINIMUM, "greater_than_equal", id="two-bases-on"),
        pytest.param("alias.json", (), IN_RANGE, BELOW_MINIMUM, "greater_than_equal", id="alias-off"),
        pytest.param("alias.json", (OPTION,), IN_RANGE, BELOW_MINIMUM, "greater_than_equal", id="alias-on"),
        pytest.param("chain.json", MSGSPEC, BELOW_MINIMUM, NO_COUNT, "missing required field", id="chain-msgspec-off"),
        pytest.param("chain.json", (*MSGSPEC, OPTION), IN_RANGE, BELOW_MINIMUM, ">= 1", id="chain-msgspec-on"),
    ],
)
def test_inherited_property_schemas_required_inherited_property(
    output_file: Path, fixture: str, extra_args: tuple[str, ...], accepted: str, rejected: str, error: str
) -> None:
    """Give a property a model only requires the field its parents declare, also when it is written before them (#6849).

    ``sibling``: the parent adds the property beside the ``$ref`` of its allOf member. ``two-bases``: the second base
    reaches the property through an alias, the first base has other parents. ``alias``: the parent inherits through an
    alias that carries a description. ``chain-msgspec``: three parents and an alias, where msgspec output loses the
    bound. Without the option the field is ``Any`` or lacks the description or the bound.

    Each case decodes one payload its model accepts and one it rejects: ``count: 0``, which the schema rejects, is
    accepted without the option where the bound is lost (``sibling``, ``two-bases``, ``chain-msgspec``) and rejected
    with it.
    """
    run_main_and_assert(
        input_path=INPUT_PATH / fixture,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--intersect-combined-schema-keywords", *extra_args],
    )
    if MSGSPEC[1] in extra_args:
        with _generated_model(output_file, "inherited_property_schemas", "Model") as model:
            msgspec.json.decode(accepted, type=model)
            with pytest.raises(msgspec.ValidationError, match=re.escape(error)):
                msgspec.json.decode(rejected, type=model)
    else:
        assert_generated_model_json_validation(
            output_file,
            module_name="inherited_property_schemas",
            model_name="Model",
            valid_json=accepted,
            invalid_json=rejected,
            expected_error_type=error,
        )
