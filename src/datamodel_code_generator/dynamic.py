"""Dynamic model generation module for datamodel-code-generator.

This module provides runtime generation of Pydantic v2 models from JSON Schema
or OpenAPI schemas.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import itertools
import json
import sys
import threading
import types
from enum import Enum
from pathlib import PurePath
from typing import TYPE_CHECKING, Any, cast

import pydantic
from pydantic import BaseModel

from datamodel_code_generator import Error, generate, is_asyncapi, is_openapi
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator.config import GenerateConfig, _rebuild_generate_config
from datamodel_code_generator.enums import DataModelType, InputFileType

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_dynamic_models_cache: dict[str, dict[str, type]] = {}
_dynamic_models_lock = threading.Lock()
_dynamic_module_counter = itertools.count(1)
_MISSING_MODULE = object()


def _evict_dynamic_models_cache_entries(count: int) -> None:
    for _ in range(count):
        del _dynamic_models_cache[next(iter(_dynamic_models_cache))]


def _is_init_file(path_tuple: tuple[str, ...]) -> bool:
    """Check if path tuple represents an __init__.py file."""
    return PurePath(path_tuple[-1]).stem == "__init__"


def _path_to_module_name(package_name: str, path_tuple: tuple[str, ...]) -> str:
    """Convert path tuple to module name."""
    parts = [package_name, *path_tuple[:-1]]
    stem = PurePath(path_tuple[-1]).stem
    if stem != "__init__":
        parts.append(stem)
    return ".".join(parts)


def _execute_single_module(
    code: str,
    *,
    include_private_models: bool = False,
    module_name: str | None = None,
) -> dict[str, type]:
    """Execute single module code and extract models."""
    namespace: dict[str, Any] = {"__builtins__": builtins.__dict__}
    if module_name is not None:
        namespace["__name__"] = module_name
        namespace["__package__"] = module_name.rpartition(".")[0]
    exec(code, namespace)  # noqa: S102

    models = _extract_models(namespace, code=code, include_private=include_private_models)

    for obj in models.values():
        if issubclass(obj, BaseModel) and hasattr(obj, "__pydantic_generic_metadata__"):
            obj.model_rebuild(_types_namespace=namespace)

    return models


def _path_to_relative_module_path(path_tuple: tuple[str, ...]) -> tuple[str, ...] | None:
    """Convert a generated Python file path to its path relative to the package."""
    filepath = PurePath(path_tuple[-1])
    if filepath.suffix != ".py":
        return None
    if filepath.stem == "__init__":
        return path_tuple[:-1]
    return (*path_tuple[:-1], filepath.stem)


def _get_relative_import_paths(
    path_tuple: tuple[str, ...],
    code: str,
    module_paths: dict[tuple[str, ...], tuple[str, ...]],
) -> set[tuple[str, ...]]:
    """Resolve relative imports in a generated module to generated file paths."""
    imports: set[tuple[str, ...]] = set()
    package_path = path_tuple[:-1]
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level or node.level > len(package_path) + 1:
            continue

        base_path = package_path[: len(package_path) - node.level + 1]
        if node.module:
            module_path = (*base_path, *node.module.split("."))
            if imported_path := module_paths.get(module_path):
                imports.add(imported_path)
        else:
            module_path = base_path

        for alias in node.names:
            if alias.name == "*":
                continue
            if (imported_path := module_paths.get((*module_path, alias.name))) or (
                node.module is None and (imported_path := module_paths.get(module_path))
            ):
                imports.add(imported_path)
    return imports


def _build_module_edges(modules: dict[tuple[str, ...], str]) -> dict[tuple[str, ...], set[tuple[str, ...]]]:
    """Build dependency edges for topological sort.

    Returns edges where edges[u] contains v means u must come before v.
    """
    module_paths: dict[tuple[str, ...], tuple[str, ...]] = {}
    for path in modules:
        if (module_path := _path_to_relative_module_path(path)) is not None:
            module_paths[module_path] = path

    edges: dict[tuple[str, ...], set[tuple[str, ...]]] = {path: set() for path in modules}
    for path, code in modules.items():
        for dep_path in _get_relative_import_paths(path, code, module_paths):
            if dep_path != path:
                edges[dep_path].add(path)
    return edges


def _execute_multi_module(
    modules: dict[tuple[str, ...], str],
    *,
    include_private_models: bool = False,
    module_name: str | None = None,
) -> dict[str, type]:
    """Execute multiple modules and extract models."""
    package_name = module_name or f"_dcg_dynamic_{next(_dynamic_module_counter)}"

    created_modules: list[str] = []
    previous_modules: dict[str, types.ModuleType | object] = {}
    all_namespaces: dict[str, dict[str, Any]] = {}

    def register_module(name: str, module: types.ModuleType) -> None:
        previous_modules[name] = sys.modules.get(name, _MISSING_MODULE)
        sys.modules[name] = module
        created_modules.append(name)

    try:
        nodes = list(modules.keys())
        nodes.sort(key=lambda p: (_is_init_file(p), p))
        node_index = {node: i for i, node in enumerate(nodes)}
        edges = _build_module_edges(modules)
        sorted_paths = stable_toposort(nodes, edges, key=node_index.__getitem__)

        for path_tuple in sorted_paths:
            generated_module_name = _path_to_module_name(package_name, path_tuple)
            module = types.ModuleType(generated_module_name)
            module.__dict__["__builtins__"] = builtins.__dict__
            module.__package__ = (
                generated_module_name if _is_init_file(path_tuple) else ".".join(generated_module_name.split(".")[:-1])
            )
            register_module(generated_module_name, module)
            all_namespaces[generated_module_name] = module.__dict__

        if package_name not in sys.modules:
            pkg = types.ModuleType(package_name)
            pkg.__path__ = []
            pkg.__package__ = package_name
            register_module(package_name, pkg)

        for path_tuple in sorted_paths:
            generated_module_name = _path_to_module_name(package_name, path_tuple)
            exec(modules[path_tuple], all_namespaces[generated_module_name])  # noqa: S102

        models: dict[str, type] = {}
        combined_namespace: dict[str, Any] = {}
        for path_tuple, ns in zip(sorted_paths, all_namespaces.values(), strict=True):
            combined_namespace.update(ns)
            models.update(_extract_models(ns, code=modules[path_tuple], include_private=include_private_models))

        for obj in models.values():
            if issubclass(obj, BaseModel) and hasattr(obj, "__pydantic_generic_metadata__"):
                obj.model_rebuild(_types_namespace=combined_namespace)

        return models
    finally:
        for created_module_name in reversed(created_modules):
            previous_module = previous_modules[created_module_name]
            if previous_module is _MISSING_MODULE:
                sys.modules.pop(created_module_name, None)
            else:
                sys.modules[created_module_name] = cast("types.ModuleType", previous_module)


def _should_extract_model_name(name: str, *, include_private: bool = False) -> bool:
    if include_private:
        return True
    return not name.startswith("_")


def _extract_models(namespace: dict[str, Any], *, code: str = "", include_private: bool = False) -> dict[str, type]:
    """Extract model and enum classes from namespace."""
    match namespace.get("__name__"):
        case str() as module_name:
            pass
        case _:
            module_name = "builtins"

    models: dict[str, type] = {}
    assigned_names: set[str] | None = None
    for name, obj in namespace.items():
        if not isinstance(obj, type):
            continue
        if obj.__module__ != module_name:
            if not (
                issubclass(obj, BaseModel)
                and obj.__pydantic_root_model__
                and obj.__pydantic_generic_metadata__["origin"] is not None
            ):
                continue
            # Specialized RootModel classes belong to Pydantic; admit only generated assignments, not imports.
            if assigned_names is None:
                assigned_names = {
                    target.id
                    for statement in ast.parse(code).body
                    if isinstance(statement, ast.Assign)
                    for target in statement.targets
                    if isinstance(target, ast.Name)
                }
            if name not in assigned_names:
                continue
        if _should_extract_model_name(name, include_private=include_private) and (
            (issubclass(obj, BaseModel) and obj is not BaseModel) or (issubclass(obj, Enum) and obj is not Enum)
        ):
            models[name] = obj
    return models


def _make_cache_key(schema: Mapping[str, Any], config: GenerateConfig, module_name: str | None = None) -> str | None:
    """Create cache key from schema and config.

    Returns None if the schema is not JSON-serializable.
    """
    try:
        schema_json = json.dumps(dict(schema), separators=(",", ":"))
        config_json = config.model_dump_json(exclude_defaults=True)
        module_name_json = (
            json.dumps({"module_name": module_name}, sort_keys=True, separators=(",", ":"))
            if module_name is not None
            else None
        )
    except (TypeError, ValueError):
        return None
    schema_digest = hashlib.sha256(schema_json.encode()).hexdigest()
    config_digest = hashlib.sha256(config_json.encode()).hexdigest()
    cache_key = f"{schema_digest}:{len(schema_json)}:{config_digest}:{len(config_json)}"
    if module_name_json is None:
        return cache_key
    module_name_digest = hashlib.sha256(module_name_json.encode()).hexdigest()
    return f"{cache_key}:{module_name_digest}:{len(module_name_json)}"


def _detect_schema_input_file_type(input_: Mapping[str, Any]) -> InputFileType:
    """Detect schema input type for dynamic model generation."""
    if is_asyncapi(input_):
        return InputFileType.AsyncAPI
    if is_openapi(input_):
        return InputFileType.OpenAPI
    return InputFileType.JsonSchema


def _normalize_target_model_names(target_model_names: Sequence[str] | None) -> tuple[str, ...] | None:
    if target_model_names is None:
        return None
    if isinstance(target_model_names, str):
        msg = "target_model_names must be a sequence of model names, not a string."
        raise Error(msg)

    target_names = tuple(target_model_names)
    if not target_names:
        msg = "target_model_names must contain at least one model name."
        raise Error(msg)

    invalid_names = [name for name in target_names if not isinstance(name, str) or not name]
    if invalid_names:
        invalid_values = ", ".join(repr(name) for name in invalid_names)
        msg = f"target_model_names contains invalid model names: {invalid_values}."
        raise Error(msg)

    return target_names


def _filter_target_models(models: dict[str, type], target_model_names: tuple[str, ...] | None) -> dict[str, type]:
    if target_model_names is None:
        return models

    missing_names = [name for name in target_model_names if name not in models]
    if missing_names:
        requested_models = ", ".join(repr(name) for name in missing_names)
        available_models = ", ".join(sorted(models)) or "<none>"
        msg = (
            f"Generated dynamic models do not include requested target_model_names: {requested_models}. "
            f"Available models: {available_models}."
        )
        raise Error(msg)

    return {name: models[name] for name in target_model_names}


def generate_dynamic_models(
    input_: Mapping[str, Any],
    *,
    config: GenerateConfig | None = None,
    cache_size: int = 128,
    module_name: str | None = None,
    target_model_names: Sequence[str] | None = None,
) -> dict[str, type]:
    """Generate actual Python model classes from schema at runtime.

    This function creates real Python classes from JSON Schema or OpenAPI schemas
    using Pydantic's model creation. The generated models can be used immediately
    for validation and data processing.

    Args:
        input_: JSON Schema or OpenAPI schema as dict.
        config: A GenerateConfig object with generation options. If None, uses defaults.
        cache_size: Maximum number of schemas to cache. Set to 0 to disable caching.
        module_name: Optional module/package name to assign to generated classes.
        target_model_names: Optional model names to include in the returned dictionary.

    Returns:
        Dictionary mapping class names to model classes.

    Note:
        - Thread-safe (uses internal lock and cache)
        - Pydantic v2 only (v1 is not supported)
        - Not pickle-able (use model_dump() to serialize instances)
        - Cached by schema + config + module_name hash with FIFO eviction when cache_size is exceeded
        - Supports both single-module and multi-module output

    Example:
        >>> schema = {
        ...     "type": "object",
        ...     "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        ...     "required": ["name"],
        ... }
        >>> models = generate_dynamic_models(schema)
        >>> User = models["Model"]
        >>> user = User(name="John", age=30)
        >>> user.model_dump()
        {'name': 'John', 'age': 30}
    """
    if pydantic.VERSION < "2.0.0":  # pragma: no cover
        msg = f"generate_dynamic_models requires Pydantic v2, found v{pydantic.VERSION}"
        raise Error(msg)

    _rebuild_generate_config()

    if config is None:
        config = GenerateConfig(
            input_file_type=_detect_schema_input_file_type(input_),
            output_model_type=DataModelType.PydanticV2BaseModel,
        )
    elif config.input_file_type == InputFileType.Auto:
        config = config.model_copy(update={"input_file_type": _detect_schema_input_file_type(input_)})

    normalized_target_model_names = _normalize_target_model_names(target_model_names)
    cache_key = _make_cache_key(input_, config, module_name)
    use_cache = cache_size > 0 and cache_key is not None

    with _dynamic_models_lock:
        if use_cache:
            assert cache_key is not None
            if (cached_models := _dynamic_models_cache.get(cache_key)) is not None:
                if (excess := len(_dynamic_models_cache) - cache_size) > 0:
                    _evict_dynamic_models_cache_entries(excess)
                return _filter_target_models(cached_models, normalized_target_model_names)

        result = generate(input_=input_, config=config)
        if result is None:  # pragma: no cover
            msg = "generate() returned None"
            raise Error(msg)
        include_private_models = config.allow_leading_underscore_class_name
        models = (
            _execute_single_module(result, include_private_models=include_private_models, module_name=module_name)
            if isinstance(result, str)
            else _execute_multi_module(result, include_private_models=include_private_models, module_name=module_name)
        )

        if use_cache:
            if (excess := len(_dynamic_models_cache) - cache_size + 1) > 0:
                _evict_dynamic_models_cache_entries(excess)
            _dynamic_models_cache[cache_key] = models

        return _filter_target_models(models, normalized_target_model_names)


def clear_dynamic_models_cache() -> int:
    """Clear the dynamic models cache.

    Returns:
        Number of cached entries that were cleared.
    """
    with _dynamic_models_lock:
        count = len(_dynamic_models_cache)
        _dynamic_models_cache.clear()
        return count
