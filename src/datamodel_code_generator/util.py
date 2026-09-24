"""Utility functions for YAML/TOML loading and lazy BaseModel access."""

from __future__ import annotations

import re
import sys
import warnings
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal, cast

from datamodel_code_generator.deprecations import warn_deprecated

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@lru_cache(maxsize=1)
def _get_toml_loader() -> Callable[[Any], dict[str, Any]]:
    """Get the TOML parser lazily."""
    try:
        from tomllib import load as load_toml_data  # noqa: PLC0415  # ty: ignore[unresolved-import]
    except ImportError:  # pragma: no cover
        from tomli import load as load_toml_data  # noqa: PLC0415  # ty: ignore[unresolved-import]

    return load_toml_data


def load_toml(path: Path) -> dict[str, Any]:
    """Load and parse a TOML file."""
    record_watch_dependency(path)
    with path.open("rb") as f:
        return _get_toml_loader()(f)


def record_watch_dependency(path: Path) -> None:
    """Record an active watch dependency without importing watch support on normal paths."""
    if (watch_dependencies := sys.modules.get("datamodel_code_generator.watch_dependencies")) is not None and (
        watch_dependencies.collector_is_active()
    ):
        watch_dependencies.record_local_dependency(path)


_YAML_1_2_BOOL_PATTERN = re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$")
_YAML_DEPRECATED_BOOL_VALUES = ("True", "False", "TRUE", "FALSE")
_YAML_DEPRECATED_BOOL_LINE_PATTERN = re.compile(r"(?m)(?::|-\s*)\s*(True|False|TRUE|FALSE)(?:\s*(?:#.*)?)$")
_YAML_DEPRECATED_BOOL_WARNING_MESSAGE = "YAML bool "
_YAML_DEPRECATED_BOOL_WARNING_MODULE = "datamodel_code_generator"
_YAML_UNSUPPORTED_TAGS = {"tag:yaml.org,2002:set"}
_YAML_UNSUPPORTED_TAG_MARKERS = ("!!set", "tag:yaml.org,2002:set")
_YAML_TAG_DIRECTIVE_PATTERN = re.compile(r"(?m)^%TAG(?:\s|$)")
# Pattern for scientific notation without decimal point (e.g., 1e-5, 1E+10)
# Standard YAML only matches floats with decimal points, missing patterns like "1e-5"
_YAML_SCIENTIFIC_NOTATION_PATTERN = re.compile(r"^[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+$")


def _warning_filter_matches(pattern: Any, text: str) -> bool:
    if pattern is None:
        return True
    if hasattr(pattern, "match"):
        return bool(pattern.match(text))
    return re.match(str(pattern), text) is not None


def _is_yaml_deprecated_bool_warning_enabled() -> bool:
    for action, message, category, module, _ in warnings.filters:
        if not issubclass(DeprecationWarning, category):
            continue
        if not _warning_filter_matches(message, _YAML_DEPRECATED_BOOL_WARNING_MESSAGE):
            continue
        if not _warning_filter_matches(module, _YAML_DEPRECATED_BOOL_WARNING_MODULE):
            continue
        return action != "ignore"
    return True


def warn_yaml_deprecated_bool_values(text: str) -> None:
    """Warn for YAML 1.1-style boolean scalars when ryaml is used."""
    if not _is_yaml_deprecated_bool_warning_enabled() or not any(
        value in text for value in _YAML_DEPRECATED_BOOL_VALUES
    ):
        return

    for match in _YAML_DEPRECATED_BOOL_LINE_PATTERN.finditer(text):
        warnings.warn(
            f"YAML bool '{match.group(1)}' is deprecated. Use lowercase 'true' or 'false' instead. "
            f"In a future version, only lowercase booleans will be recognized.",
            DeprecationWarning,
            stacklevel=3,
        )


