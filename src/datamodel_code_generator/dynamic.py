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
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, Any, TypeVar, cast

import pydantic
from pydantic import BaseModel

from datamodel_code_generator import (
    Error,
    _prepare_generate_facade_config,
    _run_generation_with_metadata,
    generate,
    is_asyncapi,
    is_openapi,
)
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._module_paths import dotted_module, is_package_init, module_parts
from datamodel_code_generator._process_state import ABSENT, PROCESS_STATE_LOCK, restore_entry
from datamodel_code_generator.config import GenerateConfig, _rebuild_generate_config
from datamodel_code_generator.enums import DataModelType, InputFileType
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.reference import _is_special_path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from datamodel_code_generator import GeneratedModules
    from datamodel_code_generator.model_metadata import ModelMetadata


@dataclass(frozen=True, eq=False, slots=True, kw_only=True)
class DynamicModule:
    """The classes generated from one schema, the schema location each comes from, and the module they live in."""

    models: dict[str, type]
    """Class names mapped to the classes, like the dictionary generate_dynamic_models() returns, in a cache entry of
    its own."""
    root_class: type | None
    """The class generated for the document itself (location ``#``), or None."""
    models_by_source_ref: Mapping[str, type]
    """A read-only map from a schema location (the ``source_ref`` of ``--emit-model-metadata``) to its class."""
    module_name: str | None
    """The module (the package for split output) the classes were executed in; None for an unnamed single module."""


_CachedT = TypeVar("_CachedT", dict[str, type], DynamicModule)

_dynamic_models_cache: dict[str, dict[str, type] | DynamicModule] = {}
_dynamic_models_lock = threading.Lock()
_dynamic_module_counter = itertools.count(1)


def _evict_dynamic_models_cache_entries(count: int) -> None:
    for _ in range(count):
        del _dynamic_models_cache[next(iter(_dynamic_models_cache))]


def _cached(cache_key: str | None, cache_size: int, build: Callable[[], _CachedT]) -> _CachedT:
    """Return the entry cached under ``cache_key``, or build one and cache it, keeping at most ``cache_size`` entries.

    The oldest entries go first. The caller holds ``_dynamic_models_lock``.
    """
    if cache_size <= 0 or cache_key is None:
        return build()
    if (cached := _dynamic_models_cache.get(cache_key)) is not None:
        _evict_dynamic_models_cache_entries(len(_dynamic_models_cache) - cache_size)
        return cast("_CachedT", cached)
    result = build()
    _evict_dynamic_models_cache_entries(len(_dynamic_models_cache) - cache_size + 1)
    _dynamic_models_cache[cache_key] = result
    return result


def _execute_single_module(
    code: str,
    *,
    include_private_models: bool = False,
    module_name: str | None = None,
) -> dict[str, type]:
    """Execute single module code and extract models.

    A named module is registered while its code runs, unless a module of that name exists, because Pydantic looks
    up the module that parameterizes a generic model. A module of that name that can be imported is imported first,
    so that the code imports from it, not from a registered empty module.
    """
    namespace: dict[str, Any] = {"__builtins__": builtins.__dict__}
    if module_name is None:
        return _run_module_code(code, namespace, include_private_models=include_private_models)
    namespace |= {"__name__": module_name, "__package__": module_name.rpartition(".")[0]}
    if module_name in sys.modules or _import_module_if_found(module_name):
        return _run_module_code(code, namespace, include_private_models=include_private_models)
    module = sys.modules[module_name] = types.ModuleType(module_name)
    module.__dict__.update(namespace)
    try:
        return _run_module_code(code, module.__dict__, include_private_models=include_private_models)
    finally:
        sys.modules.pop(module_name, None)


def _import_module_if_found(module_name: str) -> bool:
    """Import a module that is not imported yet, and return whether it could be found."""
    try:
        spec = find_spec(module_name)
    except ModuleNotFoundError:
        return False
    if spec is None:
        return False
    import_module(module_name)
    return True


