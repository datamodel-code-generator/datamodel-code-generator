"""Pydantic v2 RootModel implementation.

Generates models inheriting from pydantic.RootModel for wrapping single types.
"""

from __future__ import annotations

from typing import Any, ClassVar

from datamodel_code_generator import Error
from datamodel_code_generator.imports import IMPORT_ANY, Import
from datamodel_code_generator.model.pydantic_v2.base_model import (
    _CONFIG_ITEMS_TEMPLATE_DATA_KEY,
    _NEUTRALIZE_ROOT_MODEL_EXTRA_CONFIG_TEMPLATE_DATA_KEY,
    BaseModel,
    _config_dict_items,
    _safe_config_dict_items,
)
from datamodel_code_generator.model.pydantic_v2.imports import IMPORT_CONFIG_DICT
from datamodel_code_generator.python_literal import represent_untrusted_python_value

IMPORT_ABC_ITERATOR = Import.from_full_path("collections.abc.Iterator")
IMPORT_ABC_SEQUENCE = Import.from_full_path("collections.abc.Sequence")
IMPORT_OVERLOAD = Import.from_full_path("typing.overload")
IMPORT_SUPPORTS_INDEX = Import.from_full_path("typing.SupportsIndex")
_SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY = "sequence_base_class"
_SEQUENCE_ITEM_TYPE_TEMPLATE_DATA_KEY = "sequence_item_type"
_SEQUENCE_SLICE_TYPE_TEMPLATE_DATA_KEY = "sequence_slice_type"
_STRING_PATTERN_ALIAS_REACHED_TEMPLATE_DATA_KEY = "string_pattern_alias_reached"
_ROOT_MODEL_CONFIG_KEYS: frozenset[str] = frozenset({"regex_engine", "frozen"})


def _root_model_config_items(config: Any) -> list[tuple[str, Any]]:
    return [
        (field_name, value)
        for field_name, value in _config_dict_items(config)
        if field_name in _ROOT_MODEL_CONFIG_KEYS and value is not None
    ]


