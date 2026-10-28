"""Watch mode for automatic code regeneration."""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.__main__ import Config, Exit
    from datamodel_code_generator.watch_dependencies import WatchDependencies

_PENDING_CHANGE_SAMPLE_LIMIT = 32


def _get_watchfiles() -> Any:
    """Lazily import watchfiles."""
    try:
        import watchfiles  # noqa: PLC0415
    except ImportError as exc:
        msg = "Please run `pip install 'datamodel-code-generator[watch]'` to use watch mode"
        raise Exception(msg) from exc  # noqa: TRY002
    return watchfiles


def _watch_filter(
    dependencies: WatchDependencies, *, accept_directory_events: bool = False
) -> Callable[[Any, str], bool]:
    def includes_dependency(_change: Any, path: str) -> bool:
        return dependencies.accepts_event(Path(path), accept_directory_events=accept_directory_events)

    return includes_dependency


def _force_polling() -> bool:
    """Avoid the macOS native backend's atomic-replace blind spot."""
    return sys.platform == "darwin"


def _regenerate(regenerate: Callable[[], Exit]) -> None:
    from datamodel_code_generator.__main__ import Exit  # noqa: PLC0415
    from datamodel_code_generator.model.base import _clear_custom_template_caches  # noqa: PLC0415

    _clear_custom_template_caches()
    if regenerate() == Exit.OK:
        return
    msg = "Generation failed"
    raise RuntimeError(msg)


@dataclass(frozen=True)
class _WatchContext:
    watchfiles: Any
    config: Config
    dependencies: WatchDependencies
    regenerate: Callable[[], Exit]
    watch_delay: float | None = None

    @property
    def effective_watch_delay(self) -> float:
        """Return the outer scheduler delay, defaulting to the generation config."""
        return self.config.watch_delay if self.watch_delay is None else self.watch_delay


@dataclass(slots=True)
class _WatcherState:
    """Small bounded hand-off from the watch thread to the regeneration loop."""

    pending_change_sample: set[tuple[Any, str]]
    has_pending_changes: bool = False
    error: BaseException | None = None
    exhausted: bool = False

    def add_changes(self, changes: set[tuple[Any, str]]) -> None:
        """Keep a diagnostic sample without retaining an unbounded event stream."""
        self.has_pending_changes = True
        remaining = _PENDING_CHANGE_SAMPLE_LIMIT - len(self.pending_change_sample)
        if remaining > 0:
            self.pending_change_sample.update(islice(changes, remaining))

    def take_changes(self) -> set[tuple[Any, str]]:
        """Drain the diagnostic sample while retaining the change notification."""
        changes = self.pending_change_sample.copy()
        self.pending_change_sample.clear()
        self.has_pending_changes = False
        return changes

    @property
    def is_ready(self) -> bool:
        """Whether the main watch loop has work, an error, or completion to process."""
        return self.has_pending_changes or self.error is not None or self.exhausted


def _regenerate_after_change(changes: set[tuple[Any, str]] | None, regenerate: Callable[[], Exit]) -> None:
    if changes is None:
        print("\nDetected changes while restarting the watcher.")  # noqa: T201
    else:
        print(f"\nDetected changes: {changes}")  # noqa: T201
    print("Regenerating...")  # noqa: T201
    try:
        _regenerate(regenerate)
        print("Done.")  # noqa: T201
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)  # noqa: T201


