"""The runtime write guard that ``DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES`` turns on, seen through custom parsers.

After L1, a custom parser that changes a model, a field or a data type directly, outside a ``GenerationStore``
operation, is reported with the line that wrote it, the parser method it ran in and the phase. The finality check,
which would report the same writes as made outside every pass, is off in these tests.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import Error
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.types import DataType
from tests.conftest import CHECK_FACT_FINALITY_ENV, CHECK_STORE_WRITES_ENV, assert_output

if TYPE_CHECKING:
    from datamodel_code_generator.parser.base import ModuleContext

SCHEMA = Path(__file__).parents[1] / "data" / "jsonschema" / "person.json"
REPORTED = ("owner", "attribute", "operation", "key", "scope", "phase")
GUARDED_CLASSES = (DataType, DataModelFieldBase, DataModel)


class DescribingParser(JsonSchemaParser):
    """Describes every model directly when its module is processed."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in models:
            model.description = "described"
        return super()._process_single_module(module_, models, *args)


class ReorderingParser(JsonSchemaParser):
    """Reorders the fields of every model, through the store and directly, when its module is processed."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in models:
            self.generation_store.rename_model(model, class_name=f"{model.class_name}Renamed")
            self.generation_store.set_fields(model, model.fields[::-1])
            model.fields.reverse()
            model.fields[:] = model.fields[::-1]
            model.fields[0] = model.fields[0]
            model.fields.insert(0, model.fields.pop())
            model.fields.sort(key=lambda field: field.name or "", reverse=True)
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
        self.installed = {cls.__name__: "__setattr__" in vars(cls) for cls in GUARDED_CLASSES}
        return super()._process_single_module(module_, models, *args)


class NestingParser(JsonSchemaParser):
    """Runs a whole describing parse of the same schema, then sets the keyword-only flag of its own models directly."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        DescribingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()
        for model in models:
            model.keyword_only = model.keyword_only
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
    """Return the reported writes without what depends on the checkout and the Python version."""
    lines = report.read_text(encoding="utf-8").splitlines() if report.exists() else []
    writes = [json.loads(line) for line in lines]
    return "".join(
        json.dumps({"writer": Path(write["path"]).name, **{key: write[key] for key in REPORTED}}) + "\n"
        for write in writes
    )


@pytest.mark.parametrize("flag", ["check", "report", "off"])
def test_store_write_guard_reports_a_custom_parser_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    """A model described directly after L1 raises (``1``), is reported (``report:<path>``) or passes (``0``)."""
    report = tmp_path / "writes.jsonl"
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, {"check": "1", "report": f"report:{report}", "off": "0"}[flag])
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "0")
    parser = DescribingParser(SCHEMA, formatters=[Formatter.BUILTIN])
    if flag == "check":
        with pytest.raises(
            Error,
            match=rf"^{CHECK_STORE_WRITES_ENV}: BaseModel\.description is written outside store operations by "
            r"(DescribingParser\.)?_process_single_module \(.+test_store_write_guard\.py:\d+\), in "
            r"(DescribingParser\.)?_process_single_module during MODULES$",
        ):
            parser.parse()
        return
    assert_output(parser.parse(), name="output")
    assert_output(_reported(report), name="writes")


def test_store_write_guard_reports_list_writes_outside_store_operations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Store operations write freely, and the lists they set report direct writes, once per writing line."""
    report = tmp_path / "writes.jsonl"
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, f"report:{report}")
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "0")

    output = ReorderingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output(output, name="output")
    assert_output(_reported(report), name="writes")


def test_store_write_guard_leaves_deep_copies_unguarded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A deep copy of a list that a store operation set is a plain list, and writing it reports nothing."""
    report = tmp_path / "writes.jsonl"
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, f"report:{report}")
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "0")
    parser = CopyingParser(SCHEMA, formatters=[Formatter.BUILTIN])
    parser.copy_types = []

    parser.parse()

    copy_types = "".join(f"{copy_type.__name__}\n" for copy_type in parser.copy_types)
    assert_output(f"copies:\n{copy_types}writes:\n{_reported(report)}", name="copies")


@pytest.mark.parametrize("flag", ["report", "finality", "off"])
def test_store_write_guard_is_installed_only_while_a_parse_is_armed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    """With either area that watches writes on, the graph classes are guarded while a parse runs, and given back."""
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, f"report:{tmp_path / 'writes.jsonl'}" if flag == "report" else "0")
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "1" if flag == "finality" else "0")
    parser = InstallRecordingParser(SCHEMA, formatters=[Formatter.BUILTIN])

    parser.parse()

    after = {cls.__name__: "__setattr__" in vars(cls) for cls in GUARDED_CLASSES}
    assert_output(json.dumps({"during": parser.installed, "after": after}, indent=2) + "\n")


def test_store_write_guard_of_a_nested_parse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A parse run inside a pass keeps the guard of the parse around it: both parses report their writes."""
    report = tmp_path / "writes.jsonl"
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, f"report:{report}")
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "0")

    NestingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output(_reported(report))


def test_store_write_guard_keeps_what_the_parsed_graph_shares(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Objects that several holders share at the end of L1 are guarded once, and a shared list stays one list."""
    report = tmp_path / "writes.jsonl"
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, f"report:{report}")
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, "0")
    parser = SharingParser(SCHEMA.with_name("combined_array.json"), formatters=[Formatter.BUILTIN])

    output = parser.parse()

    assert_output(output, name="output")
    assert_output(json.dumps({"shared while processing modules": parser.shared}) + "\n", name="shared")
