"""Request parameters and media that write the values a server chose into a caller's querystring or JSON body.

Others show a helper what the caller's own first request sends, such as the position a traversal starts at. Only a
helper's plan loads them, since they need the operation runtime every generated operation already imports.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, TypeAlias

from typing_extensions import TypeVar

from ..client.operations import BodyMedia, ParameterSpec
from ..client.paths import dot_segment, path_segments
from ..model_codecs.errors import CodecAdapterError, ParameterEncodingError
from .records import BodyTarget, ParameterTarget, QuerystringTarget
from .values import Patch

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from ..client.operations import OperationPlan
    from ..client.options import RequestValidation
    from ..model_codecs.wire import WireValue
    from .records import RequestTarget, Selector

__all__ = (
    "PatchedMedia",
    "PatchedParameter",
    "ReadMedia",
    "ReadParameter",
    "ReadPaths",
    "Writes",
    "dotted_read",
    "query_written",
    "read_paths",
    "targeted",
)

T = TypeVar("T")
Writes: TypeAlias = tuple[tuple[int | None, str | None], ...]
PathPart: TypeAlias = "tuple[str, int | None, Selector | None, int]"
ReadPaths: TypeAlias = "tuple[tuple[str, tuple[PathPart, ...]], ...]"


@dataclass(frozen=True, slots=True, kw_only=True)
class PatchedParameter(ParameterSpec):
    """A parameter whose argument is always a Patch of the caller's argument, such as a querystring's properties."""

    def encode(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's argument with the patch's writes."""
        assert isinstance(value, Patch)
        return value.applied(lambda given: ParameterSpec.encode(self, given, mode))


@dataclass(frozen=True, slots=True, kw_only=True)
class PatchedMedia(BodyMedia):
    """A JSON request media whose body is always a Patch of the caller's body."""

    def wire(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's body with the patch's writes."""
        assert isinstance(value, Patch)
        return value.applied(lambda given: BodyMedia.wire(self, given, mode))


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadParameter(ParameterSpec):
    """A parameter whose encoded argument a helper reads before it is sent, as validated as any argument's."""

    read: Callable[[WireValue], None]

    def encode(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's argument once the helper has read it."""
        wire = ParameterSpec.encode(self, value, mode)
        self.read(wire)
        return wire


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadMedia(BodyMedia):
    """A JSON request media whose encoded body a helper reads before it is sent, as validated as any body's."""

    read: Callable[[WireValue], None]

    def wire(self, value: object, mode: RequestValidation) -> WireValue:
        """Return the wire value of the caller's body once the helper has read it."""
        wire = BodyMedia.wire(self, value, mode)
        self.read(wire)
        return wire


def _position(call: OperationPlan[T, object], location: str, name: str) -> int:
    """Return the argument position of a declared parameter, matching a header's name without regard to case."""

    def key(value: str) -> str:
        return value.lower() if location == "header" else value

    wanted = key(name)
    return next(
        index
        for index, spec in enumerate(call.parameters)
        if spec.plan.location == location and key(spec.plan.name) == wanted
    )


def targeted(
    call: OperationPlan[T, object], targets: Iterable[RequestTarget]
) -> tuple[OperationPlan[T, object], Writes, frozenset[str], frozenset[str]]:
    """Return an operation that takes the values written to the targets as wire values, and where each one goes.

    A write is a parameter's argument position without a pointer, a querystring's position with a pointer into its
    value, or no position with a pointer into the JSON body, every media of which is JSON. The header and query names
    written follow, which a call's options must not patch. The operation skips the schema and argument checks, since
    the server chose the values.
    """
    parameters = list(call.parameters)
    writes: list[tuple[int | None, str | None]] = []
    headers: set[str] = set()
    queries: set[str] = set()
    patched = False
    for target in targets:
        if isinstance(target, BodyTarget):
            writes.append((None, target.pointer))
            patched = True
            continue
        if isinstance(target, QuerystringTarget):
            position, pointer = _position(call, "querystring", target.name), target.pointer
        else:
            position, pointer = _position(call, target.location, target.name), None
            if (location := target.location) == "header":
                headers.add(target.name.lower())
            elif location == "query":
                queries.add(target.name)
        spec = parameters[position]
        parameters[position] = (
            replace(spec, encoder=None)
            if pointer is None
            else PatchedParameter(plan=spec.plan, encoder=spec.encoder, adapter=spec.adapter)
        )
        writes.append((position, pointer))
    body = call.body
    if patched:
        assert body is not None
        body = replace(
            body,
            media=tuple(
                PatchedMedia(media_type=media.media_type, kind=media.kind, encoder=media.encoder)
                for media in body.media
            ),
        )
    called = replace(call, parameters=tuple(parameters), body=body, checks=())
    return called, tuple(writes), frozenset(headers), frozenset(queries)


def query_written(call: OperationPlan[T, object], writes: Writes, name: str) -> bool:
    """Return whether a query patch can replace a field written by a helper, including object properties.

    Exploded form objects own their declared properties and any additional name their parameter accepts. Deep
    objects own those properties within the parameter's brackets. Decoder reservations do not limit wire writes.
    """
    for position, _ in writes:
        if position is None or (plan := call.parameters[position].plan).location != "query":
            continue
        if plan.shape != "object" or not plan.explode or plan.style not in {"form", "deepObject"}:
            if name == plan.name:
                return True
            continue
        member = name
        if plan.style == "deepObject":
            prefix = f"{plan.name}["
            if not name.startswith(prefix) or not name.endswith("]"):
                continue
            member = name[len(prefix) : -1]
        if any(item.name == member for item in plan.fields) or plan.additional is not None:
            return True
    return False


def read_paths(call: OperationPlan[T, object], sources: Sequence[tuple[RequestTarget, Selector | None]]) -> ReadPaths:
    """Return the path segments read values are written to, each parameter by name, write, selector, and position.

    A parameter the caller's argument fills has no write.
    """
    paths: dict[str, tuple[int | None, Selector | None]] = {
        target.name: (index, selector)
        for index, (target, selector) in enumerate(sources)
        if isinstance(target, ParameterTarget) and target.location == "path"
    }
    reads = {name for name, (_, selector) in paths.items() if selector is not None}
    return tuple(
        (segment, tuple((name, *paths.get(name, (None, None)), _position(call, "path", name)) for name in names))
        for segment, names in (path_segments(call.path) if reads else ())
        if not reads.isdisjoint(names)
    )


def dotted_read(
    parameters: Sequence[ParameterSpec],
    segment: str,
    parts: tuple[PathPart, ...],
    written: tuple[WireValue, ...],
    callers: Callable[[], Mapping[str, str]],
) -> Selector | None:
    """Return the selector of a read value written to a path segment that encodes to a dot segment, or None.

    The caller's own path arguments in the segment keep the texts `callers` gives, and each text is the request's,
    through the parameter's registered adapter when it has one. The first read value whose encoded text is non-empty
    is blamed, or else the first read value. A value its parameter or adapter cannot encode is left to the request,
    which refuses it.
    """
    try:
        texts = {
            name: callers()[name] if index is None else parameters[position].path_text(written[index])
            for name, index, _, position in parts
        }
    except (ParameterEncodingError, CodecAdapterError):
        return None
    if not dot_segment(segment, texts):
        return None
    reads = [(name, read) for name, _, read, _ in parts if read is not None]
    return next((read for name, read in reads if texts[name]), reads[0][1])
