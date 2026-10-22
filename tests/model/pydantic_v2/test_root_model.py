"""Tests for Pydantic v2 RootModel generation."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error
from datamodel_code_generator.model import DataModelFieldBase
from datamodel_code_generator.model.pydantic_v2 import ConfigDict
from datamodel_code_generator.model.pydantic_v2.base_model import _CONFIG_ITEMS_TEMPLATE_DATA_KEY
from datamodel_code_generator.model.pydantic_v2.root_model import RootModel
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType

if TYPE_CHECKING:
    from pathlib import Path


def test_root_model() -> None:
    """Test RootModel generation with optional field."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                default="abc",
                required=False,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    assert root_model.name == "TestRootModel"
    assert root_model.fields == [
        DataModelFieldBase(
            name="a",
            data_type=DataType(type="str"),
            default="abc",
            required=False,
            parent=root_model,
        )
    ]

    assert root_model.base_class == "RootModel"
    assert root_model.custom_base_class is None
    assert root_model.render() == ("class TestRootModel(RootModel[Optional[str]]):\n    root: Optional[str] = 'abc'")


def test_root_model_sequence_interface() -> None:
    """RootModel can render sequence interface helpers."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(
                    type="str",
                    is_list=True,
                    data_types=[DataType(type="str")],
                    use_standard_collections=True,
                ),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    assert not any(import_.import_ == "Iterator" for import_ in root_model.imports)

    root_model.add_sequence_interface("str", "list[str]")
    root_model.finalize_sequence_interface()

    assert root_model.render() == (
        "class TestRootModel(RootModel[list[str]], Sequence[str]):\n"
        "    root: list[str]\n\n"
        "    def __iter__(self) -> Iterator[str]:\n"
        "        return iter(self.root)\n\n"
        "    @overload\n"
        "    def __getitem__(self, index: SupportsIndex) -> str:\n"
        "        pass\n\n"
        "    @overload\n"
        "    def __getitem__(self, index: slice) -> list[str]:\n"
        "        pass\n\n"
        "    def __getitem__(self, index: SupportsIndex | slice) -> str | list[str]:\n"
        "        return self.root[index]\n\n"
        "    def __len__(self) -> int:\n"
        "        return len(self.root)"
    )
    assert any(import_.import_ == "Iterator" for import_ in root_model.imports)
    assert any(import_.import_ == "Sequence" for import_ in root_model.imports)
    assert any(import_.import_ == "overload" for import_ in root_model.imports)
    assert any(import_.import_ == "SupportsIndex" for import_ in root_model.imports)


def test_root_model_sequence_interface_with_decorator() -> None:
    """RootModel sequence interface keeps decorators before the class definition."""
    root_model = RootModel(
        decorators=["@some_decorator"],
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str", is_list=True, data_types=[DataType(type="str")]),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    root_model.add_sequence_interface("str", "list[str]")

    rendered_lines = root_model.render().splitlines()
    assert rendered_lines[0] == "@some_decorator"
    assert rendered_lines[1] == "class TestRootModel(RootModel[List[str]], Sequence[str]):"


def test_root_model_sequence_interface_rejects_unsupported_custom_template(tmp_path: Path) -> None:
    """Unsupported custom RootModel templates fail when sequence interface helpers are requested."""
    template_path = tmp_path / "pydantic_v2" / "RootModel.jinja2"
    template_path.parent.mkdir()
    template_path.write_text(
        "class {{ class_name }}({{ base_class }}):\n    root: {{ fields[0].type_hint }}\n",
        encoding="utf-8",
    )
    root_model = RootModel(
        custom_template_dir=tmp_path,
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str", is_list=True, data_types=[DataType(type="str")]),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    root_model.add_sequence_interface("str", "List[str]")

    with pytest.raises(Error, match="does not support --use-root-model-sequence-interface"):
        root_model.render()


def test_root_model_sequence_interface_accepts_supported_custom_template(tmp_path: Path) -> None:
    """Custom RootModel templates can opt in by rendering sequence template variables."""
    template_path = tmp_path / "pydantic_v2" / "RootModel.jinja2"
    template_path.parent.mkdir()
    template_path.write_text(
        "class {{ class_name }}({{ base_class }}{% if sequence_base_class is defined %}, "
        "{{ sequence_base_class }}{% endif %}):\n"
        "    root: {{ fields[0].type_hint }}\n"
        "{% if sequence_item_type is defined and sequence_slice_type is defined %}\n"
        "    def __iter__(self) -> Iterator[{{ sequence_item_type }}]:\n"
        "        return iter(self.root)\n\n"
        "    @overload\n"
        "    def __getitem__(self, index: SupportsIndex) -> {{ sequence_item_type }}:\n"
        "        pass\n\n"
        "    @overload\n"
        "    def __getitem__(self, index: slice) -> {{ sequence_slice_type }}:\n"
        "        pass\n\n"
        "    def __getitem__(self, index: SupportsIndex | slice) -> "
        "{{ sequence_item_type }} | {{ sequence_slice_type }}:\n"
        "        return self.root[index]\n\n"
        "    def __len__(self) -> int:\n"
        "        return len(self.root)\n"
        "{% endif %}\n",
        encoding="utf-8",
    )
    root_model = RootModel(
        custom_template_dir=tmp_path,
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str", is_list=True, data_types=[DataType(type="str")]),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    root_model.add_sequence_interface("str", "List[str]")
    rendered = root_model.render()

    assert "class TestRootModel(RootModel, Sequence[str]):" in rendered
    assert "def __iter__(self) -> Iterator[str]:" in rendered
    assert "def __getitem__(self, index: SupportsIndex) -> str:" in rendered
    assert "def __len__(self) -> int:" in rendered


def test_root_model_sequence_interface_add_any_import() -> None:
    """Sequence interface helpers import Any when the wrapped item type is Any."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(is_list=True, data_types=[DataType()]),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    root_model.add_sequence_interface("Any", "list[Any]")

    assert "def __iter__(self) -> Iterator[Any]" in root_model.render()
    assert any(import_.import_ == "Any" for import_ in root_model.imports)