def _run_module_code(code: str, namespace: dict[str, Any], *, include_private_models: bool) -> dict[str, type]:
    """Run module code in ``namespace`` and extract its models."""
    exec(code, namespace)  # noqa: S102

    models = _extract_models(namespace, code=code, include_private=include_private_models)

    for obj in models.values():
        if issubclass(obj, BaseModel):
            obj.model_rebuild(_types_namespace=namespace)

    return models


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
        if PurePath(path[-1]).suffix == ".py":
            module_paths[module_parts(path)] = path

    edges: dict[tuple[str, ...], set[tuple[str, ...]]] = {path: set() for path in modules}
    for path, code in modules.items():
        for dep_path in _get_relative_import_paths(path, code, module_paths):
            if dep_path != path:
                edges[dep_path].add(path)
    return edges


def _dynamic_package_name(module_name: str | None) -> str:
    """Return the package to execute split output in: ``module_name``, or a new name of this process."""
    return module_name or f"_dcg_dynamic_{next(_dynamic_module_counter)}"


def _execute_multi_module(
    modules: dict[tuple[str, ...], str],
    *,
    include_private_models: bool = False,
    module_name: str | None = None,
    models_by_module: dict[str, dict[str, type]] | None = None,
) -> dict[str, type]:
    """Execute multiple modules and extract models.

    ``models_by_module`` receives the models of each module, by its dotted name below the package.
    """
    package_name = _dynamic_package_name(module_name)

    created_modules: list[str] = []
    previous_modules: dict[str, types.ModuleType | object] = {}
    all_namespaces: dict[str, dict[str, Any]] = {}

    def register_module(name: str, module: types.ModuleType) -> None:
        previous_modules[name] = sys.modules.get(name, ABSENT)
        sys.modules[name] = module
        created_modules.append(name)

    try:
        nodes = list(modules.keys())
        nodes.sort(key=lambda p: (is_package_init(p[-1]), p))
        node_index = {node: i for i, node in enumerate(nodes)}
        edges = _build_module_edges(modules)
        sorted_paths = stable_toposort(nodes, edges, key=node_index.__getitem__)

        for path_tuple in sorted_paths:
            generated_module_name = dotted_module(path_tuple, package_name)
            module = types.ModuleType(generated_module_name)
            module.__dict__["__builtins__"] = builtins.__dict__
            module.__package__ = (
                generated_module_name if is_package_init(path_tuple[-1]) else generated_module_name.rpartition(".")[0]
            )
            register_module(generated_module_name, module)
            all_namespaces[generated_module_name] = module.__dict__

        if package_name not in sys.modules:
            pkg = types.ModuleType(package_name)
            pkg.__path__ = []
            pkg.__package__ = package_name
            register_module(package_name, pkg)

        for path_tuple in sorted_paths:
            generated_module_name = dotted_module(path_tuple, package_name)
            exec(modules[path_tuple], all_namespaces[generated_module_name])  # noqa: S102

        models: dict[str, type] = {}
        combined_namespace: dict[str, Any] = {}
        for path_tuple, ns in zip(sorted_paths, all_namespaces.values(), strict=True):
            combined_namespace.update(ns)
            module_models = _extract_models(ns, code=modules[path_tuple], include_private=include_private_models)
            models.update(module_models)
            if models_by_module is not None:
                models_by_module[dotted_module(path_tuple)] = module_models

        for obj in models.values():
            if issubclass(obj, BaseModel):
                obj.model_rebuild(_types_namespace=combined_namespace)

        return models
    finally:
        for created_module_name in reversed(created_modules):
            restore_entry(sys.modules, created_module_name, previous_modules[created_module_name])


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
        if not include_private and name.startswith("_"):
            continue
        if (issubclass(obj, BaseModel) and obj is not BaseModel) or (issubclass(obj, Enum) and obj is not Enum):
            models[name] = obj
    return models


