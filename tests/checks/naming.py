"""``NAMING``: the names the L1 resolver and the L2 settle give are a function of the resolver's naming calls.

When the pipeline of a parse starts, the check copies the parse's resolver (``start``) and records, until
``name_models`` plans the names, every call of a naming method that ``seams.py`` wraps on it (``ModelResolver.add``,
``_add_ref_at``, ``_add_affixed``, ``_delete_at``, ``delete_names``, ``get_class_name``, ``name_past_parent``,
``refresh_reference_names``, ``settle_yielding_names``) that no recorded call makes, with what the call reads beside
its arguments: the current root, whether new references yield and the excluded names. When ``name_models`` starts, the
calls replay in order on the copy, and every reference must end with the name and duplicate name it has, and every
model of the parse be named by the reference the resolver holds at the model's path.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any, Final

from tests.checks.areas import Area, mode

if TYPE_CHECKING:
    from collections.abc import Callable

_LOG: Final = "log"
"""The key of the parse's ``_Log`` in the state of the ``NAMING`` mode."""
_REPORTED: Final = 5
"""How many names the message of a violation lists; the report lists all."""


@dataclass(frozen=True, slots=True)
class _Call:
    """One recorded naming call: the original method, its arguments and what it reads beside them."""

    method: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    current_root: tuple[str, ...]
    new_references_yield: bool
    exclude_names: frozenset[str]


@dataclass(slots=True)
class _Log:
    """The naming calls of one parse on its resolver, and the copy of the resolver they replay on."""

    resolver: Any
    replay: Any
    exclude_names: frozenset[str]
    calls: list[_Call] = field(default_factory=list)
    depth: int = 0
    """How many recorded calls run now: a call that one of them makes is part of it."""


def _frozen(value: Any) -> Any:
    """Return ``value`` as the caller passed it, a list (a path) as a tuple, so that no later write moves it."""
    return tuple(value) if isinstance(value, list) else value


def _copied_resolver(resolver: Any) -> Any:
    """Return a resolver of the class of ``resolver`` in its state now: copied containers and references.

    The copy records no allocation, so the replay leaves what the run records for ``--model-name-map`` alone.
    """
    from datamodel_code_generator.reference import Reference

    replay = object.__new__(type(resolver))
    replay.__dict__.update({
        name: copy(value) if isinstance(value, (dict, set, list)) else value for name, value in vars(resolver).items()
    })
    replay.references = {
        path: Reference(
            path=reference.path,
            original_name=reference.original_name,
            name=reference.name,
            duplicate_name=reference.duplicate_name,
            loaded=reference.loaded,
        )
        for path, reference in resolver.references.items()
    }
    copies = {id(reference): replay.references[path] for path, reference in resolver.references.items()}
    replay._yielding_names = {
        name: entry._replace(reference=copies[id(entry.reference)]) for name, entry in resolver._yielding_names.items()
    }
    replay.model_name_record = None
    return replay


def start(parser: Any) -> None:
    """Start recording the naming calls of the parse of ``parser``, from a copy of its resolver as it is now."""
    if (naming_mode := mode(Area.NAMING)) is not None:
        resolver = parser.model_resolver
        naming_mode.state[_LOG] = _Log(resolver, _copied_resolver(resolver), frozenset(resolver.exclude_names))


def recorded(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a naming method of ``ModelResolver``: record a call on the parse's resolver that no recorded call makes."""

    @wraps(original)
    def recorded(self: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            (naming_mode := mode(Area.NAMING)) is None
            or (log := naming_mode.state.get(_LOG)) is None
            or log.resolver is not self
        ):
            return original(self, *args, **kwargs)
        if not log.depth:
            if self.exclude_names != log.exclude_names:
                log.exclude_names = frozenset(self.exclude_names)
            log.calls.append(
                _Call(
                    original,
                    tuple(map(_frozen, args)),
                    {name: _frozen(value) for name, value in kwargs.items()},
                    tuple(self._current_root),
                    self._new_references_yield,
                    log.exclude_names,
                )
            )
        log.depth += 1
        try:
            return original(self, *args, **kwargs)
        finally:
            log.depth -= 1

    return recorded


def _names(reference: Any) -> str:
    return "no reference" if reference is None else f"{reference.name!r} (asked {reference.duplicate_name!r})"


def _unexplained(log: _Log, results: list[Any]) -> list[str]:
    """Replay the calls of ``log`` on its copy; return the names they do not give, and the models no reference names."""
    replay, exclude_names = log.replay, None
    for call in log.calls:
        if call.exclude_names is not exclude_names:
            replay.exclude_names = set(exclude_names := call.exclude_names)
        replay._current_root = call.current_root
        replay._new_references_yield = call.new_references_yield
        call.method(replay, *call.args, **call.kwargs)
    references, replayed = log.resolver.references, replay.references
    problems = [
        f"{path}: {_names(references.get(path))} where its calls give {_names(replayed.get(path))}"
        for path in sorted(references.keys() | replayed.keys())
        if (reference := references.get(path)) is None
        or (other := replayed.get(path)) is None
        or (reference.name, reference.duplicate_name) != (other.name, other.duplicate_name)
    ]
    problems.extend(
        f"{model.reference.path}: the model {model.class_name} is named by no reference of the resolver"
        for model in results
        if references.get(model.reference.path) is not model.reference
    )
    return problems


def name_models(original: Callable[[Any, Any], None]) -> Callable[[Any, Any], None]:
    """Wrap ``name_models``: before it plans the names, the recorded calls must explain every name of the parse."""

    @wraps(original)
    def name_models(parser: Any, run: Any) -> None:
        if (
            (naming_mode := mode(Area.NAMING)) is not None
            and (log := naming_mode.state.pop(_LOG, None)) is not None
            and (problems := _unexplained(log, parser.results))
        ):
            from datamodel_code_generator import Error

            naming_mode.violation(
                Error,
                f"names the resolver's naming calls do not explain: {'; '.join(problems[:_REPORTED])}",
                problems=problems,
            )
        return original(parser, run)

    return name_models
