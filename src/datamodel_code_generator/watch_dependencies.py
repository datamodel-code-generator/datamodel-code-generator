"""Collect lexical and resolved local resources used by one watch-mode generation."""

from __future__ import annotations

import os
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib.util import source_from_cache
from pathlib import Path
from stat import S_ISDIR
from threading import RLock
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

_JSON_CONFIG_FIELDS = frozenset({
    "aliases",
    "base_class_map",
    "client_operations",
    "client_protocols",
    "client_resource_names",
    "custom_formatters_kwargs",
    "default_values",
    "duplicate_name_suffix",
    "enum_field_as_literal_map",
    "extra_template_data",
    "import_overrides",
    "model_name_map",
    "serialization_aliases",
    "server_body_modes",
    "server_handler_modes",
    "server_operation_names",
    "server_parameter_names",
    "server_primary_responses",
    "server_router_names",
    "type_overrides",
    "validators",
})
_TARGET_OUTPUT_FIELDS = ("server_output", "client_output")


class _Weakrefable:
    """Provide a weak-reference slot for slotted state on every supported Python."""

    __slots__ = ("__weakref__",)


class _WatchConfig(Protocol):
    """Configuration attributes needed to stage watch dependencies."""

    input: str | Path | None
    output: Path | None
    emit_model_metadata: Path | None
    custom_file_header_path: Path | None
    custom_template_dir: Path | None
    http_local_ref_path: Path | None


@dataclass(slots=True)
class _CollectedGeneration(_Weakrefable):
    """Private graph additions that become visible only after a generation ends."""

    owner: WatchDependencies
    files: set[Path] = field(default_factory=set)
    symlink_events: set[Path] = field(default_factory=set)
    failed: bool = False
    polling_fingerprints: dict[Path, tuple[int, int, int]] | None = None


@dataclass(frozen=True, slots=True)
class _DependencySnapshot:
    """An immutable dependency graph safely shared with the watcher thread."""

    files: frozenset[Path]
    directories: frozenset[Path]
    event_paths: frozenset[Path]
    output: Path | None
    outputs: dict[Path, bool]
    recovery_paths: frozenset[Path]
    polling_fingerprints: dict[Path, tuple[int, int, int]] | None
    watch_roots: tuple[Path, ...]


@dataclass(slots=True)
class _StaticDependencies:
    """One bounded static candidate awaiting generation publication."""

    files: set[Path] = field(default_factory=set)
    directories: set[Path] = field(default_factory=set)
    symlink_events: set[Path] = field(default_factory=set)
    outputs: dict[Path, bool] = field(default_factory=dict)


_current_collector: ContextVar[_CollectedGeneration | None] = ContextVar("watch_dependencies", default=None)
_MISSING_PATH_FINGERPRINT = (-1, -1, -1)


def _lexical_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))  # noqa: PTH100


def _path_variants(path: Path) -> frozenset[Path]:
    """Keep lexical and resolved locations so symlink retargeting remains observable."""
    lexical_path = _lexical_path(path)
    try:
        parent_resolved_path = lexical_path.parent.resolve(strict=False) / lexical_path.name
        resolved_path = lexical_path.resolve(strict=False)
    except (OSError, ValueError):
        return frozenset({lexical_path})
    return frozenset({lexical_path, parent_resolved_path, resolved_path})


def _symlink_event_paths(path: Path) -> frozenset[Path]:
    """Return symlink ancestors and parents whose events can repoint a lexical path."""
    event_paths: set[Path] = set()
    current = _lexical_path(path)
    while current != current.parent:
        try:
            if current.is_symlink():
                parent = current.parent
                if parent != parent.parent:
                    event_paths.update(_path_variants(current))
                    event_paths.update(_path_variants(parent))
        except OSError:
            break
        current = current.parent
    return frozenset(event_paths)


def _resolved_path(path: Path) -> Path:
    return _lexical_path(path).resolve(strict=False)


def _path_fingerprint(path: Path) -> tuple[int, int, int]:
    try:
        status = path.stat()
    except OSError:
        return _MISSING_PATH_FINGERPRINT
    return status.st_ino, status.st_mtime_ns, status.st_size


def _configured_path(config_values: Mapping[str, Any], field_name: str, fallback: str | Path | None) -> Path | None:
    value = config_values.get(field_name, fallback)
    if isinstance(value, (str, Path)):
        return Path(value)
    return Path(fallback) if isinstance(fallback, str) else fallback