def _make_cache_key(schema: Mapping[str, Any], config: GenerateConfig, module_name: str | None = None) -> str | None:
    """Create cache key from schema and config.

    The values of set-valued settings are sorted, because a set has no order, so the key does not depend on the hash
    seed. Returns None if the schema or the config is not JSON-serializable.
    """
    try:
        schema_json = json.dumps(dict(schema), separators=(",", ":"))
        config_json = json.dumps(
            {
                name: sorted(value) if isinstance(getattr(config, name), (set, frozenset)) else value
                for name, value in config.model_dump(mode="json", exclude_defaults=True).items()
            },
            separators=(",", ":"),
        )
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


def _generate_code(
    input_: Mapping[str, Any], config: GenerateConfig, *, collect_model_metadata: bool
) -> tuple[str | GeneratedModules | None, ModelMetadata | None, tuple[str, ...]]:
    """Return the code generate() writes for ``config``, beside the model metadata and identity paths of the run.

    The metadata are collected only with ``collect_model_metadata``; otherwise they are None and empty.
    """
    if not collect_model_metadata:
        return generate(input_=input_, config=config), None, ()
    config = _prepare_generate_facade_config(config)
    with PROCESS_STATE_LOCK:
        caller_cwd = Path.cwd()
    return _run_generation_with_metadata(input_, config, caller_cwd, use_output_cwd=False, collect_model_metadata=True)


def _models_by_source_ref(entries: Iterable[tuple[str, str, type | None]]) -> dict[str, type]:
    """Map each schema location to the class generated from it.

    ``entries`` are ``(source_ref, identity, class)`` for every generated model: its location, the reference path it
    was parsed under, and its class, or None when it is no class at runtime (a type alias, or one left out of
    ``models``). A location gets the one model at it, or else the one model among them parsed under the location
    itself, which is the model a ``$ref`` to it names; it gets no key when there is no such model or the model has no
    class, so a helper class never stands in for it. Paths that name a place inside a generated model, not in a
    document, are no locations.
    """
    models_at: dict[str, list[tuple[str, type | None]]] = {}
    for source_ref, identity, cls in entries:
        if not _is_special_path(source_ref):
            models_at.setdefault(source_ref, []).append((identity, cls))
    models: dict[str, type] = {}
    for source_ref, at in models_at.items():
        chosen = at if len(at) == 1 else [entry for entry in at if entry[0] == source_ref]
        if len(chosen) == 1 and (cls := chosen[0][1]) is not None:
            models[source_ref] = cls
    return models


def _build_dynamic_module(
    input_: Mapping[str, Any], config: GenerateConfig, *, module_name: str | None, collect_model_metadata: bool
) -> DynamicModule:
    """Generate the code of ``config``, execute it, and return its classes; with ``collect_model_metadata`` by location.

    Without ``collect_model_metadata``, ``models_by_source_ref`` is empty and ``root_class`` None.
    """
    result, metadata, identities = _generate_code(input_, config, collect_model_metadata=collect_model_metadata)
    if result is None:  # pragma: no cover
        msg = "generate() returned None"
        raise Error(msg)
    include_private_models = config.allow_leading_underscore_class_name
    if isinstance(result, str):
        models = _execute_single_module(result, include_private_models=include_private_models, module_name=module_name)
        models_by_module = {"": models}
    else:
        module_name = _dynamic_package_name(module_name)
        models_by_module = {}
        models = _execute_multi_module(
            result,
            include_private_models=include_private_models,
            module_name=module_name,
            models_by_module=models_by_module,
        )
    models_by_source_ref = (
        {}
        if metadata is None
        else _models_by_source_ref(
            (model["source_ref"], identity, models_by_module.get(model["module"], {}).get(model["class_name"]))
            for model, identity in zip(metadata["models"], identities, strict=True)
        )
    )
    return DynamicModule(
        models=models,
        root_class=models_by_source_ref.get("#"),
        models_by_source_ref=types.MappingProxyType(models_by_source_ref),
        module_name=module_name,
    )