def _watch_changes(
    context: _WatchContext,
    watch_roots: tuple[Path, ...],
    stop_event: threading.Event,
    condition: threading.Condition,
    state: _WatcherState,
) -> None:
    """Publish filesystem changes from the persistent background watch stream."""
    force_polling = _force_polling()
    debounce_ms = max(1, int(context.effective_watch_delay * 1000))
    poll_delay_ms = min(300, debounce_ms)
    if force_polling:
        context.dependencies.enable_polling_fingerprints()
    pending_polling_fallback = False
    try:
        for changes in context.watchfiles.watch(
            *watch_roots,
            debounce=debounce_ms,
            step=debounce_ms,
            force_polling=force_polling,
            poll_delay_ms=poll_delay_ms,
            recursive=True,
            rust_timeout=poll_delay_ms if force_polling else 5000,
            stop_event=stop_event,
            watch_filter=_watch_filter(context.dependencies, accept_directory_events=True),
            yield_on_timeout=force_polling,
        ):
            if not changes:
                if not context.dependencies._polling_dependencies_changed():  # noqa: SLF001
                    pending_polling_fallback = False
                    continue
                if not pending_polling_fallback:
                    pending_polling_fallback = True
                    continue
                pending_polling_fallback = False
            else:
                pending_polling_fallback = False
            if (
                changes
                and force_polling
                and all(Path(path).is_dir() for _change, path in changes)
                and not context.dependencies._polling_dependencies_changed()  # noqa: SLF001
            ):
                continue
            with condition:
                state.add_changes(changes)
                condition.notify()
    except (KeyboardInterrupt, Exception) as exc:  # noqa: BLE001
        with condition:
            state.error = exc
            condition.notify()
    finally:
        with condition:
            state.exhausted = True
            condition.notify()


def _wait_for_changes(condition: threading.Condition, state: _WatcherState) -> set[tuple[Any, str]] | None:
    """Block for a watched change, raising a background watcher failure when present."""
    with condition:
        while not state.is_ready:
            condition.wait()
        if state.error is not None:
            raise state.error
        if not state.has_pending_changes:
            return None
        return state.take_changes()


def _watch_once(
    context: _WatchContext,
    watch_roots: tuple[Path, ...],
    *,
    catch_up: bool,
) -> bool:
    stop_event = threading.Event()
    condition = threading.Condition()
    state = _WatcherState(set())
    watcher = threading.Thread(
        target=_watch_changes,
        args=(context, watch_roots, stop_event, condition, state),
        name="datamodel-codegen-watch",
        daemon=True,
    )
    watcher.start()
    try:
        if catch_up:
            _regenerate_after_change(None, context.regenerate)
            if context.dependencies.watch_roots() != watch_roots:
                return True

        while (changes := _wait_for_changes(condition, state)) is not None:
            if not changes and not context.dependencies._polling_dependencies_changed():  # noqa: SLF001
                continue
            _regenerate_after_change(changes, context.regenerate)
            if context.dependencies.watch_roots() != watch_roots:
                return True
    finally:
        stop_event.set()
        watcher.join()
    return False


def watch_and_regenerate(
    config: Config,
    *,
    dependencies: WatchDependencies | None = None,
    regenerate: Callable[[], Exit],
    watch_path: Path | None = None,
    watch_delay: float | None = None,
) -> Exit:
    """Watch every local generation dependency and fully regenerate on changes."""
    from datamodel_code_generator.__main__ import Exit  # noqa: PLC0415
    from datamodel_code_generator.watch_dependencies import WatchDependencies  # noqa: PLC0415

    watchfiles = _get_watchfiles()

    watch_path = watch_path or (Path(config.input) if isinstance(config.input, (str, Path)) else None)
    if watch_path is None:
        print("Watch mode requires --input file path", file=sys.stderr)  # noqa: T201
        return Exit.ERROR

    if dependencies is None:
        dependencies = WatchDependencies()
        dependencies.configure(config, config_values={})

    print(f"Watching {watch_path} for changes... (Ctrl+C to stop)")  # noqa: T201

    watch_context = _WatchContext(
        watchfiles,
        config,
        dependencies,
        regenerate,
        watch_delay,
    )
    catch_up = False
    try:
        while watch_roots := dependencies.watch_roots():
            restart = _watch_once(
                watch_context,
                watch_roots,
                catch_up=catch_up,
            )
            if not restart:
                return Exit.OK
            catch_up = True
    except KeyboardInterrupt:
        print("\nWatch mode stopped.")  # noqa: T201

    return Exit.OK