class RootModel(BaseModel):
    """DataModel for Pydantic v2 RootModel."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "pydantic_v2/RootModel.jinja2"
    BASE_CLASS: ClassVar[str] = "pydantic.RootModel"
    IS_ROOT_MODEL: ClassVar[bool] = True
    REQUIRES_FIELD_DEPENDENCY_ORDERING: ClassVar[bool] = True
    SUPPORTS_CONFIG_EXTRA: ClassVar[bool] = False
    SUPPORTS_ARBITRARY_TYPES_ALLOWED: ClassVar[bool] = False

    @classmethod
    def _uses_builtin_hash_implementation(cls) -> bool:
        """Recognize the builtin root without a reverse import from BaseModel."""
        return cls is RootModel

    def add_literal_validation(self, values: list[object]) -> None:
        """Keep finite JSON literal membership beside the root's existing value constraints."""
        self._requires_literal_validation = True
        self._literal_values = values
        self._additional_imports.extend((
            IMPORT_ANY,
            IMPORT_CONFIG_DICT,
            Import.from_full_path("enum.Enum"),
            Import.from_full_path("pydantic.model_validator"),
        ))
        literal_source = repr(values)
        if any(
            marker in literal_source
            for marker in ("[inf", "(inf", "{inf", " inf", "[nan", "(nan", "{nan", " nan", "-inf")
        ):
            literal_source = represent_untrusted_python_value(values)
        lines = [
            "",
            "@classmethod",
            "def _json_schema_literal_key(cls, value: Any) -> Any:",
            "    if isinstance(value, Enum):",
            "        value = value.value",
            "    if getattr(type(value), '__pydantic_root_model__', False):",
            "        value = value.model_dump(mode='json')",
            "    if isinstance(value, dict):",
            "        return (",
            "            'object',",
            "            frozenset(",
            "                (key, cls._json_schema_literal_key(item))",
            "                for key, item in value.items()",
            "            ),",
            "        )",
            "    if isinstance(value, list):",
            "        return (",
            "            'array',",
            "            tuple(cls._json_schema_literal_key(item) for item in value),",
            "        )",
            "    if isinstance(value, bool):",
            "        return ('boolean', value)",
            "    if isinstance(value, (int, float)):",
            "        return ('number', value)",
            "    if isinstance(value, str):",
            "        return ('string', value)",
            "    return (type(value).__name__, value)",
            "",
            "@model_validator(mode='before')",
            "@classmethod",
            "def _validate_json_schema_literal(cls, value: Any) -> Any:",
            "    if isinstance(value, Enum):",
            "        value = value.value",
            "    if getattr(type(value), '__pydantic_root_model__', False):",
            "        value = value.model_dump(mode='json')",
            "    candidate = cls._json_schema_literal_key(value)",
            f"    allowed_values = {literal_source}",
            "    if not any(",
            "        candidate == cls._json_schema_literal_key(allowed)",
            "        for allowed in allowed_values",
            "    ):",
            "        raise ValueError('Value does not match an allowed JSON Schema literal')",
            "    return value",
            "",
        ]
        if any(isinstance(value, (dict, list)) for value in values):
            self._additional_imports.extend((
                Import.from_full_path("pydantic.BaseModel"),
                Import.from_full_path("pydantic.TypeAdapter"),
            ))
            key_index = lines.index("def _json_schema_literal_key(cls, value: Any) -> Any:")
            lines[key_index] = "def _json_schema_literal_key(cls, value: Any, models: list[bool] | None = None) -> Any:"
            lines[key_index + 1 : key_index + 1] = [
                "    if models is not None and isinstance(value, BaseModel):",
                "        models[0] = True",
                "        return ('model', id(value))",
            ]
            lines = [
                line.replace("_json_schema_literal_key(item)", "_json_schema_literal_key(item, models)")
                for line in lines
            ]
            validator_index = lines.index("def _validate_json_schema_literal(cls, value: Any) -> Any:")
            lines[validator_index + 4] = "        value = value.model_dump(mode='json', by_alias=True)"
            lines[validator_index - 2] = "@model_validator(mode='wrap')"
            lines[validator_index] = "def _validate_json_schema_literal(cls, value: Any, handler: Any) -> Any:"
            lines[validator_index + 1 : validator_index + 1] = [
                "    if isinstance(value, cls):",
                "        return handler(value)",
            ]
            lines[-2] = "    return result if models[0] else handler(value)"
            candidate_index = lines.index("    candidate = cls._json_schema_literal_key(value)")
            lines[candidate_index : candidate_index + 1] = [
                "    models = [False]",
                "    candidate = cls._json_schema_literal_key(value, models)",
                "    if models[0]:",
                "        result = handler(value)",
                "        try:",
                "            adapter = TypeAdapter(cls.model_fields['root'].annotation)",
                "            parsed = result.root",
                "            dump = adapter.dump_python",
                "            serialized = dump(parsed, mode='json', by_alias=True, warnings=False)",
                "            candidate_value = cls._json_schema_model_value(value, serialized)",
                "        except (AttributeError, IndexError, TypeError, ValueError) as error:",
                "            raise ValueError('Cannot serialize JSON Schema literal') from error",
                "        candidate = cls._json_schema_literal_key(candidate_value)",
            ]
            lines.extend([
                "@classmethod",
                "def _json_schema_model_value(cls, value: Any, serialized: Any) -> Any:",
                "    if isinstance(value, BaseModel):",
                "        return serialized",
                "    if isinstance(value, dict):",
                "        return {",
                "            key: cls._json_schema_model_value(item, serialized.get(key, item))",
                "            for key, item in value.items()",
                "        }",
                "    if isinstance(value, list):",
                "        return [",
                "            cls._json_schema_model_value(item, serialized[index])",
                "            for index, item in enumerate(value)",
                "        ]",
                "    return value",
                "",
            ])
        for line in lines:
            self._append_internal_template_data("class_body_lines", line)

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        """Initialize RootModel without unnecessary model_config.

        RootModel subclasses should not have model_config except when regex_engine is required
        for lookaround patterns. Also removes custom_base_class as it cannot implement both
        BaseModel and RootModel.
        """
        if "custom_base_class" in kwargs:
            kwargs.pop("custom_base_class")

        super().__init__(**kwargs)

        if not self._has_meaningful_config(self.extra_template_data.get("config")):
            self.extra_template_data.pop("config", None)
            self._pop_internal_template_data(_CONFIG_ITEMS_TEMPLATE_DATA_KEY)
            self._additional_imports = [imp for imp in self._additional_imports if imp != IMPORT_CONFIG_DICT]

    @staticmethod
    def _has_meaningful_config(config: Any) -> bool:
        has_config = False
        match config:
            case None:
                pass
            case _:
                has_config = bool(_root_model_config_items(config))
        return has_config

    def _sync_config_items(self) -> None:
        config = self.extra_template_data.get("config")
        config_items = _root_model_config_items(config)
        if literal_values := getattr(self, "_literal_values", None):
            config_items.append(("json_schema_extra", {"enum": literal_values}))
            if not config:
                self.extra_template_data["config"] = {"json_schema_extra": {"enum": literal_values}}
        if self._internal_template_data.get(_NEUTRALIZE_ROOT_MODEL_EXTRA_CONFIG_TEMPLATE_DATA_KEY):
            config_items.append(("extra", None))
            if not config:
                self.extra_template_data["config"] = {"extra": None}
        if config_items:
            self._set_internal_template_data(
                _CONFIG_ITEMS_TEMPLATE_DATA_KEY,
                _safe_config_dict_items(dict(config_items)),
            )
            if IMPORT_CONFIG_DICT not in self._additional_imports:
                self._additional_imports.append(IMPORT_CONFIG_DICT)
            self.clear_imports_cache()
            return
        self.extra_template_data.pop("config", None)
        self._pop_internal_template_data(_CONFIG_ITEMS_TEMPLATE_DATA_KEY)
        self._additional_imports = [imp for imp in self._additional_imports if imp != IMPORT_CONFIG_DICT]
        self.clear_imports_cache()

    def _keep_string_pattern_aliases_out_of_bases(self) -> None:
        """Write ``Any`` in the generic: Pydantic builds it without this model's config, and aliases stay named."""
        self._set_internal_template_data(key=_STRING_PATTERN_ALIAS_REACHED_TEMPLATE_DATA_KEY, value=True)
        self._additional_imports.append(IMPORT_ANY)
        self.invalidate_render_caches()

    def add_sequence_interface(self, item_type: str, slice_type: str) -> None:
        """Add sequence interface helpers that delegate to the wrapped root value."""
        self._additional_imports.append(IMPORT_ABC_ITERATOR)
        self._additional_imports.append(IMPORT_ABC_SEQUENCE)
        self._additional_imports.append(IMPORT_OVERLOAD)
        self._additional_imports.append(IMPORT_SUPPORTS_INDEX)
        if item_type == "Any":
            self._additional_imports.append(IMPORT_ANY)
        sequence_template_data = {
            _SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY: f"Sequence[{item_type}]",
            _SEQUENCE_ITEM_TYPE_TEMPLATE_DATA_KEY: item_type,
            _SEQUENCE_SLICE_TYPE_TEMPLATE_DATA_KEY: slice_type,
        }
        for key, value in sequence_template_data.items():
            self._set_internal_template_data(key, value)
        self.clear_imports_cache()

    def finalize_sequence_interface(self) -> None:
        """Refresh helpers once final root types and reference aliases are available."""
        if _SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY not in self._internal_template_data:
            return
        root_type = self.fields[0].data_type
        if not (root_type.is_list or root_type.is_sequence or root_type.is_set):
            root_type = root_type.data_types[0]
        if root_type.is_set:
            imports = [IMPORT_ABC_ITERATOR, IMPORT_ABC_SEQUENCE, IMPORT_OVERLOAD, IMPORT_SUPPORTS_INDEX]
            if self._internal_template_data[_SEQUENCE_ITEM_TYPE_TEMPLATE_DATA_KEY] == "Any":
                imports.append(IMPORT_ANY)
            for import_ in imports:
                self._additional_imports.remove(import_)
            for key in (
                _SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY,
                _SEQUENCE_ITEM_TYPE_TEMPLATE_DATA_KEY,
                _SEQUENCE_SLICE_TYPE_TEMPLATE_DATA_KEY,
            ):
                self._pop_internal_template_data(key)
            self.invalidate_render_caches()
            return
        # The eligible root is non-optional, so its outer container encloses the
        # exact item hint, including final reference aliases and union syntax.
        slice_type = root_type.type_hint
        _, bracket, item_hint = slice_type.partition("[")
        item_type = item_hint[:-1] if bracket else "Any"
        slice_type = slice_type if bracket else f"{slice_type}[{item_type}]"
        self._set_internal_template_data(_SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY, f"Sequence[{item_type}]")
        self._set_internal_template_data(_SEQUENCE_ITEM_TYPE_TEMPLATE_DATA_KEY, item_type)
        self._set_internal_template_data(_SEQUENCE_SLICE_TYPE_TEMPLATE_DATA_KEY, slice_type)

    def render(self, *, class_name: str | None = None) -> str:
        """Render the RootModel and validate custom sequence templates when needed."""
        use_custom_template = self._uses_custom_root_template
        fields = self._template_fields(use_custom_template=use_custom_template)
        if fields:
            _ = fields[0].type_hint
        self._sync_config_items()
        extra_template_data = self._custom_template_data() if use_custom_template else self._builtin_template_data()
        rendered = self._render(
            class_name=class_name or self.class_name,
            fields=fields,
            decorators=self.decorators,
            base_class=self.base_class,
            methods=self.methods,
            description=self._template_description(use_custom_template=use_custom_template),
            dataclass_arguments=self.dataclass_arguments,
            path=self.path,
            **extra_template_data,
        )
        self._validate_custom_template_sequence_interface(rendered)
        if (
            use_custom_template
            and getattr(self, "_requires_literal_validation", False)
            and "def _validate_json_schema_literal(" not in rendered
        ):
            msg = (
                "The custom RootModel template must render class_body_lines to preserve JSON Schema literal validation."
            )
            raise Error(msg)
        return rendered

    def _validate_custom_template_sequence_interface(self, rendered: str) -> None:
        sequence_base_class = self._internal_template_data.get(_SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY)
        if not self._uses_custom_root_template or not sequence_base_class:
            return

        missing: list[str] = []
        if sequence_base_class not in rendered:
            missing.append(_SEQUENCE_BASE_CLASS_TEMPLATE_DATA_KEY)

        missing.extend(
            method_name
            for method_name in ("__iter__", "__getitem__", "__len__")
            if f"def {method_name}(" not in rendered
        )

        if missing:
            missing_items = ", ".join(missing)
            msg = (
                "The custom RootModel template does not support --use-root-model-sequence-interface. "
                f"Update {self.template_file_path} to render sequence_base_class, "
                "sequence_item_type, and sequence_slice_type. "
                f"Missing: {missing_items}."
            )
            raise Error(msg)
