"""Code formatting utilities and Python version handling.

Provides CodeFormatter for applying black, isort, and ruff formatting,
along with PythonVersion enum and DatetimeClassType for output configuration.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # noqa: S404
import sys
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module, invalidate_caches
from importlib.abc import Loader
from importlib.machinery import ModuleSpec, PathFinder
from importlib.util import source_from_cache
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any
from warnings import warn
from weakref import ReferenceType, WeakKeyDictionary, ref

from typing_extensions import Sentinel

from datamodel_code_generator import _format_types
from datamodel_code_generator.deprecations import warn_deprecated, warn_legacy_dependency
from datamodel_code_generator.util import load_toml

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import CodeType

DEFAULT_FORMATTERS = _format_types.DEFAULT_FORMATTERS
EXTERNAL_FORMATTERS = _format_types.EXTERNAL_FORMATTERS
DateClassType = _format_types.DateClassType
DatetimeClassType = _format_types.DatetimeClassType
Formatter = _format_types.Formatter
PythonVersion = _format_types.PythonVersion
PythonVersionMin = _format_types.PythonVersionMin


DEFAULT_LINE_LENGTH = 88
DEFAULT_KNOWN_FIRST_PARTY = frozenset({"datamodel_code_generator", "tests"})
MAX_TOP_LEVEL_BLANK_LINES = 2
MAX_SHORT_DEFAULT_OVERFLOW = 13
LONG_TARGET_PREFIX_LENGTH = 30
TYPE_ALIAS_INLINE_ARGUMENT_COUNT = 2
STRING_PREFIX_PATTERN = re.compile(r"(?i)^([rubf]*)(\"\"\"|'''|\"|')")


@dataclass(frozen=True, slots=True)
class _WatchFormatterState:
    generation: ReferenceType[object]
    module_names: tuple[str, ...]


_WATCH_FORMATTER_STATES: WeakKeyDictionary[ModuleType, _WatchFormatterState] = WeakKeyDictionary()


def _module_source_path(module: ModuleType) -> Path | None:
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        return None
    path = Path(module_file)
    if path.suffix == ".pyc":
        try:
            path = Path(source_from_cache(str(path)))
        except ValueError:
            return None
    return path.resolve(strict=False)


def _local_package_modules(module: ModuleType, *, previous_modules: frozenset[str]) -> tuple[str, ...]:
    package_paths = getattr(module, "__path__", None)
    if package_paths is None:
        return (module.__name__,)
    roots = tuple(Path(path).resolve(strict=False) for path in package_paths)
    prefix = f"{module.__name__}."
    loaded_modules = sys.modules.copy()
    local_modules = tuple(
        name
        for name, candidate in loaded_modules.items()
        if name not in previous_modules
        and name.startswith(prefix)
        and isinstance(candidate, ModuleType)
        and (source_path := _module_source_path(candidate)) is not None
        and any(source_path.is_relative_to(root) for root in roots)
    )
    return (module.__name__, *local_modules)


def _record_watch_module_candidates(module_name: str, watch_dependencies: Any) -> None:
    relative_module = Path(*module_name.split("."))
    candidate_roots = [Path.cwd()]
    if python_path := os.environ.get("PYTHONPATH"):
        candidate_roots.extend(Path(path) for path in python_path.split(os.pathsep) if path)
    for root in dict.fromkeys(candidate_roots):
        watch_dependencies.record_local_dependency(root / relative_module.with_suffix(".py"))
        watch_dependencies.record_local_dependency(root / relative_module / "__init__.py")


def _prepare_watch_module(module: ModuleType) -> tuple[ModuleType, CodeType] | None:
    """Build and compile a fresh module without mutating interpreter state."""
    spec = module.__spec__
    loader = spec.loader if spec is not None else None
    get_source = getattr(loader, "get_source", None)
    if spec is None or not callable(get_source):
        return None
    try:
        source = get_source(module.__name__)
    except (ImportError, OSError):
        return None
    if source is None:
        return None
    module_path = spec.origin or module.__file__ or module.__name__
    replacement = ModuleType(module.__name__)
    replacement.__file__ = module_path
    replacement.__package__ = spec.parent
    replacement.__loader__ = loader
    replacement.__spec__ = spec
    replacement.__dict__["__cached__"] = spec.cached
    if spec.submodule_search_locations is not None:
        replacement.__path__ = list(spec.submodule_search_locations)
    return replacement, compile(source, module_path, "exec")


class _WatchSourceLoader(Loader):
    """Load a watched package child from its source instead of its bytecode cache."""

    def __init__(self, loader: Loader, origin: str | None) -> None:
        self._loader = loader
        self._origin = origin

    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        create_module = getattr(self._loader, "create_module", None)
        return create_module(spec) if callable(create_module) else None

    def exec_module(self, module: ModuleType) -> None:
        get_source = getattr(self._loader, "get_source", None)
        if callable(get_source) and (source := get_source(module.__name__)) is not None:
            module_path = self._origin or module.__file__ or module.__name__
            exec(compile(source, module_path, "exec"), module.__dict__)  # noqa: S102
            return
        exec_module = getattr(self._loader, "exec_module", None)
        if callable(exec_module):
            exec_module(module)
            return
        msg = f"cannot load module {module.__name__!r}: get_source() returned None and exec_module() is unavailable"
        raise ImportError(msg)


class _WatchSourceFinder:
    """Wrap only current-package child imports with source-first loading."""

    def __init__(self, package_name: str) -> None:
        self._prefix = f"{package_name}."

    def find_spec(
        self, fullname: str, path: Sequence[str] | None = None, target: ModuleType | None = None
    ) -> ModuleSpec | None:
        if not fullname.startswith(self._prefix) or (spec := PathFinder.find_spec(fullname, path, target)) is None:
            return None
        if spec.loader is not None and callable(getattr(spec.loader, "get_source", None)):
            spec.loader = _WatchSourceLoader(spec.loader, spec.origin)
        return spec


def _bind_watch_module(module: ModuleType) -> None:
    """Publish a refreshed module on its parent package when one exists."""
    parent_name, _, child_name = module.__name__.rpartition(".")
    if parent_name and (parent_module := sys.modules.get(parent_name)) is not None:
        setattr(parent_module, child_name, module)


def _fresh_watch_module(module: ModuleType) -> ModuleType:
    """Execute a watch dependency module from source before atomically replacing it."""
    if (prepared := _prepare_watch_module(module)) is None:
        return module
    replacement, code = prepared

    from datamodel_code_generator import PROCESS_STATE_LOCK  # noqa: PLC0415

    with PROCESS_STATE_LOCK:
        exec(code, replacement.__dict__)  # noqa: S102
        sys.modules[module.__name__] = replacement
        _bind_watch_module(replacement)
    return replacement


def _restore_watch_package(
    module_name: str,
    modules: dict[str, ModuleType],
    module_namespaces: dict[ModuleType, dict[str, Any]],
    parent_module: object,
    parent_attribute: object,
) -> None:
    """Restore package interpreter state after a failed transactional refresh."""
    prefix = f"{module_name}."
    for loaded_name in tuple(sys.modules.copy()):
        if loaded_name == module_name or loaded_name.startswith(prefix):
            sys.modules.pop(loaded_name, None)
    sys.modules.update(modules)
    for original, namespace in module_namespaces.items():
        original.__dict__.clear()
        original.__dict__.update(namespace)
    parent_name, _, child_name = module_name.rpartition(".")
    if not parent_name or not isinstance(parent_module, ModuleType):
        return
    if parent_attribute is _MISSING_PARENT_ATTRIBUTE:
        parent_module.__dict__.pop(child_name, None)
    else:
        setattr(parent_module, child_name, parent_attribute)


_MISSING_PARENT_ATTRIBUTE = Sentinel("_MISSING_PARENT_ATTRIBUTE")


def _fresh_watch_package(module: ModuleType) -> ModuleType:
    """Refresh a complete formatter package as one interpreter-state transaction."""
    if (prepared := _prepare_watch_module(module)) is None:
        return module
    replacement, code = prepared

    from datamodel_code_generator import PROCESS_STATE_LOCK  # noqa: PLC0415

    with PROCESS_STATE_LOCK:
        prefix = f"{module.__name__}."
        loaded_modules = sys.modules.copy()
        original_modules = {
            loaded_name: loaded_module
            for loaded_name, loaded_module in loaded_modules.items()
            if loaded_name == module.__name__ or loaded_name.startswith(prefix)
        }
        module_namespaces = {
            loaded_module: loaded_module.__dict__.copy()
            for loaded_module in original_modules.values()
            if isinstance(loaded_module, ModuleType)
        }
        parent_name, _, child_name = module.__name__.rpartition(".")
        parent_module = loaded_modules.get(parent_name)
        parent_attribute = (
            parent_module.__dict__.get(child_name, _MISSING_PARENT_ATTRIBUTE)
            if isinstance(parent_module, ModuleType)
            else _MISSING_PARENT_ATTRIBUTE
        )
        finder = _WatchSourceFinder(module.__name__)
        try:
            invalidate_caches()
            for loaded_name in original_modules:
                sys.modules.pop(loaded_name, None)
            sys.modules[module.__name__] = replacement
            _bind_watch_module(replacement)
            sys.meta_path.insert(0, finder)
            try:
                exec(code, replacement.__dict__)  # noqa: S102
            finally:
                if finder in sys.meta_path:
                    sys.meta_path.remove(finder)
        except BaseException:
            _restore_watch_package(
                module.__name__,
                original_modules,
                module_namespaces,
                parent_module,
                parent_attribute,
            )
            raise
    return replacement


def _load_watch_formatter_module(module_name: str, watch_dependencies: Any) -> ModuleType:
    """Load or source-refresh a formatter once per watch generation."""
    _record_watch_module_candidates(module_name, watch_dependencies)
    generation = watch_dependencies.collector_identity()

    from datamodel_code_generator import PROCESS_STATE_LOCK  # noqa: PLC0415

    with PROCESS_STATE_LOCK:
        module = sys.modules.get(module_name)
        if module is None:
            previous_modules = frozenset(sys.modules.copy())
            module = import_module(module_name)
            module_names = _local_package_modules(module, previous_modules=previous_modules)
        else:
            state = _WATCH_FORMATTER_STATES.get(module)
            if state is not None and state.generation() is generation:
                module_names = state.module_names
            else:
                module = (
                    _fresh_watch_package(module)
                    if getattr(module, "__path__", None) is not None
                    else _fresh_watch_module(module)
                )
                module_names = _local_package_modules(module, previous_modules=frozenset())
        _WATCH_FORMATTER_STATES[module] = _WatchFormatterState(ref(generation), module_names)
    for tracked_name in module_names:
        watch_dependencies.record_module_dependency(sys.modules.get(tracked_name))
    return module


# Keep the re-export shim visible to auto-fixers without changing star-import behavior.
_BUILTIN_FORMATTER_REEXPORTS: tuple[str, ...] = (
    "_AliasSortCategory",
    "_ImportCategory",
    "DEFAULT_LINE_LENGTH",
    "DEFAULT_KNOWN_FIRST_PARTY",
    "MAX_TOP_LEVEL_BLANK_LINES",
    "MAX_SHORT_DEFAULT_OVERFLOW",
    "LONG_TARGET_PREFIX_LENGTH",
    "TYPE_ALIAS_INLINE_ARGUMENT_COUNT",
    "STRING_PREFIX_PATTERN",
    "_is_valid_builtin_line_length",
    "_line_indent",
    "_indent_first_line",
    "_find_pyproject_toml",
    "_get_builtin_line_length",
    "_get_builtin_known_first_party",
    "_get_builtin_string_normalization",
    "_format_alias",
    "_alias_imported_name",
    "_alias_sort_key",
    "_format_from_import",
    "_import_category",
    "_has_inline_comment",
    "_format_import_node_without_reordering",
    "_import_node_category",
    "_format_import_node",
    "_from_import_key",
    "_modules_with_aliased_imports",
    "_can_merge_from_imports",
    "_iter_aliased_from_import_lines",
    "_import_line_sort_key",
    "_build_builtin_import_block",
    "_is_name_or_attr",
    "_is_type_checking_if",
    "_source_segment",
    "_inline_source_segment",
    "_format_call_argument",
    "_format_dict_literal",
    "_format_list_literal",
    "_split_escaped_string_literal",
    "_format_wrapped_string_literal",
    "_format_call_argument_for_block",
    "_format_call",
    "_format_constrained_call",
    "_is_call",
    "_has_attribute_root",
    "_is_datetime_module_call",
    "_is_annotated",
    "_is_list_of_annotated",
    "_is_union",
    "_CONSTRAINED_CALL_NAMES",
    "_is_constrained_string_call",
    "_is_constrained_call",
    "_contains_constrained_string_call",
    "_contains_annotated",
    "_contains_list_of_annotated",
    "_is_simple_union_annotation",
    "_should_format_constrained_call_union",
    "_can_parenthesize_field_value",
    "_should_format_union_annotation",
    "_iter_bit_or_elements",
    "_format_bit_or_element",
    "_format_bit_or_elements",
    "_format_parenthesized_bit_or_annotation",
    "_should_format_field_bit_or_annotation_assignment",
    "_should_format_field_bit_or_value_assignment",
    "_format_parenthesized_field_value",
    "_should_format_string_bit_or_annotation_assignment",
    "_format_bit_or_annotation_assignment",
    "_iter_subscript_elements",
    "_format_annotated",
    "_config_dict_assignment",
    "_format_generated_annotation_assignment",
    "_format_generated_class_statement",
    "_format_constrained_call_union",
    "_format_annotated_union",
    "_format_list_of_annotated",
    "_format_union_subscript",
    "_format_subscript_value",
    "_format_type_alias_type_call",
    "_format_type_alias_union_assignment",
    "_format_typed_dict_call",
    "_is_root_model_constrained_union",
    "_format_root_model_constrained_union_base",
    "_format_root_model_union_base",
    "_format_generated_class_definition",
    "_format_generated_module_statement",
    "_format_type_checking_block",
    "_LineReplacement",
    "_collect_builtin_replacements",
    "_module_docstring_node",
    "_docstring_node",
    "_leading_lines_before_imports",
    "_iter_module_import_nodes",
    "_apply_line_replacements",
    "_previous_non_empty_line_index",
    "_ensure_post_class_annotation_assignment_spacing",
    "_normalize_top_level_blank_lines",
    "_normalize_string_quotes",
    "_finalize_builtin_code",
)


@lru_cache(maxsize=1)
def _get_builtin_formatter_module() -> Any:
    from datamodel_code_generator import _builtin_formatter  # noqa: PLC0415

    return _builtin_formatter


def _builtin_formatter_attr(name: str) -> Any:
    return getattr(_get_builtin_formatter_module(), name)


def _builtin_formatter_global(name: str) -> Any:
    return getattr(sys.modules[__name__], name)


def __getattr__(name: str) -> Any:
    if name not in _BUILTIN_FORMATTER_REEXPORTS:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    value = _builtin_formatter_attr(name)
    globals()[name] = value
    return value


@lru_cache(maxsize=1)
def _get_black() -> Any:
    import black as _black  # noqa: PLC0415

    return _black


@lru_cache(maxsize=1)
def _get_black_mode() -> Any:  # pragma: no cover
    black = _get_black()
    try:
        import black.mode  # noqa: PLC0415
    except ImportError:
        return None
    else:
        return black.mode


@lru_cache(maxsize=1)
def _get_isort() -> Any:
    import isort as _isort  # noqa: PLC0415

    return _isort


def apply_builtin_formatter(  # noqa: PLR0913
    code: str,
    *,
    line_length: int = DEFAULT_LINE_LENGTH,
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
    wrap_string_literal: bool = False,
    string_normalization: bool = False,
    python_version: PythonVersion | None = None,
) -> str:
    """Apply dependency-free formatting for generated Python code."""
    return _builtin_formatter_attr("apply_builtin_formatter")(
        code,
        line_length=line_length,
        known_first_party=known_first_party,
        wrap_string_literal=wrap_string_literal,
        string_normalization=string_normalization,
        python_version=python_version,
    )


@lru_cache(maxsize=1)
def _get_black_python_version_map() -> dict[PythonVersion, Any]:
    black = _get_black()
    return {
        v: getattr(black.TargetVersion, f"PY{v.name.split('_')[-1]}")
        for v in PythonVersion
        if hasattr(black.TargetVersion, f"PY{v.name.split('_')[-1]}")
    }


def is_supported_in_black(python_version: PythonVersion) -> bool:  # pragma: no cover
    """Check if a Python version is supported by the installed black version."""
    return python_version in _get_black_python_version_map()


def unsupported_black_message(python_version: PythonVersion) -> str:
    """Return the message asking for a newer black when the installed one cannot format the target Python version."""
    return (
        f"Installed black doesn't support Python version {python_version.value}.\n"
        "You have to install a newer black.\n"
        f"Installed black version: {_get_black().__version__}"
    )


def black_find_project_root(sources: Sequence[Path]) -> Path:
    """Find the project root directory for black configuration."""
    from black import find_project_root as _find_project_root  # noqa: PLC0415

    project_root = _find_project_root(tuple(str(s) for s in sources))
    if isinstance(project_root, tuple):
        return project_root[0]
    return project_root  # pragma: no cover


def resolve_use_type_checking_imports(
    use_type_checking_imports: bool | None,  # noqa: FBT001
    *,
    is_multi_module_output: bool,
    formatters: list[Formatter] | None,
    requires_runtime_imports_with_ruff_check: bool,
) -> bool:
    """Resolve the effective TYPE_CHECKING import behavior."""
    if use_type_checking_imports is not None:
        return use_type_checking_imports

    has_ruff_check = bool(formatters) and Formatter.RUFF_CHECK in formatters
    return not (is_multi_module_output and has_ruff_check and requires_runtime_imports_with_ruff_check)


class CodeFormatter:
    """Formats generated code using black, isort, ruff, and custom formatters."""

    def __init__(  # noqa: PLR0912, PLR0913, PLR0914, PLR0915, PLR0917
        self,
        python_version: PythonVersion,
        settings_path: Path | None = None,
        wrap_string_literal: bool | None = None,  # noqa: FBT001
        skip_string_normalization: bool = True,  # noqa: FBT001, FBT002
        known_third_party: list[str] | None = None,
        custom_formatters: list[str] | None = None,
        custom_formatters_kwargs: dict[str, Any] | None = None,
        encoding: str = "utf-8",
        formatters: list[Formatter] | None = None,
        builtin_format_line_length: int | None = None,
        use_type_checking_imports: bool = True,  # noqa: FBT001, FBT002
        defer_formatting: bool = False,  # noqa: FBT001, FBT002
        formatter_cwd: Path | None = None,
    ) -> None:
        """Initialize code formatter with configuration for black, isort, ruff, and custom formatters."""
        if formatters is None:
            warn_deprecated(
                "format.default-formatters",
                stacklevel=2,
            )
            formatters = list(DEFAULT_FORMATTERS)
        elif Formatter.BLACK in formatters or Formatter.ISORT in formatters:
            warn_deprecated("dependency.external-formatters-optional", stacklevel=2)

        if not settings_path:
            settings_path = Path.cwd()
        elif settings_path.is_file():
            settings_path = settings_path.parent
        elif not settings_path.exists():
            for parent in settings_path.parents:
                if parent.exists():
                    settings_path = parent
                    break
            else:
                settings_path = Path.cwd()  # pragma: no cover

        self.settings_path: str = str(settings_path)
        self.formatters = formatters
        self.defer_formatting = defer_formatting
        self.encoding = encoding
        self.use_type_checking_imports = use_type_checking_imports
        self.python_version = python_version
        self._formatting_generated_code = False

        has_external_formatter = bool(EXTERNAL_FORMATTERS.intersection(formatters))
        if Formatter.BUILTIN in formatters and has_external_formatter:
            warn(
                "The built-in formatter is ignored when an external formatter is selected.",
                UserWarning,
                stacklevel=2,
            )
        use_builtin = Formatter.BUILTIN in formatters and not has_external_formatter
        use_black = Formatter.BLACK in formatters
        use_isort = Formatter.ISORT in formatters
        self.use_builtin_formatter = use_builtin

        builtin_tool_config: dict[str, Any] | None = None
        if use_builtin:
            is_valid_builtin_line_length = _builtin_formatter_global("_is_valid_builtin_line_length")
            if builtin_format_line_length is not None and not is_valid_builtin_line_length(builtin_format_line_length):
                msg = "builtin_format_line_length must be a positive integer"
                raise ValueError(msg)
            if (pyproject_toml_path := _builtin_formatter_global("_find_pyproject_toml")(settings_path)) is not None:
                builtin_tool_config = load_toml(pyproject_toml_path).get("tool", {})
            else:
                builtin_tool_config = {}

        self.builtin_line_length = (
            _builtin_formatter_global("_get_builtin_line_length")(
                settings_path,
                builtin_format_line_length,
                tool_config=builtin_tool_config,
            )
            if use_builtin
            else DEFAULT_LINE_LENGTH
        )
        self.builtin_known_first_party = (
            _builtin_formatter_global("_get_builtin_known_first_party")(
                settings_path,
                tool_config=builtin_tool_config,
            )
            if use_builtin
            else DEFAULT_KNOWN_FIRST_PARTY
        )
        self.builtin_wrap_string_literal = bool(wrap_string_literal)
        self.builtin_string_normalization = (
            _builtin_formatter_global("_get_builtin_string_normalization")(
                settings_path,
                skip_string_normalization=skip_string_normalization,
                tool_config=builtin_tool_config,
            )
            if use_builtin
            else False
        )

        if use_black:
            if (black_target_version := _get_black_python_version_map().get(python_version)) is None:
                from datamodel_code_generator import Error  # noqa: PLC0415

                raise Error(unsupported_black_message(python_version))
            root = black_find_project_root((settings_path,))
            path = root / "pyproject.toml"
            if path.is_file():
                pyproject_toml = load_toml(path)
                config = pyproject_toml.get("tool", {}).get("black", {})
            else:
                config = {}

            black = _get_black()
            warn_legacy_dependency("dependency.black-minimum", black.__version__, (24, 3, 0))
            black_mode = _get_black_mode()

            black_kwargs: dict[str, Any] = {}
            if wrap_string_literal is not None:
                experimental_string_processing = wrap_string_literal
            elif black.__version__ < "24.1.0":  # pragma: no cover
                experimental_string_processing = config.get("experimental-string-processing")
            else:
                experimental_string_processing = config.get("preview", False) and (  # pragma: no cover
                    config.get("unstable", False) or "string_processing" in config.get("enable-unstable-feature", [])
                )

            if experimental_string_processing is not None:  # pragma: no cover
                if black.__version__.startswith("19."):
                    warn(
                        f"black doesn't support `experimental-string-processing` option"
                        f" for wrapping string literal in {black.__version__}",
                        stacklevel=2,
                    )
                elif black.__version__ < "24.1.0":
                    black_kwargs["experimental_string_processing"] = experimental_string_processing
                elif experimental_string_processing:
                    black_kwargs["preview"] = True
                    black_kwargs["unstable"] = config.get("unstable", False)
                    black_kwargs["enabled_features"] = {black_mode.Preview.string_processing}

            self.black_mode = black.FileMode(
                target_versions={black_target_version},
                line_length=config.get("line-length", black.DEFAULT_LINE_LENGTH),
                string_normalization=not skip_string_normalization or not config.get("skip-string-normalization", True),
                **black_kwargs,
            )
        else:
            self.black_mode = None

        if use_isort:
            isort = _get_isort()
            warn_legacy_dependency("dependency.isort-minimum", isort.__version__, (6, 0, 0))
            self.isort_config_kwargs: dict[str, Any] = {}
            if known_third_party:
                self.isort_config_kwargs["known_third_party"] = known_third_party
            if formatter_cwd is not None:
                self.isort_config_kwargs["directory"] = str(formatter_cwd)

            if isort.__version__.startswith("4."):  # pragma: no cover
                self.isort_config = None
            else:
                self.isort_config = isort.Config(settings_path=self.settings_path, **self.isort_config_kwargs)
        else:
            self.isort_config_kwargs = {}
            self.isort_config = None

        self.custom_formatters_kwargs = custom_formatters_kwargs or {}
        self.custom_formatters = self._check_custom_formatters(custom_formatters)

    def _load_custom_formatter(self, custom_formatter_import: str) -> CustomCodeFormatter:
        """Load and instantiate a custom formatter from a module path."""
        try:
            if (watch_dependencies := sys.modules.get("datamodel_code_generator.watch_dependencies")) is not None and (
                watch_dependencies.collector_is_active()
            ):
                import_ = _load_watch_formatter_module(custom_formatter_import, watch_dependencies)
            else:
                import_ = import_module(custom_formatter_import)
        except ImportError as e:
            from datamodel_code_generator import Error  # noqa: PLC0415

            msg = f"Unable to import custom formatter {custom_formatter_import!r}: {e}"
            raise Error(msg) from e

        if not hasattr(import_, "CodeFormatter"):
            msg = f"Custom formatter module `{import_.__name__}` must contains object with name CodeFormatter"
            raise NameError(msg)

        formatter_class = import_.__getattribute__("CodeFormatter")  # noqa: PLC2801

        if not issubclass(formatter_class, CustomCodeFormatter):
            msg = f"The custom module {custom_formatter_import} must inherit from `datamodel-code-generator`"
            raise TypeError(msg)

        return formatter_class(formatter_kwargs=self.custom_formatters_kwargs)

    def _check_custom_formatters(self, custom_formatters: list[str] | None) -> list[CustomCodeFormatter]:
        """Validate and load all custom formatters."""
        if custom_formatters is None:
            return []

        return [self._load_custom_formatter(custom_formatter_import) for custom_formatter_import in custom_formatters]

    def format_code(
        self,
        code: str,
    ) -> str:
        """Apply all configured formatters to the code string."""
        return self._format_code(code, generated=self._formatting_generated_code)

    def _format_generated_code(self, code: str) -> str:
        """Apply formatters to source rendered by the built-in generators."""
        previous_generated = self._formatting_generated_code
        self._formatting_generated_code = True
        try:
            return self.format_code(code)
        finally:
            self._formatting_generated_code = previous_generated

    def _format_code(self, code: str, *, generated: bool) -> str:
        if Formatter.ISORT in self.formatters:
            code = self.apply_isort(code)
        if self.use_builtin_formatter:
            formatter = self.apply_builtin_formatter
            match generated:
                case True:
                    formatter = _builtin_formatter_attr("_apply_builtin_generated_formatter")
                case _:
                    pass
            code = formatter(
                code,
                line_length=self.builtin_line_length,
                known_first_party=self.builtin_known_first_party,
                wrap_string_literal=self.builtin_wrap_string_literal,
                string_normalization=self.builtin_string_normalization,
                python_version=self.python_version,
            )
        if Formatter.BLACK in self.formatters:
            code = self.apply_black(code)

        if not self.defer_formatting:
            has_ruff_check = Formatter.RUFF_CHECK in self.formatters
            has_ruff_format = Formatter.RUFF_FORMAT in self.formatters
            if has_ruff_check and has_ruff_format:
                code = self.apply_ruff_check_and_format(code)
            elif has_ruff_check:
                code = self.apply_ruff_lint(code)
            elif has_ruff_format:
                code = self.apply_ruff_formatter(code)

        for formatter in self.custom_formatters:
            code = formatter.apply(code)

        return code

    def apply_black(self, code: str) -> str:
        """Format code using black."""
        black = _get_black()
        return black.format_str(
            code,
            mode=self.black_mode,
        )

    @staticmethod
    def apply_builtin_formatter(  # noqa: PLR0913
        code: str,
        *,
        line_length: int = DEFAULT_LINE_LENGTH,
        known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
        wrap_string_literal: bool = False,
        string_normalization: bool = False,
        python_version: PythonVersion | None = None,
    ) -> str:
        """Format generated code without external formatter dependencies."""
        return apply_builtin_formatter(
            code,
            line_length=line_length,
            known_first_party=known_first_party,
            wrap_string_literal=wrap_string_literal,
            string_normalization=string_normalization,
            python_version=python_version,
        )

    def apply_ruff_lint(self, code: str) -> str:
        """Run ruff check with auto-fix on code."""
        result = self._run_ruff_command(
            self._ruff_check_command("-"),
            stdin=code.encode(self.encoding),
            allow_stdout_on_error=True,
        )
        return result.stdout.decode(self.encoding)

    def apply_ruff_formatter(self, code: str) -> str:
        """Format code using ruff format."""
        ruff_path = self._find_ruff_path()
        result = self._run_ruff_command(
            (ruff_path, "format", "-"),
            stdin=code.encode(self.encoding),
        )
        return result.stdout.decode(self.encoding)

    def apply_ruff_check_and_format(self, code: str) -> str:
        """Run ruff check and format sequentially for reliable processing."""
        ruff_path = self._find_ruff_path()
        check_result = self._run_ruff_command(
            self._ruff_check_command("-", ruff_path=ruff_path),
            stdin=code.encode(self.encoding),
            allow_stdout_on_error=True,
        )
        format_result = self._run_ruff_command(
            (ruff_path, "format", "-"),
            stdin=check_result.stdout,
        )
        return format_result.stdout.decode(self.encoding)

    def _run_ruff_command(
        self,
        command: tuple[str, ...],
        *,
        stdin: bytes | None = None,
        allow_stdout_on_error: bool = False,
    ) -> subprocess.CompletedProcess[bytes]:
        kwargs: dict[str, Any] = {}
        if stdin is not None:
            kwargs["input"] = stdin
        result = subprocess.run(  # noqa: S603
            command,
            capture_output=True,
            check=False,
            cwd=self.settings_path,
            **kwargs,
        )
        if result.returncode == 0 or (allow_stdout_on_error and result.returncode == 1 and result.stdout):
            return result
        if (message := result.stderr.decode(self.encoding, errors="replace").strip()) or (
            message := result.stdout.decode(self.encoding, errors="replace").strip()
        ):
            detail = message
        else:
            detail = "no output"
        msg = f"Ruff command failed with exit code {result.returncode}: {' '.join(command)}\n{detail}"
        raise RuntimeError(msg)

    def _ruff_check_command(self, *paths: str, ruff_path: str | None = None) -> tuple[str, ...]:
        """Build the Ruff check command for the current formatter settings."""
        if ruff_path is None:
            ruff_path = self._find_ruff_path()
        command: tuple[str, ...] = (ruff_path, "check", "--fix", "--unsafe-fixes")
        if not self.use_type_checking_imports:
            command += ("--unfixable", "TC001,TC002,TC003")
        return (*command, *paths)

    @staticmethod
    def _find_ruff_path() -> str:
        """Find ruff executable path, checking virtual environment first."""
        bin_dir = Path(sys.executable).parent
        ruff_name = "ruff.exe" if sys.platform == "win32" else "ruff"
        ruff_in_venv = bin_dir / ruff_name
        if ruff_in_venv.exists():
            return str(ruff_in_venv)
        if ruff_path := shutil.which("ruff"):
            return ruff_path
        msg = "Ruff executable was not found. Install it with `pip install 'datamodel-code-generator[ruff]'`."
        raise RuntimeError(msg)

    def apply_isort(self, code: str) -> str:
        """Sort imports using isort."""
        isort = _get_isort()
        if self.isort_config is None:  # pragma: no cover
            return isort.SortImports(
                file_contents=code,
                settings_path=self.settings_path,
                **self.isort_config_kwargs,
            ).output
        return isort.code(code, config=self.isort_config)

    def format_directory(self, directory: Path) -> None:
        """Apply ruff formatting to all Python files in a directory."""
        ruff_path = self._find_ruff_path()
        if Formatter.RUFF_CHECK in self.formatters:
            self._run_ruff_command(
                self._ruff_check_command(str(directory), ruff_path=ruff_path),
            )
        if Formatter.RUFF_FORMAT in self.formatters:
            self._run_ruff_command(
                (ruff_path, "format", str(directory)),
            )


class CustomCodeFormatter:
    """Base class for custom code formatters.

    Subclasses must implement the apply() method to transform code.
    """

    def __init__(self, formatter_kwargs: dict[str, Any]) -> None:
        """Initialize custom formatter with optional keyword arguments."""
        self.formatter_kwargs = formatter_kwargs

    def apply(self, code: str) -> str:
        """Apply formatting to code. Must be implemented by subclasses."""
        raise NotImplementedError