def _iter_yaml_nodes(node: Any) -> Iterator[Any]:
    import yaml  # noqa: PLC0415

    yield node
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            yield from _iter_yaml_nodes(key_node)
            yield from _iter_yaml_nodes(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item_node in node.value:
            yield from _iter_yaml_nodes(item_node)


def reject_unsupported_yaml_tags(text: str) -> None:
    """Reject YAML tags that cannot be represented by JSON-compatible sample data."""
    if not any(marker in text for marker in _YAML_UNSUPPORTED_TAG_MARKERS) and not _YAML_TAG_DIRECTIVE_PATTERN.search(
        text
    ):
        return

    import yaml  # noqa: PLC0415

    node = yaml.compose(text, Loader=cast("type[yaml.SafeLoader | yaml.CSafeLoader]", get_safe_loader()))
    if node is None:
        return
    for yaml_node in _iter_yaml_nodes(node):
        if yaml_node.tag in _YAML_UNSUPPORTED_TAGS:
            msg = f"Unsupported YAML tag: {yaml_node.tag}"
            raise yaml.YAMLError(msg)


def _construct_yaml_bool_with_warning(loader: Any, node: Any) -> bool:
    value = loader.construct_scalar(node)
    if value in _YAML_DEPRECATED_BOOL_VALUES:
        warn_deprecated(
            "config.yaml-non-lowercase-bool",
            details=(
                f"YAML bool {value!r} is deprecated. In a future version, only lowercase booleans will be recognized."
            ),
            stacklevel=6,
        )
    return value in {"true", "True", "TRUE"}


def _fast_construct_document(loader: Any, root: Any) -> Any:  # noqa: PLR0914, PLR0915
    """Construct a composed node tree in PyYAML's breadth-first order without per-node dispatch.

    Behaviourally identical to ``BaseConstructor.construct_document`` for SafeLoader-family
    loaders: PyYAML's ``constructed_objects`` memo and ``state_generators`` queue are shared,
    so aliases, recursive structures, merge keys, explicit tags and error cases take the
    standard path in the same order. Only plain map / seq / str handling is inlined.
    """
    from collections import deque  # noqa: PLC0415
    from collections.abc import Hashable  # noqa: PLC0415
    from types import GeneratorType  # noqa: PLC0415

    from yaml.constructor import ConstructorError, SafeConstructor  # noqa: PLC0415
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode  # noqa: PLC0415

    map_tag = "tag:yaml.org,2002:map"
    seq_tag = "tag:yaml.org,2002:seq"
    str_tag = "tag:yaml.org,2002:str"
    generator_marker = object()

    constructors = loader.yaml_constructors
    plain_map = constructors.get(map_tag) is SafeConstructor.construct_yaml_map
    plain_seq = constructors.get(seq_tag) is SafeConstructor.construct_yaml_seq
    plain_str = constructors.get(str_tag) is SafeConstructor.construct_yaml_str
    flatten_mapping = loader.flatten_mapping
    construct_object = loader.construct_object
    memo = loader.constructed_objects
    state_generators = loader.state_generators
    queue: deque[tuple[Any, Any]] = deque()

    def delegate(node: Any) -> Any:
        """Use standard PyYAML construction and retain its breadth-first deferred work."""
        value = construct_object(node)
        while state_generators:
            queue.append((generator_marker, state_generators.pop(0)))
        return value

    def container_for(node: Any) -> Any:
        if (existing := memo.get(node)) is not None:
            return existing

        node_class = node.__class__
        match node_class:
            case _ if node_class is MappingNode and node.tag == map_tag and plain_map:
                value: Any = {}
            case _ if node_class is SequenceNode and node.tag == seq_tag and plain_seq:
                value = []
            case _:
                return delegate(node)

        memo[node] = value
        queue.append((node, value))
        return value

    def scalar(node: Any) -> Any:
        tag = node.tag
        if tag == str_tag and plain_str:
            return node.value
        constructor = constructors.get(tag)
        if constructor is None:
            return delegate(node)
        value = constructor(loader, node)
        if value.__class__ is GeneratorType:
            value.close()
            return delegate(node)
        return value

    result = scalar(root) if root.__class__ is ScalarNode else container_for(root)
    while queue:
        node, container = queue.popleft()
        if node is generator_marker:
            for _ in container:
                pass
            while state_generators:
                queue.append((generator_marker, state_generators.pop(0)))
            continue
        if node.__class__ is MappingNode:
            flatten_mapping(node)
            for key_node, value_node in node.value:
                if key_node.__class__ is ScalarNode and key_node.tag == str_tag and plain_str:
                    key = key_node.value
                else:
                    key = scalar(key_node) if key_node.__class__ is ScalarNode else delegate(key_node)
                    if not isinstance(key, Hashable):
                        raise ConstructorError(  # noqa: TRY003
                            "while constructing a mapping",  # noqa: EM101
                            node.start_mark,
                            "found unhashable key",
                            key_node.start_mark,
                        )
                container[key] = scalar(value_node) if value_node.__class__ is ScalarNode else container_for(value_node)
        else:
            append = container.append
            for item in node.value:
                append(scalar(item) if item.__class__ is ScalarNode else container_for(item))
    return result


@lru_cache(maxsize=1)
def get_safe_loader() -> type:
    """Get customized SafeLoader lazily."""
    try:
        from yaml import CSafeLoader as _SafeLoader  # noqa: PLC0415
    except ImportError:  # pragma: no cover
        from yaml import SafeLoader as _SafeLoader  # noqa: PLC0415

    class CustomSafeLoader(_SafeLoader):  # ty: ignore[unsupported-base]
        """SafeLoader with YAML 1.2 bool handling and timestamp-as-string."""

        yaml_constructors = _SafeLoader.yaml_constructors.copy()
        yaml_implicit_resolvers = {  # noqa: RUF012
            k: v
            for k, v in (
                (k, [(tag, pat) for tag, pat in v if tag != "tag:yaml.org,2002:bool"])
                for k, v in _SafeLoader.yaml_implicit_resolvers.items()
            )
            if v
        }

        def construct_document(self, node: Any) -> Any:
            if not hasattr(self, "state_generators"):  # pragma: no cover  # PyYAML internals changed
                return super().construct_document(node)
            data = _fast_construct_document(self, node)
            while self.state_generators:  # pragma: no cover  # normally empty; mirrors BaseConstructor
                generators = self.state_generators
                self.state_generators = []
                for generator in generators:
                    for _ in generator:
                        pass
            self.constructed_objects = {}
            self.recursive_objects = {}
            self.deep_construct = False
            return data

    CustomSafeLoader.yaml_constructors["tag:yaml.org,2002:timestamp"] = CustomSafeLoader.yaml_constructors[
        "tag:yaml.org,2002:str"
    ]
    for key in ["t", "f", "T", "F"]:
        CustomSafeLoader.yaml_implicit_resolvers.setdefault(key, []).append((
            "tag:yaml.org,2002:bool",
            _YAML_1_2_BOOL_PATTERN,
        ))
    CustomSafeLoader.yaml_constructors["tag:yaml.org,2002:bool"] = _construct_yaml_bool_with_warning

    # Add scientific notation without decimal point (e.g., 1e-5) as float
    for key in ["-", "+", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
        CustomSafeLoader.yaml_implicit_resolvers.setdefault(key, []).append((
            "tag:yaml.org,2002:float",
            _YAML_SCIENTIFIC_NOTATION_PATTERN,
        ))

    return CustomSafeLoader


YamlBackend = Literal["ryaml", "pyyaml"]


@lru_cache(maxsize=1)
def get_yaml_backend() -> YamlBackend:
    """Detect the available YAML backend ('ryaml' or 'pyyaml')."""
    try:
        import ryaml  # noqa: PLC0415, F401  # ty: ignore[unresolved-import]
    except ImportError:
        return "pyyaml"
    else:
        return "ryaml"


@lru_cache(maxsize=1)
def get_yaml_parse_errors() -> tuple[type[Exception], ...]:
    """Return YAML parse error types for both backends."""
    import yaml  # noqa: PLC0415

    errors: list[type[Exception]] = [yaml.YAMLError]
    try:
        import ryaml  # noqa: PLC0415  # ty: ignore[unresolved-import]

        errors.append(ryaml.InvalidYamlError)
    except ImportError:
        pass
    return tuple(errors)


@lru_cache(maxsize=1)
def _get_base_model_class() -> type:
    """Get BaseModel class with strict=False config lazily."""
    from pydantic import BaseModel as _PydanticBaseModel  # noqa: PLC0415
    from pydantic import ConfigDict as _ConfigDict  # noqa: PLC0415

    class _BaseModelV2(_PydanticBaseModel):
        model_config = _ConfigDict(strict=False, defer_build=True)

    return _BaseModelV2


def create_module_getattr(
    module_name: str,
    lazy_imports: dict[str, tuple[str, str]],
) -> Callable[[str], Any]:
    """Create a __getattr__ function for lazy module imports.

    Args:
        module_name: The name of the module (typically __name__).
        lazy_imports: Mapping of attribute name to (module_path, attribute_name).

    Returns:
        A __getattr__ function that lazily imports the specified attributes.

    Example:
        __getattr__ = create_module_getattr(__name__, {
            "MyClass": ("mypackage.mymodule", "MyClass"),
        })
    """
    from importlib import import_module  # noqa: PLC0415

    def _getattr(name: str) -> Any:
        if name in lazy_imports:
            module_path, attr_name = lazy_imports[name]
            module = import_module(module_path)
            return getattr(module, attr_name)
        msg = f"module {module_name!r} has no attribute {name!r}"
        raise AttributeError(msg)

    return _getattr


def __getattr__(name: str) -> Any:
    """Provide lazy access to BaseModel and SafeLoader."""
    if name == "BaseModel":
        return _get_base_model_class()
    if name == "SafeLoader":
        return get_safe_loader()
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


_UNDER_SCORE_1: re.Pattern[str] = re.compile(r"([^_])([A-Z][a-z]+)")
_UNDER_SCORE_2: re.Pattern[str] = re.compile(r"([a-z0-9])([A-Z])")


@lru_cache
def camel_to_snake(string: str) -> str:
    """Convert camelCase or PascalCase to snake_case."""
    subbed = _UNDER_SCORE_1.sub(r"\1_\2", string)
    return _UNDER_SCORE_2.sub(r"\1_\2", subbed).lower()