def test_root_model_custom_base_class_is_ignored() -> None:
    """Verify that passing a custom_base_class is ignored."""
    root_model = RootModel(
        custom_base_class="test.Test",
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                default="abc",
                required=False,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    assert root_model.name == "TestRootModel"
    assert root_model.fields == [
        DataModelFieldBase(
            name="a",
            data_type=DataType(type="str"),
            default="abc",
            required=False,
            parent=root_model,
        )
    ]

    assert root_model.base_class == "RootModel"
    assert root_model.custom_base_class is None  # make sure it's ignored
    assert root_model.render() == ("class TestRootModel(RootModel[Optional[str]]):\n    root: Optional[str] = 'abc'")


@pytest.mark.parametrize("additional_properties", [True, False])
def test_root_model_ignores_extra_config(additional_properties: bool) -> None:
    """RootModel must not render ConfigDict(extra=...) because Pydantic rejects it."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
        extra_template_data=defaultdict(dict, {"test_root_model": {"additionalProperties": additional_properties}}),
    )

    assert "model_config" not in root_model.render()
    assert _CONFIG_ITEMS_TEMPLATE_DATA_KEY not in root_model.extra_template_data


def test_root_model_ignores_arbitrary_types_config() -> None:
    """RootModel must not render arbitrary_types_allowed for custom root types."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="ExternalType", is_custom_type=True),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )

    assert "model_config" not in root_model.render()


def test_root_model_renders_existing_config_object() -> None:
    """RootModel renders config objects with their derived config_items."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
        extra_template_data=defaultdict(
            dict,
            {"test_root_model": {"config": ConfigDict(regex_engine='"python-re"')}},
        ),
    )

    rendered = root_model.render()

    assert "model_config = ConfigDict(" in rendered
    assert 'regex_engine="python-re",' in rendered
    assert isinstance(root_model.extra_template_data["config"], ConfigDict)
    assert root_model.extra_template_data["config"].dict(exclude_unset=True) == {"regex_engine": '"python-re"'}
    assert _CONFIG_ITEMS_TEMPLATE_DATA_KEY in root_model._internal_template_data


def test_root_model_renders_late_config_dict() -> None:
    """RootModel syncs config_items for config added after initialization."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )
    root_model.extra_template_data["config"] = {"regex_engine": '"python-re"'}

    rendered = root_model.render()

    assert "model_config = ConfigDict(" in rendered
    assert 'regex_engine="python-re",' in rendered
    assert _CONFIG_ITEMS_TEMPLATE_DATA_KEY in root_model._internal_template_data


def test_root_model_renders_late_config_after_literal_validation() -> None:
    """A config set after a render with literal validation replaces the config that render synthesized."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
    )
    root_model.add_literal_validation(["a"])
    assert "json_schema_extra" in root_model.render()

    root_model.extra_template_data["config"] = {"regex_engine": '"python-re"'}
    rendered = root_model.render()

    assert 'regex_engine="python-re",' in rendered
    assert root_model._builtin_template_data()["config"] == {"regex_engine": '"python-re"'}

    del root_model.extra_template_data["config"]
    root_model._literal_values = None
    assert "model_config" not in root_model.render()
    assert "config" not in root_model._builtin_template_data()


def test_root_model_include_only_custom_dir_keeps_builtin_context_safe(tmp_path: Path) -> None:
    """A custom include directory does not make the built-in RootModel context raw."""
    root_model = RootModel(
        fields=[DataModelFieldBase(name="root", data_type=DataType(type="str"), required=True)],
        reference=Reference(name="Root", path="Root"),
        custom_template_dir=tmp_path,
        extra_template_data=defaultdict(dict, {"Root": {"config_items": [("x", "True")]}}),
    )

    with pytest.raises(Error, match="config_items is reserved"):
        root_model.render()


def test_root_model_rejects_external_config_items() -> None:
    """Built-in RootModel rendering reserves its code-bearing config items."""
    root_model = RootModel(
        fields=[
            DataModelFieldBase(
                name="a",
                data_type=DataType(type="str"),
                required=True,
            )
        ],
        reference=Reference(name="TestRootModel", path="test_root_model"),
        extra_template_data=defaultdict(
            dict,
            {
                "test_root_model": {
                    "config": ConfigDict(regex_engine='"python-re"'),
                    _CONFIG_ITEMS_TEMPLATE_DATA_KEY: object(),
                }
            },
        ),
    )

    with pytest.raises(Error, match="config_items is reserved"):
        root_model.render()