def _logical_working_directory() -> Path:
    working_directory = Path.cwd()
    logical_working_directory = Path(os.environ.get("PWD", working_directory)).expanduser()
    try:
        return logical_working_directory if logical_working_directory.samefile(working_directory) else working_directory
    except OSError:
        return working_directory


def _nearest_existing_directory(path: Path) -> Path:
    while not path.is_dir():
        if path == path.parent:
            return path
        path = path.parent
    return path


def _watch_roots(
    files: frozenset[Path], directories: frozenset[Path], event_paths: frozenset[Path]
) -> tuple[Path, ...]:
    """Return minimal existing roots covering every dependency and recovery event."""
    roots = {_nearest_existing_directory(path.parent) for path in files}
    # Keep configured directory roots stable when the directory itself is
    # removed and recreated. Its parent still receives the relevant event.
    roots.update(_nearest_existing_directory(path.parent) for path in directories)
    roots.update(_nearest_existing_directory(path) for path in event_paths)
    watch_roots: list[Path] = []
    for root in sorted((path for path in roots if path.is_dir()), key=lambda path: (len(path.parts), path.as_posix())):
        if not any(root.is_relative_to(ancestor) for ancestor in watch_roots):
            watch_roots.append(root)
    return tuple(watch_roots)


@dataclass(slots=True)
class WatchDependencies(_Weakrefable):
    """Path-only dependency state for one persistent watch session."""

    _static_files: set[Path] = field(default_factory=set)
    _static_directories: set[Path] = field(default_factory=set)
    _static_symlink_events: set[Path] = field(default_factory=set)
    _generation_files: set[Path] = field(default_factory=set)
    _generation_symlink_events: set[Path] = field(default_factory=set)
    _outputs: dict[Path, bool] = field(default_factory=dict)
    _verified_remote_locks: set[Path] = field(default_factory=set)
    _pending_static: _StaticDependencies | None = None
    _failed_static: _StaticDependencies | None = None
    _failed_generation_files: set[Path] = field(default_factory=set)
    _failed_generation_symlink_events: set[Path] = field(default_factory=set)
    _polling_fingerprints_enabled: bool = False
    _lock: RLock = field(default_factory=RLock)
    _snapshot: _DependencySnapshot = field(init=False)

    def __post_init__(self) -> None:
        """Publish the initial empty immutable graph."""
        self._snapshot = self._create_snapshot()

    def _create_snapshot(self) -> _DependencySnapshot:
        static_files = self._static_files.copy()
        static_directories = self._static_directories.copy()
        static_symlink_events = self._static_symlink_events.copy()
        candidate = self._pending_static or self._failed_static
        if candidate is not None:
            static_files |= candidate.files
            static_directories |= candidate.directories
            static_symlink_events |= candidate.symlink_events
        failed_generation_symlink_events = self._failed_generation_symlink_events
        files = frozenset(static_files | self._generation_files | self._failed_generation_files)
        directories = frozenset(static_directories)
        event_paths = frozenset(
            files | static_symlink_events | self._generation_symlink_events | failed_generation_symlink_events
        )
        watch_roots = _watch_roots(files, directories, event_paths)
        outputs = self._outputs.copy()
        if candidate is not None:
            outputs.update(candidate.outputs)
        recovery_paths = frozenset(
            (candidate.files if candidate is not None else set()) | self._failed_generation_files
        )
        collector = _current_collector.get()
        generation_fingerprints = (
            collector.polling_fingerprints if collector is not None and collector.owner is self else None
        )
        polling_fingerprints = (
            {
                path: generation_fingerprints[path]
                if generation_fingerprints is not None and path in generation_fingerprints
                else _path_fingerprint(path)
                for path in files | directories
            }
            if self._polling_fingerprints_enabled
            else None
        )
        output = next(iter(self._outputs)) if len(self._outputs) == 1 else None
        return _DependencySnapshot(
            files,
            directories,
            event_paths,
            output,
            outputs,
            recovery_paths,
            polling_fingerprints,
            watch_roots,
        )

    def _publish(self) -> None:
        self._snapshot = self._create_snapshot()

    def enable_polling_fingerprints(self) -> None:
        """Track exact dependencies only while a polling watcher needs directory events."""
        with self._lock:
            if not self._polling_fingerprints_enabled:
                self._polling_fingerprints_enabled = True
                self._publish()

    @staticmethod
    def _add_path(
        path: Path,
        paths: set[Path],
        symlink_events: set[Path],
        polling_fingerprints: dict[Path, tuple[int, int, int]] | None = None,
    ) -> None:
        try:
            path_variants = _path_variants(path)
            paths.update(path_variants)
            symlink_events.update(_symlink_event_paths(path))
        except (OSError, ValueError):
            return
        if polling_fingerprints is not None:
            for dependency in path_variants:
                if dependency not in polling_fingerprints:
                    polling_fingerprints[dependency] = _path_fingerprint(dependency)

    @staticmethod
    def _add_output(path: Path | None, outputs: dict[Path, bool], *, is_directory: bool | None = None) -> None:
        """Record an output root with an explicit directory classification when known."""
        if path is None:
            return
        try:
            resolved_path = _resolved_path(path)
        except (OSError, ValueError):
            return
        if is_directory is None:
            try:
                is_directory = S_ISDIR(resolved_path.stat().st_mode)
            except OSError:
                is_directory = not resolved_path.suffix
        outputs[resolved_path] = is_directory

    def _add_config(
        self,
        candidate: _StaticDependencies,
        config: _WatchConfig,
        *,
        config_values: Mapping[str, Any],
        pyproject_path: Path | None,
    ) -> None:
        """Add one validated configuration to a staged static dependency graph."""

        def add_file(path: Path | None) -> None:
            if path is not None:
                self._add_path(path, candidate.files, candidate.symlink_events)

        def add_directory(path: Path | None) -> None:
            if path is not None:
                self._add_path(path, candidate.directories, candidate.symlink_events)

        input_path = _configured_path(config_values, "input", config.input)
        if input_path is not None:
            (add_directory if input_path.is_dir() else add_file)(input_path)
        add_file(pyproject_path or _nearest_pyproject_toml(_logical_working_directory()))
        add_file(_configured_path(config_values, "custom_file_header_path", config.custom_file_header_path))
        add_directory(_configured_path(config_values, "custom_template_dir", config.custom_template_dir))
        add_directory(_configured_path(config_values, "http_local_ref_path", config.http_local_ref_path))
        for field_name in _JSON_CONFIG_FIELDS:
            value = config_values.get(field_name)
            add_file(Path(value) if isinstance(value, (str, Path)) else None)
        self._add_output(config.output, candidate.outputs)
        # Metadata is always a single file, including when its name has no suffix.
        self._add_output(config.emit_model_metadata, candidate.outputs, is_directory=False)
        for field_name in _TARGET_OUTPUT_FIELDS:
            self._add_output(getattr(config, field_name, None), candidate.outputs, is_directory=True)

    def configure(
        self,
        config: _WatchConfig,
        *,
        config_values: Mapping[str, Any],
    ) -> None:
        """Stage a static graph without exposing a generation-sized membership gap."""
        candidate = _StaticDependencies()
        self._add_config(candidate, config, config_values=config_values, pyproject_path=None)
        with self._lock:
            self._pending_static = candidate
            self._publish()

    def configure_many(self, configs: Iterable[tuple[_WatchConfig, Mapping[str, Any], Path]]) -> None:
        """Stage the union of all validated batch-job dependency graphs."""
        candidate = _StaticDependencies()
        for config, config_values, pyproject_path in configs:
            self._add_config(candidate, config, config_values=config_values, pyproject_path=pyproject_path)
        with self._lock:
            self._pending_static = candidate
            self._publish()

    def begin_raw_attempt(self) -> None:
        """Discard an older unvalidated candidate before recording the latest replan."""
        with self._lock:
            self._pending_static = _StaticDependencies()
            self._publish()

    def add_recovery_file(self, path: Path) -> None:
        """Add an unvalidated input to the latest candidate's recovery graph."""
        with self._lock:
            candidate = self._pending_static or _StaticDependencies()
            self._add_path(path, candidate.files, candidate.symlink_events)
            self._pending_static = candidate
            self._publish()

    def stage_raw_config(self, config_values: Mapping[str, Any]) -> None:
        """Stage lexical raw paths before typed config validation can fail."""
        candidate = _StaticDependencies()

        def add_file(value: object) -> None:
            if isinstance(value, (str, Path)):
                self._add_path(Path(value), candidate.files, candidate.symlink_events)

        def add_directory(value: object) -> None:
            if isinstance(value, (str, Path)):
                self._add_path(Path(value), candidate.directories, candidate.symlink_events)

        input_value = config_values.get("input")
        if isinstance(input_value, (str, Path)):
            (add_directory if Path(input_value).is_dir() else add_file)(input_value)
        add_file(config_values.get("custom_file_header_path"))
        add_directory(config_values.get("custom_template_dir"))
        add_directory(config_values.get("http_local_ref_path"))
        for field_name in _JSON_CONFIG_FIELDS:
            add_file(config_values.get(field_name))
        if isinstance(output := config_values.get("output"), (str, Path)):
            self._add_output(Path(output), candidate.outputs)
        if isinstance(metadata := config_values.get("emit_model_metadata"), (str, Path)):
            self._add_output(Path(metadata), candidate.outputs, is_directory=False)
        for field_name in _TARGET_OUTPUT_FIELDS:
            if isinstance(target_output := config_values.get(field_name), (str, Path)):
                self._add_output(Path(target_output), candidate.outputs, is_directory=True)
        with self._lock:
            self._pending_static = candidate
            self._publish()

    @contextmanager
    def generation(self) -> Iterator[_CollectedGeneration]:
        """Collect one generation privately, publishing a complete graph only at its end."""
        collected = _CollectedGeneration(self)
        # A completed generation must acknowledge the inputs it observed, not later edits.
        if self._polling_fingerprints_enabled:
            collected.polling_fingerprints = {path: _path_fingerprint(path) for path in self._snapshot.files}
        token = _current_collector.set(collected)
        try:
            yield collected
        except BaseException:
            with self._lock:
                self._failed_static = self._pending_static
                self._pending_static = None
                self._failed_generation_files = collected.files
                self._failed_generation_symlink_events = collected.symlink_events
                self._publish()
            raise
        else:
            with self._lock:
                if collected.failed:
                    self._failed_static = self._pending_static
                    self._pending_static = None
                    self._failed_generation_files = collected.files
                    self._failed_generation_symlink_events = collected.symlink_events
                    self._publish()
                    return
                if self._pending_static is not None:
                    self._static_files = self._pending_static.files
                    self._static_directories = self._pending_static.directories
                    self._static_symlink_events = self._pending_static.symlink_events
                    self._outputs = self._pending_static.outputs
                    self._pending_static = None
                self._generation_files = collected.files
                self._generation_symlink_events = collected.symlink_events
                self._failed_static = None
                self._failed_generation_files.clear()
                self._failed_generation_symlink_events.clear()
                self._publish()
        finally:
            _current_collector.reset(token)

    def _apply_remote_lock_plans(self, plans: Iterable[Any]) -> tuple[tuple[Any, ...], set[Path]]:
        """Return effective lock plans and intent for the current generation attempt."""
        plans = tuple(plans)
        with self._lock:
            current_paths = {plan.canonical_path for plan in plans}
            candidate_intent = self._verified_remote_locks.intersection(current_paths)
        effective_plans: list[Any] = []
        for plan in plans:
            effective_plan = plan
            if plan.policy == "verify":
                candidate_intent.add(plan.canonical_path)
            elif plan.policy == "inactive" and plan.canonical_path in candidate_intent:
                effective_plan = plan._replace(policy="locked")
            effective_plans.append(effective_plan)
        return tuple(effective_plans), candidate_intent

    def _commit_remote_lock_intent(self, intent: set[Path]) -> None:
        """Persist implicit lock verification only after a successful attempt."""
        with self._lock:
            self._verified_remote_locks = intent.copy()

    def _merge_remote_lock_intent(self, intent: set[Path]) -> None:
        """Retain lock paths verified by a failed candidate for fail-closed recovery."""
        with self._lock:
            self._verified_remote_locks.update(intent)

    def add_file(self, path: Path | None) -> None:
        """Add one static local file dependency."""
        if path is None:
            return
        with self._lock:
            if self._pending_static is None:
                self._add_path(path, self._static_files, self._static_symlink_events)
            else:
                self._add_path(path, self._pending_static.files, self._pending_static.symlink_events)
            self._publish()

    def add_directory(self, path: Path | None) -> None:
        """Add one recursively watched local directory dependency."""
        if path is None:
            return
        with self._lock:
            self._add_path(path, self._static_directories, self._static_symlink_events)
            self._publish()

    def exclude_file(self, path: Path) -> None:
        """Exclude one exact generated file from watch events."""
        with self._lock:
            outputs = self._outputs if self._pending_static is None else self._pending_static.outputs
            self._add_output(path, outputs, is_directory=False)
            self._publish()

    def record_file(self, path: Path) -> None:
        """Add a generated dependency to the private collector or current snapshot."""
        if (collector := _current_collector.get()) is not None and collector.owner is self:
            self._add_path(path, collector.files, collector.symlink_events, collector.polling_fingerprints)
            return
        with self._lock:
            self._add_path(path, self._generation_files, self._generation_symlink_events)
            self._publish()

    @property
    def files(self) -> frozenset[Path]:
        """All exact file dependencies from one immutable snapshot."""
        return self._snapshot.files

    @property
    def directories(self) -> frozenset[Path]:
        """Recursively watched dependency directories from one snapshot."""
        return self._snapshot.directories

    @property
    def output(self) -> Path | None:
        """The sole committed output path retained for legacy callers."""
        return self._snapshot.output

    @property
    def outputs(self) -> frozenset[Path]:
        """Every output path excluded from watch events."""
        return frozenset(self._snapshot.outputs)

    def watch_roots(self) -> tuple[Path, ...]:
        """Return existing roots cached when the dependency graph was published."""
        return self._snapshot.watch_roots

    @staticmethod
    def _includes_snapshot(snapshot: _DependencySnapshot, path_variants: frozenset[Path]) -> bool:
        if path_variants & snapshot.event_paths:
            return True
        return any(
            path_variant.is_relative_to(directory)
            for path_variant in path_variants
            for directory in snapshot.directories
        )

    @staticmethod
    def _snapshot_polling_dependencies_changed(
        snapshot: _DependencySnapshot, path_variants: frozenset[Path] | None = None
    ) -> bool:
        return any(
            _path_fingerprint(dependency) != fingerprint
            for dependency, fingerprint in (snapshot.polling_fingerprints or {}).items()
            if path_variants is None or any(dependency.is_relative_to(path_variant) for path_variant in path_variants)
        )

    def _polling_dependencies_changed(self) -> bool:
        """Return whether a polling watcher missed an exact dependency event."""
        return self._snapshot_polling_dependencies_changed(self._snapshot)

    def includes(self, path: Path) -> bool:
        """Return whether an event belongs to the immutable current dependency graph."""
        return self._includes_snapshot(self._snapshot, _path_variants(path))

    def accepts_event(self, path: Path, *, accept_directory_events: bool = False) -> bool:
        """Accept recovery candidates before output exclusions and optional directory events."""
        snapshot = self._snapshot
        try:
            resolved_path = path.resolve(strict=False)
        except (OSError, ValueError):
            resolved_path = path
        path_variants = _path_variants(path)
        if path_variants & snapshot.recovery_paths:
            return True
        if any(
            resolved_path == output
            or (is_directory and resolved_path.is_relative_to(output) and not path_variants & snapshot.files)
            for output, is_directory in snapshot.outputs.items()
        ):
            return False
        includes_dependency = self._includes_snapshot(snapshot, path_variants)
        if includes_dependency or not accept_directory_events:
            return includes_dependency
        try:
            is_directory = path.is_dir()
        except OSError:
            is_directory = False
        if not is_directory:
            return False
        has_dependency_below = any(
            dependency.is_relative_to(path_variant)
            for path_variant in path_variants
            for dependency in snapshot.event_paths
        )
        if not has_dependency_below:
            return False
        has_output_below = any(
            output.is_relative_to(path_variant) for path_variant in path_variants for output in snapshot.outputs
        )
        return not has_output_below or self._snapshot_polling_dependencies_changed(snapshot, path_variants)


def collector_is_active() -> bool:
    """Return whether watch dependency collection is active without importing watch mode."""
    return _current_collector.get() is not None


def collector_identity() -> object | None:
    """Return the identity of the active generation collector."""
    return _current_collector.get()


def record_local_dependency(path: Path) -> None:
    """Record a local file when dependency collection is active."""
    if (collector := _current_collector.get()) is not None:
        collector.owner.record_file(path)


def record_module_dependency(module: object) -> Path | None:
    """Record an imported module's source file when generation collection is active."""
    module_file = getattr(module, "__file__", None)
    if module_file is None:
        return None
    module_path = Path(module_file)
    if module_path.suffix == ".pyc":
        with suppress(ValueError):
            module_path = Path(source_from_cache(str(module_path)))
    record_local_dependency(module_path)
    return module_path


def _nearest_pyproject_toml(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        candidate = parent / "pyproject.toml"
        if candidate.is_file():
            return candidate
    return None
