"""The write guards that ``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` installs, seen through custom parsers.

After L1, the graph classes carry a ``__setattr__`` guard and the list attributes of the parsed graph are guarded lists,
so that the finality check sees a write where it is made. That writes happen only inside store operations is heki's
check (``tests/test_build_heki_contracts_script.py``).
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.types import DataType
from tests.conftest import CHECK_FACT_FINALITY_ENV, assert_output

if TYPE_CHECKING:
    from datamodel_code_generator.parser.base import ModuleContext

SCHEMA = Path(__file__).parents[1] / "data" / "jsonschema" / "person.json"
REPORTED = ("pass_name", "rule", "row", "model", "phase")
GUARDED_CLASSES = (DataType, DataModelFieldBase, DataModel)


def _installed() -> dict[str, bool]:
    return {cls.__name__: "__setattr__" in vars(cls) for cls in GUARDED_CLASSES}


class DescribingParser(JsonSchemaParser):
    """Describes every model directly, outside every pass, when its module is processed."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in models:
            model.description = "described"
        return super()._process_single_module(module_, models, *args)


class CopyingParser(JsonSchemaParser):
    """Sets the fields of every model through the store, then deep-copies them and reorders the copy directly."""

    copy_types: list[type[list[Any]]]

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in models:
            self.generation_store.set_fields(model, list(model.fields))
            copied = deepcopy(model.fields)
            copied.reverse()
            self.copy_types.append(type(copied))
        return super()._process_single_module(module_, models, *args)


class InstallRecordingParser(JsonSchemaParser):
    """Records which graph classes carry their own ``__setattr__`` while a module is processed."""

    installed: dict[str, bool]

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        self.installed = _installed()
        return super()._process_single_module(module_, models, *args)


class NestingParser(DescribingParser):
    """Runs a whole describing parse of the same schema, records the guards after it, then describes its own models."""

    installed: dict[str, bool]

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        DescribingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()
        self.installed = _installed()
        return super()._process_single_module(module_, models, *args)


class SharingParser(JsonSchemaParser):
    """Gives the data types of two fields one list of members at the end of L1 and records whether it stays shared."""

    shared: list[bool]

    def _first_fields(self) -> tuple[DataModelFieldBase, DataModelFieldBase]:
        first, second, *_ = next(model for model in self.results if len(model.fields) > 1).fields
        return first, second

    def parse_raw(self) -> None:
        """Parse, then share the member list of the first field's data type with the second field's."""
        super().parse_raw()
        first, second = self._first_fields()
        second.data_type.data_types = first.data_type.data_types
        self.shared = []

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        first, second = self._first_fields()
        self.shared.append(second.data_type.data_types is first.data_type.data_types)
        return super()._process_single_module(module_, models, *args)


def _reported(report: Path) -> str:
    """Return the reported violations without what depends on the checkout."""
    lines = report.read_text(encoding="utf-8").splitlines() if report.exists() else []
    return "".join(json.dumps({key: violation[key] for key in REPORTED}) + "\n" for violation in map(json.loads, lines))


def test_finality_guards_leave_deep_copies_unguarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deep copy of a list that a store operation set is a plain list, and writing it changes nothing checked."""
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "1")
    parser = CopyingParser(SCHEMA, formatters=[Formatter.BUILTIN])
    parser.copy_types = []

    parser.parse()

    assert_output("".join(f"{copy_type.__name__}\n" for copy_type in parser.copy_types))


@pytest.mark.parametrize("flag", ["finality", "off"])
def test_finality_guards_are_installed_only_while_a_parse_is_armed(monkeypatch: pytest.MonkeyPatch, flag: str) -> None:
    """With the finality check on, the graph classes are guarded while a parse runs, and given back."""
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "1" if flag == "finality" else "0")
    parser = InstallRecordingParser(SCHEMA, formatters=[Formatter.BUILTIN])

    parser.parse()

    assert_output(json.dumps({"during": parser.installed, "after": _installed()}, indent=2) + "\n")


def test_finality_guards_of_a_nested_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A parse run inside a pass keeps the guards of the parse around it: both parses report their writes."""
    report = tmp_path / "violations.jsonl"
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, f"report:{report}")
    parser = NestingParser(SCHEMA, formatters=[Formatter.BUILTIN])

    parser.parse()

    assert_output(f"guards after the nested parse: {json.dumps(parser.installed)}\n{_reported(report)}")


def test_finality_guards_keep_what_the_parsed_graph_shares(monkeypatch: pytest.MonkeyPatch) -> None:
    """Objects that several holders share at the end of L1 are guarded once, and a shared list stays one list."""
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "1")
    parser = SharingParser(SCHEMA.with_name("combined_array.json"), formatters=[Formatter.BUILTIN])

    output = parser.parse()

    assert_output(output, name="output")
    assert_output(json.dumps({"shared while processing modules": parser.shared}) + "\n", name="shared")
