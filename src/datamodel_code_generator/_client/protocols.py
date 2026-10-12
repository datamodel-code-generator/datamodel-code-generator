"""Helper configuration of a client target: its JSON value and the validation that normalizes it.

The JSON value is an object mapping each helper's name to its definition. Validation gives every diagnostic and the
normalized metadata of each helper. Selectors and request targets are the runtime records generated helpers use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import partial
from math import isfinite
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from datamodel_code_generator._api_types import Diagnostic, OperationRef, SchemaRef
from datamodel_code_generator._client.naming import folded, helper_name_problem, token
from datamodel_code_generator._runtime.model_codecs.media import normalize_media_type
from datamodel_code_generator._target_config import _diagnostic  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator._target_documents import canonical_bytes

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

__all__ = (
    "Converter",
    "Helper",
    "HelperKind",
    "Link",
    "Spec",
    "Tree",
    "validate",
)

HelperKind: TypeAlias = Literal[
    "pagination", "polling", "sse", "ndjson", "websocket", "webhook", "cache", "resumable_upload"
]
Converter: TypeAlias = "Callable[[object, str], object]"
Spec: TypeAlias = "Mapping[str, tuple[Converter, object]]"
Tree: TypeAlias = "dict[str, Any]"

KINDS: Final = (
    "pagination",
    "polling",
    "sse",
    "ndjson",
    "websocket",
    "webhook",
    "cache",
    "resumable_upload",
)
_PUBLIC_KEY_SIGNATURES: Final = ("ed25519", "rsa-pss-sha256")
_SOURCES: Final = ("input", "initial", "previous")
_ROOT: Final = "protocols"
_EMPTY_STRING: Final = {"kind": "value", "value": ""}
_STATE_SETS: Final = ("pending", "succeeded", "failed", "cancelled")
_FRAMES: Final = {"json": None, "utf8": "text", "bytes": "binary"}
_VALIDATOR_KINDS: Final = ("etag", "last_modified", "both")


class _Mark(Enum):
    REQUIRED = "required"
    OMITTED = "omitted"


class _Invalid(Enum):
    INVALID = "invalid"


REQUIRED: Final = _Mark.REQUIRED
OMITTED: Final = _Mark.OMITTED
INVALID: Final = _Invalid.INVALID


@dataclass(frozen=True, slots=True, kw_only=True)
class Link:
    """An operation a helper sends, and the request targets it writes into that operation."""

    at: str
    ref: OperationRef
    targets: tuple[tuple[str, Mapping[str, Any]], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Helper:
    """One validated helper: its normalized metadata, operation links from its entry on, and schema references."""

    name: str
    kind: HelperKind
    enabled: bool
    at: str
    tree: Mapping[str, Any]
    links: tuple[Link, ...]
    schemas: tuple[tuple[str, SchemaRef], ...]


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _literal_problem(value: object) -> str | None:
    """Return why a value is no JSON literal: another type, or text with a lone surrogate."""
    pending = [value]
    while pending:
        match item := pending.pop():
            case None | bool() | int():
                continue
            case float() if isfinite(item):
                continue
            case str():
                if not _encodable(item):
                    return "has text that is not UTF-8"
                continue
            case list():
                pending.extend(item)
                continue
            case dict() if all(isinstance(key, str) and _encodable(key) for key in item):
                pending.extend(item.values())
                continue
            case _:
                pass
        return "must be a JSON value"
    return None


def validate(tree: Mapping[str, object]) -> tuple[tuple[Helper, ...], list[Diagnostic]]:
    """Validate a helper configuration object, returning its valid helpers and every problem in declaration order."""
    validator = _Validator()
    helpers = validator.helpers(tree, _ROOT)
    return helpers, validator.problems


def _choices(values: tuple[str, ...]) -> str:
    quoted = [repr(value) for value in values]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _collides(name: str, other: str) -> bool:
    return name == other or name.startswith(f"{other}.") or other.startswith(f"{name}.")


class _Validator:  # noqa: PLR0904
    """Validate helper configuration trees, collecting problems and the schema references of the current helper."""

    def __init__(self) -> None:
        self.problems: list[Diagnostic] = []
        self.schemas: list[tuple[str, SchemaRef]] = []

    def value(self, at: str, message: str) -> _Invalid:
        self.problems.append(_diagnostic("E_CONFIG_VALUE", at, message))
        return INVALID

    def conflict(self, at: str, message: str) -> None:
        self.problems.append(_diagnostic("E_CONFIG_CONFLICT", at, message))

    def record(self, value: object, at: str, spec: Spec, noun: str) -> Tree | _Invalid:
        """Convert a mapping by the spec, refusing unknown and missing keys; any invalid member invalidates it."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be {noun}")
        for key in value:
            if key not in spec:
                self.problems.append(_diagnostic("E_CONFIG_UNKNOWN", f"{at}.{key}", f"{at} has no key {key!r}"))
        result: Tree = {}
        for key, (convert, default) in spec.items():
            if key in value:
                result[key] = convert(value[key], f"{at}.{key}")
            elif default is REQUIRED:
                result[key] = self.value(f"{at}.{key}", f"{at} needs {key!r}")
            elif default is not OMITTED:
                result[key] = default
        known = all(key in spec for key in value)
        return result if known and all(item is not INVALID for item in result.values()) else INVALID

    def tagged(self, value: object, at: str, key: str, variants: Mapping[str, Spec], noun: str) -> Tree | _Invalid:
        """Convert a mapping whose tag key selects the spec of its other keys."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be {noun}")
        if key not in value:
            return self.value(f"{at}.{key}", f"{at} needs {key!r}")
        if not isinstance(tag := value[key], str) or tag not in variants:
            return self.value(f"{at}.{key}", f"{at}.{key} must be {_choices(tuple(variants))}")
        return self.record(value, at, {key: (_keep, REQUIRED), **variants[tag]}, noun)

    def items(self, value: object, at: str, convert: Converter, *, nonempty: bool = False) -> object:
        if not isinstance(value, list) or (nonempty and not value):
            return self.value(at, f"{at} must be a {'nonempty ' if nonempty else ''}list")
        result = [convert(item, f"{at}[{index}]") for index, item in enumerate(value)]
        return INVALID if any(item is INVALID for item in result) else result

    def distinct(self, at: str, values: object, what: str) -> object:
        if not isinstance(values, list):
            return values
        seen: set[bytes] = set()
        for index, item in enumerate(values):
            if (key := canonical_bytes(item)) in seen:
                return self.value(f"{at}[{index}]", f"{at}[{index}] repeats {what}")
            seen.add(key)
        return values

    def boolean(self, value: object, at: str) -> object:
        return value if type(value) is bool else self.value(at, f"{at} must be a boolean")

    def text(self, value: object, at: str) -> object:
        valid = isinstance(value, str) and _encodable(value)
        return value if valid else self.value(at, f"{at} must be a UTF-8 string")

    def nonempty(self, value: object, at: str) -> object:
        valid = isinstance(value, str) and value and _encodable(value)
        return value if valid else self.value(at, f"{at} must be a nonempty UTF-8 string")

    def line(self, value: object, at: str) -> object:
        if isinstance(value, str) and "\n" in value:
            return self.value(at, f"{at} must not contain a line feed")
        return self.nonempty(value, at)

    def header(self, value: object, at: str) -> object:
        return value if token(value) else self.value(at, f"{at} must be a header name")

    def optional_header(self, value: object, at: str) -> object:
        return None if value is None else self.header(value, at)

    def media(self, value: object, at: str) -> object:
        if isinstance(value, str):
            try:
                return normalize_media_type(value)
            except ValueError:
                pass
        return self.value(at, f"{at} must be a media type")

    def natural(self, value: object, at: str) -> object:
        valid = type(value) is int and value >= 0
        return value if valid else self.value(at, f"{at} must be an integer of at least 0")

    def positive(self, value: object, at: str) -> object:
        return value if type(value) is int and value > 0 else self.value(at, f"{at} must be a positive integer")

    def true(self, value: object, at: str) -> object:
        return value if value is True else self.value(at, f"{at} must be true")

    def seconds(self, value: object, at: str) -> object:
        if isinstance(value, int | float) and not isinstance(value, bool) and isfinite(value) and value > 0:
            return float(value)
        return self.value(at, f"{at} must be positive finite seconds")

    def literal(self, value: object, at: str) -> object:
        return value if (problem := _literal_problem(value)) is None else self.value(at, f"{at} {problem}")

    def pointer(self, value: object, at: str) -> object:
        from datamodel_code_generator._runtime.protocols.records import BodySelector  # noqa: PLC0415

        try:
            BodySelector(pointer=value)  # ty: ignore[invalid-argument-type]
        except (TypeError, ValueError):
            return self.value(at, f"{at} must be an RFC 6901 pointer")
        return value if self.text(value, at) is not INVALID else INVALID

    def choice(self, *choices: str) -> Converter:
        def convert(value: object, at: str) -> object:
            valid = isinstance(value, str) and value in choices
            return value if valid else self.value(at, f"{at} must be {_choices(choices)}")

        return convert

    def operation(self, value: object, at: str) -> object:
        if isinstance(value, str):
            return INVALID if self.pointer(value, at) is INVALID else OperationRef(pointer=value)
        spec = {"pointer": (self.pointer, REQUIRED), "document": (self.text, OMITTED)}
        reference = self.record(value, at, spec, "an operation reference")
        return INVALID if reference is INVALID else OperationRef(**reference)

    def schema(self, value: object, at: str) -> object:
        spec = {"pointer": (self.pointer, REQUIRED), "document": (self.text, OMITTED)}
        if (reference := self.record(value, at, spec, "a schema reference")) is INVALID:
            return INVALID
        self.schemas.append((at, schema := SchemaRef(**reference)))
        return schema

    def selector(self, value: object, at: str, *, every: bool = False, body: bool = False) -> object:
        """Convert a selector, which may select every occurrence of a header only in a binding value."""
        from datamodel_code_generator._runtime.protocols.records import BodySelector, HeaderSelector  # noqa: PLC0415

        variants: dict[str, Spec] = {"body": {"pointer": (self.text, REQUIRED)}}
        if not body:
            variants["header"] = {
                "name": (self.text, REQUIRED),
                "occurrence": (self.choice("single", "all"), "single"),
            }
            variants["status"] = {}
        if (selected := self.tagged(value, at, "from", variants, "a selector")) is INVALID:
            return INVALID
        if selected.get("occurrence") == "all" and not every:
            return self.value(f"{at}.occurrence", f"{at}.occurrence can be 'all' only in a binding value")
        try:
            if selected["from"] == "body":
                BodySelector(pointer=selected["pointer"])
            elif selected["from"] == "header":
                HeaderSelector(name=selected["name"], occurrence=selected["occurrence"])
        except ValueError as error:
            return self.value(at, f"{at}: {error}")
        return selected

    def target(self, value: object, at: str) -> object:
        from datamodel_code_generator._runtime.protocols.records import (  # noqa: PLC0415
            BodyTarget,
            ParameterTarget,
            QuerystringTarget,
        )

        named: Spec = {"name": (self.text, REQUIRED)}
        selected = self.tagged(
            value,
            at,
            "in",
            {
                "path": named,
                "query": named,
                "header": named,
                "cookie": named,
                "querystring": {"name": (self.text, REQUIRED), "pointer": (self.text, REQUIRED)},
                "body": {"pointer": (self.text, REQUIRED)},
            },
            "a request target",
        )
        if selected is INVALID:
            return INVALID
        try:
            match selected["in"]:
                case "querystring":
                    QuerystringTarget(name=selected["name"], pointer=selected["pointer"])
                case "body":
                    BodyTarget(pointer=selected["pointer"])
                case _:
                    ParameterTarget(location=selected["in"], name=selected["name"])
        except ValueError as error:
            return self.value(at, f"{at}: {error}")
        return selected

    def bindings(self, value: object, at: str) -> object:
        return self.items(value, at, self.binding)

    def binding(self, value: object, at: str) -> object:
        return self.record(value, at, {"target": (self.target, REQUIRED), "value": (self.bound, REQUIRED)}, "a binding")

    def bound(self, value: object, at: str) -> object:
        if isinstance(value, Mapping) and "literal" in value:
            return self.record(value, at, {"literal": (self.literal, REQUIRED)}, "a binding value")
        spec: Spec = {
            "source": (self.choice(*_SOURCES), REQUIRED),
            "selector": (partial(self.selector, every=True), REQUIRED),
        }
        return self.record(value, at, spec, "a binding value")

    def end(self, value: object, at: str) -> object:
        condition = self.tagged(
            value, at, "kind", {"missing": {}, "null": {}, "value": {"value": (self.literal, REQUIRED)}}, "an end"
        )
        if condition is not INVALID and condition["kind"] == "value" and condition["value"] is None:
            return self.value(f"{at}.value", f"{at}.value is null; use kind 'null'")
        return condition

    def ends(self, value: object, at: str) -> object:
        return self.distinct(at, self.items(value, at, self.end, nonempty=True), "an end condition")

    def statuses(self, value: object, at: str) -> object:
        def status(item: object, where: str) -> object:
            valid = type(item) is int and 100 <= item <= 599  # noqa: PLR2004
            return item if valid else self.value(where, f"{where} must be a status from 100 to 599")

        return self.distinct(at, self.items(value, at, status, nonempty=True), "a status")

    def states(self, *, nonempty: bool) -> Converter:
        def convert(value: object, at: str) -> object:
            return self.distinct(at, self.items(value, at, self.literal, nonempty=nonempty), "a state")

        return convert

    def helpers(self, value: Mapping[str, object], at: str) -> tuple[Helper, ...]:
        """Validate each helper in declaration order, refusing invalid and colliding names."""
        names: list[tuple[str, str]] = []
        helpers: list[Helper] = []
        for name, body in value.items():
            here = f"{at}[{name!r}]"
            if (problem := helper_name_problem(name)) is not None:
                self.value(here, problem)
            elif other := next((other for key, other in names if _collides(folded(name), key)), None):
                message = f"The helper names {other!r} and {name!r} collide"
                self.problems.append(_diagnostic("E_NAME_COLLISION", here, message))
            else:
                names.append((folded(name), name))
            if (helper := self.helper(name, body, here)) is not None:
                helpers.append(helper)
        return tuple(helpers)

    def helper(self, name: str, value: object, at: str) -> Helper | None:
        """Validate one helper and its kind-specific settings."""
        if not isinstance(value, Mapping):
            self.value(at, f"{at} must be a helper definition")
            return None
        if "kind" not in value:
            self.value(f"{at}.kind", f"{at} needs 'kind'")
            return None
        if not isinstance(kind := value["kind"], str) or kind not in KINDS:
            self.value(f"{at}.kind", f"{at}.kind must be {_choices(KINDS)}")
            return None
        self.schemas = []
        match kind:
            case "pagination":
                tree = self.pagination(value, at)
            case "polling":
                tree = self.polling(value, at)
            case "webhook":
                tree = self.webhook(value, at)
            case "websocket":
                tree = self.websocket(value, at)
            case "cache":
                tree = self.cache(value, at)
            case "resumable_upload":
                tree = self.upload(value, at, name)
            case _:
                tree = self.stream(value, at, sse=kind == "sse")
        if tree is INVALID:
            return None
        return Helper(
            name=name,
            kind=kind,
            enabled=tree["enabled"],
            at=at,
            tree=tree,
            links=tuple(_links(kind, tree, at)),
            schemas=tuple(self.schemas),
        )

    def pagination(self, value: object, at: str) -> Tree | _Invalid:
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "items": (partial(self.selector, body=True), REQUIRED),
                "item_schema": (self.schema, REQUIRED),
                "continuation": (self.continuation, REQUIRED),
                "bindings": (self.bindings, ()),
            },
            "a helper definition",
        )

    def continuation(self, value: object, at: str) -> object:
        """Convert a continuation, refusing an end at the empty string and counts without exactly one end evidence."""
        count: Spec = {
            "write": (self.target, REQUIRED),
            "first": (self.natural, REQUIRED),
            "step": (self.step, REQUIRED),
            "has_more": (self.selector, OMITTED),
            "total": (self.selector, OMITTED),
        }
        variants: dict[str, Spec] = {
            "cursor": {
                "read": (self.selector, REQUIRED),
                "write": (self.target, REQUIRED),
                "end": (self.ends, REQUIRED),
                "empty_string": (self.choice("value", "end"), REQUIRED),
            },
            "offset": count,
            "page": count,
            "next_url": {
                "read": (self.selector, REQUIRED),
                "end": (self.ends, REQUIRED),
                "repeat_request_body": (self.boolean, False),
            },
            "link": {"header": (self.header, REQUIRED), "rel": (self.nonempty, "next")},
        }
        if (selected := self.tagged(value, at, "kind", variants, "a continuation")) is INVALID:
            return INVALID
        if selected["kind"] == "cursor" and _EMPTY_STRING in selected["end"]:
            where = f"{at}.end[{selected['end'].index(_EMPTY_STRING)}]"
            self.conflict(where, f"{where} ends at the empty string, which empty_string decides alone")
            return INVALID
        if selected["kind"] in {"offset", "page"} and ("has_more" in selected) == ("total" in selected):
            if "total" in selected:
                self.conflict(at, f"{at} needs has_more or total, not both")
                return INVALID
            return self.value(at, f"{at} needs has_more or total")
        return selected

    def step(self, value: object, at: str) -> object:
        spec: Spec = {"literal": (self.positive, OMITTED), "page_items_count": (self.true, OMITTED)}
        if (step := self.record(value, at, spec, "a step")) is not INVALID and len(step) != 1:
            return self.value(at, f"{at} needs exactly one of 'literal' and 'page_items_count'")
        return step

    def polling(self, value: object, at: str) -> Tree | _Invalid:
        """Convert a polling helper, refusing state sets that share a value and immediate statuses it accepts."""
        polling = self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "create": (self.operation, REQUIRED),
                "accepted_statuses": (self.statuses, REQUIRED),
                "poll": (self.operation, REQUIRED),
                "bindings": (self.bindings, REQUIRED),
                "state": (self.selector, REQUIRED),
                "pending": (self.states(nonempty=True), REQUIRED),
                "succeeded": (self.states(nonempty=True), REQUIRED),
                "failed": (self.states(nonempty=False), ()),
                "cancelled": (self.states(nonempty=False), ()),
                "result": (self.result, REQUIRED),
                "interval": (self.interval, {"seconds": 1.0, "retry_after_header": None}),
                "remote_cancel": (self.remote_cancel, OMITTED),
                "immediate_result": (self.immediate, OMITTED),
                "expires_at": (self.selector, OMITTED),
            },
            "a helper definition",
        )
        if polling is INVALID:
            return INVALID
        owners: dict[bytes, str] = {}
        for name in _STATE_SETS:
            for index, state in enumerate(polling[name]):
                if (other := owners.setdefault(canonical_bytes(state), name)) != name:
                    where = f"{at}.{name}[{index}]"
                    self.conflict(where, f"{where} is a state {at}.{other} also lists")
                    return INVALID
        if shared := sorted(
            set(polling.get("immediate_result", {}).get("statuses", ())) & set(polling["accepted_statuses"])
        ):
            where = f"{at}.immediate_result.statuses"
            self.conflict(where, f"{where} lists the accepted statuses {shared}")
            return INVALID
        return polling

    def result(self, value: object, at: str) -> object:
        variants: dict[str, Spec] = {
            "inline": {"selector": (self.selector, REQUIRED), "schema": (self.schema, REQUIRED)},
            "operation": {"operation": (self.operation, REQUIRED), "bindings": (self.bindings, ())},
            "none": {},
        }
        return self.tagged(value, at, "kind", variants, "a result")

    def interval(self, value: object, at: str) -> object:
        spec: Spec = {"seconds": (self.seconds, 1.0), "retry_after_header": (self.optional_header, None)}
        return self.record(value, at, spec, "an interval")

    def remote_cancel(self, value: object, at: str) -> object:
        spec: Spec = {"operation": (self.operation, REQUIRED), "bindings": (self.bindings, ())}
        return self.record(value, at, spec, "a remote cancel")

    def immediate(self, value: object, at: str) -> object:
        spec: Spec = {
            "statuses": (self.statuses, REQUIRED),
            "selector": (self.selector, REQUIRED),
            "schema": (self.schema, REQUIRED),
        }
        return self.record(value, at, spec, "an immediate result")

    def upload(self, value: Mapping[object, object], at: str, name: str) -> Tree | _Invalid:
        """Convert a resumable upload helper of the offset profile; the parts profile is not supported yet."""
        if value.get("profile") == "parts":
            message = f"The parts profile of the resumable_upload helper {name!r} is not supported yet"
            self.problems.append(_unsupported(f"{at}.profile", message))
            return INVALID
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "profile": (self.choice("offset"), REQUIRED),
                "create": (self.upload_create, REQUIRED),
                "probe": (self.upload_probe, REQUIRED),
                "append": (self.upload_append, REQUIRED),
                "partial_commit": (self.choice("allowed", "forbidden"), REQUIRED),
                "completion": (self.upload_completion, REQUIRED),
                "abort": (self.remote_cancel, OMITTED),
            },
            "a helper definition",
        )

    def upload_create(self, value: object, at: str) -> object:
        spec: Spec = {
            "operation": (self.operation, REQUIRED),
            "size": (self.target, OMITTED),
            "session_url": (self.selector, OMITTED),
            "expires_at": (self.selector, OMITTED),
        }
        return self.record(value, at, spec, "an upload create")

    def upload_probe(self, value: object, at: str) -> object:
        spec: Spec = {
            "operation": (self.operation, REQUIRED),
            "bindings": (self.bindings, ()),
            "remote_offset": (self.selector, REQUIRED),
        }
        return self.record(value, at, spec, "an upload probe")

    def upload_append(self, value: object, at: str) -> object:
        spec: Spec = {
            "operation": (self.operation, REQUIRED),
            "bindings": (self.bindings, ()),
            "offset": (self.target, REQUIRED),
            "length": (self.target, OMITTED),
            "checksum": (self.upload_checksum, OMITTED),
        }
        return self.record(value, at, spec, "an upload append")

    def upload_checksum(self, value: object, at: str) -> object:
        spec: Spec = {
            "target": (self.target, REQUIRED),
            "algorithm": (self.choice("md5", "sha1", "sha256", "sha512"), REQUIRED),
            "encoding": (self.choice("base64", "hex"), "base64"),
            "algorithm_prefix": (self.boolean, False),
        }
        return self.record(value, at, spec, "an upload checksum")

    def upload_completion(self, value: object, at: str) -> object:
        variants: dict[str, Spec] = {
            "length": {},
            "operation": {
                "operation": (self.operation, REQUIRED),
                "bindings": (self.bindings, ()),
                "result_schema": (self.schema, REQUIRED),
            },
        }
        return self.tagged(value, at, "kind", variants, "a completion")

    def stream(self, value: object, at: str, *, sse: bool) -> Tree | _Invalid:
        """Convert an SSE or NDJSON helper, refusing raw and error events without a discriminator."""
        final: Spec = {} if sse else {"final_line": (self.choice("require_newline", "allow_eof"), REQUIRED)}
        stream = self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "media": (self.media, REQUIRED),
                "event_schema": (partial(self.event_schema, sse=sse), REQUIRED),
                "completion": (partial(self.completion, sse=sse), REQUIRED),
                **final,
                "unknown": (self.choice("error", "raw"), "error"),
                "error_events": (self.event_schemas, {}),
                "resume": (partial(self.resume, sse=sse), {"enabled": False}),
            },
            "a helper definition",
        )
        if stream is INVALID:
            return INVALID
        mapping = stream["event_schema"]["mapping"] if isinstance(stream["event_schema"], dict) else None
        if stream["unknown"] == "raw" and mapping is None:
            self.conflict(f"{at}.unknown", f"{at}.unknown can be 'raw' only with a discriminator")
            return INVALID
        if not (events := stream["error_events"]):
            return stream
        if mapping is None:
            self.conflict(f"{at}.error_events", f"{at}.error_events need a discriminator")
            return INVALID
        if shared := next((key for key in events if key in mapping), None):
            where = f"{at}.error_events[{shared!r}]"
            self.conflict(where, f"{where} is also an event of {at}.event_schema.mapping")
            return INVALID
        return stream

    def event_schema(self, value: object, at: str, *, sse: bool) -> object:
        if isinstance(value, Mapping) and ("discriminator" in value or "mapping" in value):
            spec: Spec = {
                "discriminator": (partial(self.discriminator, sse=sse), REQUIRED),
                "mapping": (partial(self.event_schemas, nonempty=True), REQUIRED),
            }
            return self.record(value, at, spec, "an event schema")
        return self.schema(value, at)

    def discriminator(self, value: object, at: str, *, sse: bool) -> object:
        variants: dict[str, Spec] = {"event_type": {}} if sse else {}
        variants["body"] = {"pointer": (self.pointer, REQUIRED)}
        return self.tagged(value, at, "from", variants, "a discriminator")

    def event_schemas(self, value: object, at: str, *, nonempty: bool = False) -> object:
        if not isinstance(value, Mapping) or (nonempty and not value):
            return self.value(at, f"{at} must be a {'nonempty ' if nonempty else ''}mapping")
        schemas: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(key, str) and key and _encodable(key):
                schemas[key] = self.schema(item, f"{at}[{key!r}]")
            else:
                schemas[""] = self.value(at, f"{at} has the key {key!r}, which is not a nonempty UTF-8 string")
        return INVALID if any(item is INVALID for item in schemas.values()) else schemas

    def completion(self, value: object, at: str, *, sse: bool) -> object:
        """Convert a completion; an NDJSON sentinel is a whole line, so it cannot contain a line feed."""
        sentinel = self.nonempty if sse else self.line
        variants: dict[str, Spec] = {"eof": {}, "sentinel": {"value": (sentinel, REQUIRED)}}
        if sse:
            variants["event_type"] = {"value": (self.nonempty, REQUIRED)}
        return self.tagged(value, at, "kind", variants, "a completion")

    def resume(self, value: object, at: str, *, sse: bool) -> object:
        """Convert a resume setting; a disabled one takes no other key, and an event ID cursor is SSE only."""
        if not isinstance(value, Mapping):
            return self.value(at, f"{at} must be a mapping")
        if (enabled := value.get("enabled", False)) is False:
            for key in (extra := [key for key in value if key != "enabled"]):
                self.conflict(f"{at}.{key}", f"{at}.{key} applies only when resume is enabled")
            return INVALID if extra else {"enabled": False}
        if enabled is not True:
            return self.value(f"{at}.enabled", f"{at}.enabled must be a boolean")
        event_id = sse and value.get("cursor") == "event_id"
        cursor: Spec = (
            {}
            if event_id
            else {
                "missing": (self.choice("inherit", "error"), REQUIRED),
                "null": (self.choice("clear", "error"), REQUIRED),
            }
        )
        spec: Spec = {
            "enabled": (_keep, REQUIRED),
            "cursor": (_keep if event_id else self.selector, REQUIRED),
            "write": (self.target, REQUIRED),
            "reopen_operation": (self.operation, REQUIRED),
            "delivery": (self.choice("at_least_once"), REQUIRED),
            "bindings": (self.bindings, ()),
            **cursor,
            "reconnect_on": (self.reasons, ("transport_interruption",)),
            "expires_at": (self.selector, OMITTED),
        }
        return self.record(value, at, spec, "a resume")

    def reasons(self, value: object, at: str) -> object:
        reasons = self.items(value, at, self.choice("transport_interruption", "incomplete_eof"), nonempty=True)
        return self.distinct(at, reasons, "a reconnect reason")

    def websocket(self, value: object, at: str) -> Tree | _Invalid:
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "subprotocols": (self.subprotocols, ()),
                "send": (self.message, REQUIRED),
                "receive": (self.message, REQUIRED),
            },
            "a helper definition",
        )

    def subprotocols(self, value: object, at: str) -> object:
        def subprotocol(item: object, where: str) -> object:
            return item if token(item) else self.value(where, f"{where} must be a subprotocol token")

        return self.distinct(at, self.items(value, at, subprotocol), "a subprotocol")

    def message(self, value: object, at: str) -> object:
        """Convert a message definition, giving it the frame its codec defaults to and refusing a mismatched one."""
        spec: Spec = {
            "codec": (self.choice(*_FRAMES), REQUIRED),
            "frame": (self.choice("text", "binary"), OMITTED),
            "schema": (self.schema, OMITTED),
        }
        if (message := self.record(value, at, spec, "a message definition")) is INVALID:
            return INVALID
        codec, fixed = message["codec"], _FRAMES[message["codec"]]
        if (codec == "json") != ("schema" in message):
            where = f"{at}.schema"
            if codec == "json":
                return self.value(where, f"{at} needs 'schema' for JSON messages")
            self.conflict(where, f"{where} applies only to JSON messages")
            return INVALID
        frame = message.setdefault("frame", fixed or "text")
        if fixed is not None and frame != fixed:
            self.conflict(f"{at}.frame", f"{at}.frame must be {fixed!r} for {codec} messages")
            return INVALID
        return message

    def cache(self, value: object, at: str) -> Tree | _Invalid:
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "operation": (self.operation, REQUIRED),
                "validator": (self.choice(*_VALIDATOR_KINDS), REQUIRED),
                "authenticated": (self.boolean, REQUIRED),
                "statuses": (self.statuses, [200]),
                "vary_allowlist": (self.vary_names, []),
            },
            "a helper definition",
        )

    def vary_names(self, value: object, at: str) -> object:
        """Convert Vary header names, refusing `*` and a name repeated in another case."""
        names = self.items(value, at, self.vary_name)
        seen: set[str] = set()
        for index, name in enumerate(names if isinstance(names, list) else ()):
            if (folded := name.lower()) in seen:
                return self.value(f"{at}[{index}]", f"{at}[{index}] repeats a header name")
            seen.add(folded)
        return names

    def vary_name(self, value: object, at: str) -> object:
        return self.value(at, f"{at} must name a header, not '*'") if value == "*" else self.header(value, at)

    def webhook(self, value: object, at: str) -> Tree | _Invalid:
        """Convert a webhook helper with a fixed signature preset or application verifier."""
        return self.record(
            value,
            at,
            {
                "kind": (_keep, REQUIRED),
                "enabled": (self.boolean, True),
                "event_schema": (partial(self.event_schema, sse=False), REQUIRED),
                "signature": (self.signature, REQUIRED),
            },
            "a helper definition",
        )

    def signature(self, value: object, at: str) -> object:
        """Convert fixed signature settings without interpreting arbitrary signed parts."""
        body: Spec = {
            "header": (self.header, REQUIRED),
            "encoding": (self.choice("hex", "base64"), "hex"),
            "prefix": (partial(self.visible, empty=True), ""),
        }
        public: Spec = {**body, "encoding": (self.choice("hex", "base64", "base64url"), "hex")}
        fact = self.choice("required", "none")
        return self.tagged(
            value,
            at,
            "kind",
            {
                "standard_webhooks": {},
                "stripe_style": {"header": (self.header, "Stripe-Signature")},
                "body_hmac": {**body, "algorithm": (self.choice("hmac-sha256", "hmac-sha512"), "hmac-sha256")},
                **dict.fromkeys(_PUBLIC_KEY_SIGNATURES, public),
                "adapter": {"timestamp": (fact, REQUIRED), "delivery_id": (fact, REQUIRED)},
                "none": {},
            },
            "a signature",
        )

    def visible(self, value: object, at: str, *, empty: bool = False) -> object:
        valid = isinstance(value, str) and (empty or value) and all("!" <= char <= "~" for char in value)
        return value if valid else self.value(at, f"{at} must be {'' if empty else 'nonempty '}visible ASCII text")


def _keep(value: object, _: str) -> object:
    return value


def _unsupported(at: str, message: str) -> Diagnostic:
    return Diagnostic(code="E_CLIENT_UNSUPPORTED", severity="error", stage="target", message=message, option_path=at)


def _targets(bindings: list[Tree], at: str) -> tuple[tuple[str, Tree], ...]:
    return tuple((f"{at}[{index}].target", binding["target"]) for index, binding in enumerate(bindings))


def _links(kind: str, tree: Tree, at: str) -> Iterator[Link]:
    """Yield the operations a valid helper sends, its entry operation first, with the targets each one takes."""
    match kind:
        case "pagination":
            continuation = tree["continuation"]
            written = ((f"{at}.continuation.write", continuation["write"]),) if "write" in continuation else ()
            yield Link(
                at=f"{at}.operation",
                ref=tree["operation"],
                targets=(*written, *_targets(tree["bindings"], f"{at}.bindings")),
            )
        case "webhook":
            return
        case "websocket":
            yield Link(at=f"{at}.operation", ref=tree["operation"])
        case "cache":
            yield Link(at=f"{at}.operation", ref=tree["operation"])
        case "resumable_upload":
            yield from _upload_links(tree, at)
        case "polling":
            yield Link(at=f"{at}.create", ref=tree["create"])
            yield Link(at=f"{at}.poll", ref=tree["poll"], targets=_targets(tree["bindings"], f"{at}.bindings"))
            if (result := tree["result"])["kind"] == "operation":
                where = f"{at}.result"
                targets = _targets(result["bindings"], f"{where}.bindings")
                yield Link(at=f"{where}.operation", ref=result["operation"], targets=targets)
            if (cancel := tree.get("remote_cancel")) is not None:
                where = f"{at}.remote_cancel"
                targets = _targets(cancel["bindings"], f"{where}.bindings")
                yield Link(at=f"{where}.operation", ref=cancel["operation"], targets=targets)
        case _:
            yield Link(at=f"{at}.operation", ref=tree["operation"])
            if (resume := tree["resume"])["enabled"]:
                where = f"{at}.resume"
                targets = ((f"{where}.write", resume["write"]), *_targets(resume["bindings"], f"{where}.bindings"))
                yield Link(at=f"{where}.reopen_operation", ref=resume["reopen_operation"], targets=targets)


def _upload_links(tree: Tree, at: str) -> Iterator[Link]:
    """Yield the operations an upload helper sends, its create operation first, with the targets each one takes."""
    create = tree["create"]
    sized = ((f"{at}.create.size", create["size"]),) if "size" in create else ()
    yield Link(at=f"{at}.create.operation", ref=create["operation"], targets=sized)
    probe = tree["probe"]
    yield Link(
        at=f"{at}.probe.operation", ref=probe["operation"], targets=_targets(probe["bindings"], f"{at}.probe.bindings")
    )
    append = tree["append"]
    written = tuple((f"{at}.append.{name}", append[name]) for name in ("offset", "length") if name in append)
    if (checksum := append.get("checksum")) is not None:
        written = (*written, (f"{at}.append.checksum.target", checksum["target"]))
    yield Link(
        at=f"{at}.append.operation",
        ref=append["operation"],
        targets=(*written, *_targets(append["bindings"], f"{at}.append.bindings")),
    )
    for name in ("completion", "abort"):
        if (part := tree.get(name)) is not None and "operation" in part:
            where = f"{at}.{name}"
            yield Link(
                at=f"{where}.operation", ref=part["operation"], targets=_targets(part["bindings"], f"{where}.bindings")
            )
