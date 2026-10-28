"""The model graph check: ``parent`` names a holder, no type contains itself, a refresh records what a rebuild would."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.types import DataType
from tests.conftest import CHECK_MODEL_GRAPH_ENV
from tests.main.conftest import JSON_SCHEMA_DATA_PATH

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.model.base import DataModel


def _parent_is_a_sibling_field(model: DataModel) -> None:
    model.fields[1].data_type.parent = model.fields[0]


def _parent_is_another_data_type(model: DataModel) -> None:
    model.fields[0].data_type.parent = DataType()


def _contains_itself(model: DataModel) -> None:
    data_type = model.fields[0].data_type
    data_type.data_types.append(data_type)


@pytest.mark.parametrize(
    ("corrupt", "message"),
    [
        (_parent_is_a_sibling_field, "a field data type of Person has a recorded field as its parent, which does not"),
        (_parent_is_another_data_type, "a field data type of Person has a data type as its parent, which does not"),
        (_contains_itself, "a data type of Person contains itself"),
    ],
)
def test_model_graph_check_reports_holder_violations(
    monkeypatch: pytest.MonkeyPatch, corrupt: Callable[[DataModel], None], message: str
) -> None:
    """A custom parser that changes a model's types outside the store and reports the model breaks an invariant.

    The check (`1`, as the test suite sets it) raises at the refresh that records the model again, before that
    refresh walks a type that contains itself.
    """

    class CorruptingParser(JsonSchemaParser):
        def parse_raw(self) -> None:
            super().parse_raw()
            model = next(model for model in self.results if model.class_name == "Person")
            corrupt(model)
            self.generation_store.note_changed_models(model)

    monkeypatch.setenv(CHECK_MODEL_GRAPH_ENV, "1")
    parser = CorruptingParser(JSON_SCHEMA_DATA_PATH / "person.json", formatters=[Formatter.BUILTIN])
    with pytest.raises(Error, match=rf"^{CHECK_MODEL_GRAPH_ENV}: {message}"):
        parser.parse()


def test_model_graph_check_reports_a_change_no_operation_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refresh records again only the models store operations noted, so a type changed without a note is reported.

    The custom parser retypes a field of one model after the facts are built and then changes another model through the
    store; the refresh that follows records the second model again and keeps the first as it was, which a full rebuild
    would not.
    """

    class UnnotingParser(JsonSchemaParser):
        def parse_raw(self) -> None:
            super().parse_raw()
            store = self.generation_store
            store.current_facts()
            unnoted, noted = self.results[:2]
            unnoted.fields[0].data_type = DataType(type="int")
            store.set_fields(noted, list(noted.fields))

    monkeypatch.setenv(CHECK_MODEL_GRAPH_ENV, "1")
    parser = UnnotingParser(JSON_SCHEMA_DATA_PATH / "nested_person.json", formatters=[Formatter.BUILTIN])
    with pytest.raises(
        Error,
        match=rf"^{CHECK_MODEL_GRAPH_ENV}: the models that a refresh recorded for the noted models differ from a full "
        "rebuild$",
    ):
        parser.parse()


def test_model_graph_check_reports_a_cycle_in_a_model_no_operation_noted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A type that contains itself is reported in a model no operation noted, before a rebuild would walk it.

    The custom parser makes a field type of one model contain itself after the facts are built and then changes another
    model through the store; the comparison with a full rebuild that follows would not end on the cycle.
    """

    class UnnotingParser(JsonSchemaParser):
        def parse_raw(self) -> None:
            super().parse_raw()
            store = self.generation_store
            store.current_facts()
            unnoted, noted = self.results[:2]
            _contains_itself(unnoted)
            store.set_fields(noted, list(noted.fields))

    monkeypatch.setenv(CHECK_MODEL_GRAPH_ENV, "1")
    parser = UnnotingParser(JSON_SCHEMA_DATA_PATH / "nested_person.json", formatters=[Formatter.BUILTIN])
    with pytest.raises(Error, match=rf"^{CHECK_MODEL_GRAPH_ENV}: a data type of \w+ contains itself$"):
        parser.parse()