def _dynamic_generate_config(input_: Mapping[str, Any], config: GenerateConfig | None) -> GenerateConfig:
    """Return the config of a dynamic call, with the input type detected where ``config`` leaves it to detection.

    Without ``config``, the defaults with Pydantic v2 models.
    """
    if pydantic.VERSION < "2.0.0":  # pragma: no cover
        msg = f"generate_dynamic_models requires Pydantic v2, found v{pydantic.VERSION}"
        raise Error(msg)

    _rebuild_generate_config()

    if config is None:
        return GenerateConfig(
            input_file_type=_detect_schema_input_file_type(input_),
            output_model_type=DataModelType.PydanticV2BaseModel,
        )
    if config.input_file_type == InputFileType.Auto:
        return config.model_copy(update={"input_file_type": _detect_schema_input_file_type(input_)})
    return config


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
        invalid_values = ", ".join(stable_repr(name) for name in invalid_names)
        msg = f"target_model_names contains invalid model names: {invalid_values}."
        raise Error(msg)

    return target_names


def _filter_target_models(models: dict[str, type], target_model_names: tuple[str, ...] | None) -> dict[str, type]:
    if target_model_names is None:
        return models

    missing_names = [name for name in target_model_names if name not in models]
    if missing_names:
        requested_models = ", ".join(stable_repr(name) for name in missing_names)
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
        - generate_dynamic_module() also gives the root class and the class of each schema location

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
    config = _dynamic_generate_config(input_, config)
    normalized_target_model_names = _normalize_target_model_names(target_model_names)
    cache_key = _make_cache_key(input_, config, module_name)

    with _dynamic_models_lock:
        models = _cached(
            cache_key,
            cache_size,
            lambda: _build_dynamic_module(input_, config, module_name=module_name, collect_model_metadata=False).models,
        )
        return _filter_target_models(models, normalized_target_model_names)


def generate_dynamic_module(
    input_: Mapping[str, Any],
    *,
    config: GenerateConfig | None = None,
    cache_size: int = 128,
    module_name: str | None = None,
) -> DynamicModule:
    """Generate model classes from a schema at runtime, with the root class and the class of each schema location.

    Generation, execution and options are those of generate_dynamic_models(); the result also tells which class
    the document itself became and which class each schema location became.

    Args:
        input_: JSON Schema or OpenAPI schema as dict.
        config: A GenerateConfig object with generation options. If None, uses defaults.
        cache_size: Maximum number of schemas to cache. Set to 0 to disable caching.
        module_name: Optional module/package name to assign to generated classes.

    Returns:
        A DynamicModule with the classes by name, the root class, the classes by schema location and the module name.

    Note:
        - Cached like generate_dynamic_models(), in entries of its own: ``models`` is not the dictionary that
          generate_dynamic_models() returns for the same arguments, and its classes are other classes
        - A location is the ``source_ref`` that ``--emit-model-metadata`` writes, such as ``#``, ``#/$defs/User`` or
          ``sub/other.json#/$defs/X``; a location that dcg merged into another class or that several helper classes
          share has no key

    Example:
        >>> schema = {"title": "Root", "type": "object", "properties": {"child": {"$ref": "#/$defs/Root"}}}
        >>> schema["$defs"] = {"Root": {"type": "object", "properties": {"name": {"type": "string"}}}}
        >>> module = generate_dynamic_module(schema)
        >>> module.root_class.__name__, module.models_by_source_ref["#/$defs/Root"].__name__
        ('RootModel', 'Root')
    """
    config = _dynamic_generate_config(input_, config)
    cache_key = _make_cache_key(input_, config, module_name)

    with _dynamic_models_lock:
        return _cached(
            None if cache_key is None else f"module:{cache_key}",
            cache_size,
            lambda: _build_dynamic_module(input_, config, module_name=module_name, collect_model_metadata=True),
        )


def clear_dynamic_models_cache() -> int:
    """Clear the dynamic models cache.

    Returns:
        Number of cached entries that were cleared.
    """
    with _dynamic_models_lock:
        count = len(_dynamic_models_cache)
        _dynamic_models_cache.clear()
        return count
