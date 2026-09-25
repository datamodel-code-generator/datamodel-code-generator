"""Version 1 codec adapter protocols and the core wrappers that keep validation, direction, and presence."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from decimal import Decimal
from types import GenericAlias, MappingProxyType
from typing import TYPE_CHECKING, Final, Generic, Literal, Protocol, TypeVar
from urllib.parse import unquote_to_bytes

from typing_extensions import TypeIs

from .capabilities import canonical_capabilities
from .errors import (
    CodecAdapterError,
    CodecBindingError,
    CodecConfigurationError,
    CodecError,
    ModelProjectionError,
    NativeIssue,
    NativeValidationError,
    ParameterEncodingError,
    WireIssue,
    WireValidationError,
)
from .parameters import (
    EncodedParameterContribution,
    FragmentContribution,
    ParameterFragment,
    QueryStringContribution,
    RawParameter,
)
from .patterns import MatchBudget
from .unset import UNSET, Unset
from .values import DecodedValue, ModelInput, ModelValue, ProjectionIssue
from .wire import (
    JSONValue,
    PresenceTree,
    WireValue,
    escape_pointer_token,
    freeze_wire,
    select_present,
    snapshot_presence,
    without_pointers,
)

if TYPE_CHECKING:
    from .capabilities import (
        AdapterManifest,
        AnyCapabilities,
        CodecCapabilities,
        ParameterCodecCapabilities,
        SchemaCodecCapabilities,
        ServerMediaCodecCapabilities,
    )
    from .context import CodecContext
    from .schema import WireValidator
    from .views import (
        CodecBindingView,
        CodecFieldView,
        OfflineSchemaRegistry,
        ParameterPlanView,
        RuntimeModelBindingView,
        SchemaPlanView,
    )

T = TypeVar("T")

_EMPTY: Final[Mapping[str, WireValue]] = MappingProxyType({})
_PROHIBITED: Final = {
    "request": ("schema.readOnly", "The value is read-only and cannot appear in a request"),
    "response": ("schema.writeOnly", "The value is write-only and cannot appear in a response"),
}
_TRIPLET: Final = re.compile(rb"%(?![0-9A-Fa-f]{2})")
_PATH_BYTES: Final = re.compile(rb"[A-Za-z0-9\-._~!$&'()*+,;=:@%]*")
_QUERY_NAME: Final = re.compile(rb"[A-Za-z0-9\-._~!$'()*+,;:@%/?]+")
_QUERY_VALUE: Final = re.compile(rb"[A-Za-z0-9\-._~!$'()*+,;=:@%/?]*")
_QUERY: Final = re.compile(rb"[A-Za-z0-9\-._~!$&'()*+,;=:@%/?]+")
_TOKEN: Final = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HEADER_VALUE: Final = re.compile(rb"[\t\x20-\x7e\x80-\xff]*")
_COOKIE_OCTETS: Final = re.compile(rb"[\x21\x23-\x2B\x2D-\x3A\x3C-\x5B\x5D-\x7E]*")


class ModelCodecAdapterV1(Protocol[T]):
    """Convert between validated wire values and one native type through four callbacks sharing T."""

    @property
    def api_version(self) -> Literal[1]:
        """Return the adapter contract version."""
        ...

    def capabilities(self, *, binding: RuntimeModelBindingView) -> CodecCapabilities:
        """Return the capabilities the adapter supports for this binding."""
        ...

    def decode_native(self, *, wire: WireValue, binding: RuntimeModelBindingView, context: CodecContext) -> T:
        """Construct the native value from a wire value the core already validated."""
        ...

    def encode_native(self, *, value: T, binding: RuntimeModelBindingView, context: CodecContext) -> WireValue:
        """Read a native value into schema wire coordinates."""
        ...

    def presence(self, *, value: T, binding: RuntimeModelBindingView, context: CodecContext) -> PresenceTree | None:
        """Return official presence when the adapter declares native presence, otherwise None."""
        ...


class ParameterCodecAdapterV1(Protocol):
    """Serialize one parameter the builtin styles cannot represent."""

    @property
    def api_version(self) -> Literal[1]:
        """Return the adapter contract version."""
        ...

    def capabilities(self, *, plan: ParameterPlanView) -> ParameterCodecCapabilities:
        """Return the capabilities the adapter supports for this plan."""
        ...

    def encode_parameter(
        self, *, value: WireValue, plan: ParameterPlanView, context: CodecContext
    ) -> EncodedParameterContribution:
        """Encode a validated wire value into ordered raw fragments or a whole query."""
        ...

    def decode_parameter(
        self, *, raw: RawParameter, plan: ParameterPlanView, context: CodecContext
    ) -> WireValue | Unset:
        """Decode the target parameter from raw occurrences, returning UNSET only when it is absent."""
        ...


class CompiledSchemaValidatorV1(Protocol):
    """Validate wire values against one compiled schema closure without converting them."""

    def validate(self, *, wire: WireValue, context: CodecContext) -> tuple[WireIssue, ...]:
        """Return value-free issues in schema-evaluation order; an empty tuple means valid."""
        ...


class SchemaCodecAdapterV1(Protocol):
    """Own every keyword of a schema closure the builtin validator cannot evaluate."""

    @property
    def api_version(self) -> Literal[1]:
        """Return the adapter contract version."""
        ...

    def capabilities(self, *, schema_plan: SchemaPlanView) -> SchemaCodecCapabilities:
        """Return the capabilities the adapter supports for this schema plan."""
        ...

    def compile(
        self, *, schema_plan: SchemaPlanView, offline_registry: OfflineSchemaRegistry
    ) -> CompiledSchemaValidatorV1:
        """Compile the reachable schema closure once."""
        ...


class ByteReader(Protocol):
    """Read a synchronous byte stream owned by its surface."""

    def read(self, max_bytes: int) -> bytes:
        """Return at most max_bytes bytes, or empty bytes at the end."""
        ...


class AsyncByteReader(Protocol):
    """Read an asynchronous byte stream owned by its surface."""

    def read(self, max_bytes: int) -> Awaitable[bytes]:
        """Return at most max_bytes bytes, or empty bytes at the end."""
        ...


class ServerMediaCodecAdapterV1(Protocol):
    """Convert one buffered server request or response media and wire values."""

    @property
    def api_version(self) -> Literal[1]:
        """Return the adapter contract version."""
        ...

    def capabilities(self, *, binding: CodecBindingView) -> ServerMediaCodecCapabilities:
        """Return the capabilities the adapter supports for this binding."""
        ...

    async def decode(self, *, reader: AsyncByteReader, binding: CodecBindingView, context: CodecContext) -> WireValue:
        """Read and decode a request body into a wire value."""
        ...

    def encode(self, *, wire: WireValue, binding: CodecBindingView, context: CodecContext) -> bytes:
        """Encode a validated wire value into response bytes without I/O."""
        ...


def checked_adapter(
    descriptor: object, manifest: AdapterManifest[AnyCapabilities], report: Callable[[], object]
) -> None:
    """Require version 1 and capabilities equal to the registration, before any request."""
    if getattr(descriptor, "api_version", None) != 1:
        msg = f"The {manifest.name} adapter does not implement codec adapter API version 1"
        raise CodecAdapterError(msg)
    try:
        returned = report()
    except Exception as error:
        msg = f"The {manifest.name} adapter failed to report its capabilities"
        raise CodecAdapterError(msg) from error
    declared = manifest.capabilities
    if not isinstance(returned, type(declared)) or canonical_capabilities(returned) != canonical_capabilities(declared):
        msg = f"The {manifest.name} adapter reports capabilities that differ from its registration"
        raise CodecAdapterError(msg)


def _context_key(binding: CodecBindingView) -> tuple[object, ...]:
    use = binding.use
    return (
        use.direction,
        None if binding.schema is None else binding.schema.schema_id,
        use.owner.pointer if use.owner_kind == "operation" else None,
        use.media,
    )


def _excluded_names(fields: tuple[CodecFieldView, ...], direction: str) -> frozenset[str]:
    return frozenset(
        field.wire_name for field in fields if (field.read_only if direction == "request" else field.write_only)
    )


class SchemaAdapterValidator:
    """Validate one use through a compiled schema adapter, enforcing the smaller subject limits first.

    The adapter reads binary floats as the finite decimals its offline registry holds for schema numbers.
    Members that the use's direction excludes stay prohibited, as the builtin validator reports them.
    """

    __slots__ = ("_compiled", "_excluded", "_max_subject", "_max_total", "_prohibited", "_schema_id")

    def __init__(
        self,
        *,
        manifest: AdapterManifest[SchemaCodecCapabilities],
        plan: SchemaPlanView,
        adapter: SchemaCodecAdapterV1,
        registry: OfflineSchemaRegistry,
        excluded: frozenset[str] = frozenset(),
    ) -> None:
        """Check the adapter against its registration and compile the schema closure once."""
        capabilities = manifest.capabilities
        checked_adapter(adapter, manifest, lambda: adapter.capabilities(schema_plan=plan))
        try:
            self._compiled = adapter.compile(schema_plan=plan, offline_registry=registry)
        except CodecConfigurationError:
            raise
        except Exception as error:
            msg = f"The {manifest.name} schema adapter failed to compile its schema"
            raise CodecAdapterError(msg) from error
        self._max_subject = min(plan.limits.max_subject_bytes, capabilities.max_subject_bytes)
        self._max_total = min(plan.limits.max_total_subject_bytes, capabilities.max_total_subject_bytes)
        self._excluded = excluded
        self._prohibited = _PROHIBITED[plan.direction]
        self._schema_id = plan.root.schema_id

    def validate(
        self, wire: WireValue, *, budget: MatchBudget | None = None, context: CodecContext
    ) -> tuple[WireIssue, ...]:
        """Charge every string and member name to the smaller limits, then return the adapter's checked issues."""
        subject = _decimals(wire) if self._charge(wire, budget or MatchBudget()) else wire
        try:
            issues = self._compiled.validate(wire=subject, context=context)
        except CodecError:
            raise
        except Exception as error:
            msg = "A schema adapter failed while validating a value"
            raise CodecAdapterError(msg) from error
        if type(issues) is not tuple or any(type(issue) is not WireIssue for issue in issues):
            msg = "A schema adapter returned something other than a tuple of WireIssue records"
            raise CodecAdapterError(msg)
        code, message = self._prohibited
        return (
            *issues,
            *(
                WireIssue(
                    code=code, message=message, instance_pointer=pointer, schema_id=self._schema_id, schema_pointer=""
                )
                for pointer in self.excluded(wire)
            ),
        )

    def excluded(self, wire: WireValue, *, budget: MatchBudget | None = None) -> tuple[str, ...]:  # noqa: ARG002
        """Return the top-level members this direction excludes."""
        if not isinstance(wire, Mapping):
            return ()
        return tuple(f"/{escape_pointer_token(name)}" for name in wire if name in self._excluded)

    def _charge(self, wire: WireValue, budget: MatchBudget) -> bool:
        limits = MatchBudget(remaining=self._max_total, subject_limit=self._max_subject)
        pending: list[WireValue] = [wire]
        floats = False
        while pending:
            match value := pending.pop():
                case str():
                    limits.charge(value)
                    budget.charge(value)
                case tuple():
                    pending.extend(value)
                case Mapping():
                    pending.extend(value)
                    pending.extend(value.values())
                case float():
                    floats = True
                case _:
                    continue
        return floats


class AdapterModelCodec(Generic[T]):
    """Run a model adapter inside the core's direction, presence, and wire validation rules."""

    __slots__ = (
        "_adapter",
        "_class",
        "_context",
        "_encode_native",
        "_excluded",
        "_gaps",
        "_native_presence",
        "_presence",
        "_validator",
        "_view",
    )

    def __init__(
        self,
        *,
        manifest: AdapterManifest[CodecCapabilities],
        view: RuntimeModelBindingView,
        adapter: ModelCodecAdapterV1[T],
        validator: WireValidator,
    ) -> None:
        """Check the adapter against its registration and the binding before any value is processed."""
        binding = view.binding
        checked_adapter(adapter, manifest, lambda: adapter.capabilities(binding=view))
        self._adapter = adapter
        self._encode_native: Callable[..., WireValue] = adapter.encode_native
        self._native_presence: Callable[..., object] = adapter.presence
        self._view = view
        native = view.native_type
        self._class = native if isinstance(native, type) and not isinstance(native, GenericAlias) else None
        self._validator = validator
        self._context = _context_key(binding)
        self._presence = manifest.capabilities.presence == "native"
        self._excluded = _excluded_names(binding.fields, binding.use.direction)
        self._gaps = tuple(field for field in binding.fields if field.required and field.wire_name in self._excluded)

    @property
    def binding(self) -> CodecBindingView:
        """Return the data-only binding view this codec was built from."""
        return self._view.binding

    def decode(self, wire: WireValue, context: CodecContext) -> DecodedValue[T]:
        """Validate a received wire value in its direction, then let the adapter construct T."""
        self._require(context, inbound=True)
        snapshot = freeze_wire(wire)
        if issues := self._validator.validate(snapshot, context=context):
            raise WireValidationError(issues)
        return self._project(snapshot, context)

    def from_wire(self, wire: JSONValue | WireValue, context: CodecContext) -> DecodedValue[T]:
        """Copy a value to send, drop members excluded in its direction, validate it, and project it."""
        self._require(context, inbound=False)
        return self._project(self._outbound(freeze_wire(wire), context), context)

    def snapshot(self, value: T, context: CodecContext, *, presence: PresenceTree | None = None) -> ModelValue[T]:
        """Capture a native value's wire form for sending, using explicit presence when given."""
        self._require(context, inbound=False)
        wire = self._outbound(self._native_wire(value, context, presence), context)
        return ModelValue(
            value=value, binding_id=self.binding.binding_id, wire=wire, presence=snapshot_presence(wire), extras=_EMPTY
        )

    def encode(self, value: object, context: CodecContext) -> WireValue:
        """Return the validated wire value to send for a native value or a snapshot of this binding.

        The value may come from a dynamic boundary such as a server handler: a native type that is a class
        rejects other objects before the adapter reads them, and the adapter's wire value is validated.
        """
        self._require(context, inbound=False)
        match value:
            case ModelValue() | ModelInput():
                if value.binding_id != self.binding.binding_id:
                    msg = "The value was captured for a different binding"
                    raise CodecBindingError(msg)
                return self._outbound(freeze_wire(value.wire), context)
            case _:
                pass
        return self._outbound(self._native_wire(value, context, None), context)

    def _require(self, context: CodecContext, *, inbound: bool) -> None:
        key = (context.direction, context.schema_id, context.operation_id, context.media_type)
        if key != self._context or context.inbound is not inbound:
            msg = "The codec context does not match the bound use"
            raise CodecBindingError(msg)

    def _outbound(self, wire: WireValue, context: CodecContext) -> WireValue:
        if excluded := self._validator.excluded(wire):
            wire = without_pointers(wire, excluded)
        if issues := self._validator.validate(wire, context=context):
            raise WireValidationError(issues)
        return wire

    def _project(self, wire: WireValue, context: CodecContext) -> DecodedValue[T]:
        binding = self.binding
        if gaps := tuple(
            ProjectionIssue(
                code="DIRECTIONAL_REQUIRED",
                pointer=f"/{escape_pointer_token(field.wire_name)}",
                schema_location=field.schema_id or "",
                field_id=field.field_id,
                message="The native type requires a property that this direction excludes",
            )
            for field in self._gaps
            if not isinstance(wire, Mapping) or field.wire_name not in wire
        ):
            return ModelInput(
                binding_id=binding.binding_id, wire=wire, presence=snapshot_presence(wire), extras=_EMPTY, issues=gaps
            )
        try:
            value = self._adapter.decode_native(wire=wire, binding=self._view, context=context)
        except CodecError:
            raise
        except Exception:  # noqa: BLE001
            raise NativeValidationError((NativeIssue(code="native.adapter", pointer="", native_path=()),)) from None
        return ModelValue(
            value=value, binding_id=binding.binding_id, wire=wire, presence=snapshot_presence(wire), extras=_EMPTY
        )

    def _native_wire(self, value: object, context: CodecContext, presence: PresenceTree | None) -> WireValue:
        if (native := self._class) is not None and not isinstance(value, native):
            msg = f"The value is not a {native.__name__} value"
            raise ModelProjectionError(msg)
        try:
            encoded = self._encode_native(value=value, binding=self._view, context=context)
            official = self._native_presence(value=value, binding=self._view, context=context)
        except CodecError:
            raise
        except Exception as error:
            msg = "The model adapter failed to read the native value"
            raise CodecAdapterError(msg) from error
        mask = self._mask(official, presence)
        try:
            wire = freeze_wire(encoded)
        except (TypeError, ValueError) as error:
            msg = "The model adapter returned a value outside the wire domain"
            raise CodecAdapterError(msg) from error
        return wire if mask is None else select_present(wire, mask)

    def _mask(self, official: object, presence: PresenceTree | None) -> PresenceTree | None:
        match official:
            case None:
                return presence
            case PresenceTree() if self._presence:
                return presence or official
            case _:
                msg = "The model adapter returned presence outside its declared capability"
                raise CodecAdapterError(msg)


class AdapterParameterCodec:
    """Run a parameter adapter and verify every contribution it returns at the raw HTTP boundary."""

    __slots__ = ("_adapter", "_empty", "_name", "_plan", "_reserved")

    def __init__(
        self,
        *,
        manifest: AdapterManifest[ParameterCodecCapabilities],
        plan: ParameterPlanView,
        adapter: ParameterCodecAdapterV1,
    ) -> None:
        """Check the adapter against its registration before any request."""
        checked_adapter(adapter, manifest, lambda: adapter.capabilities(plan=plan))
        self._adapter = adapter
        self._plan = plan
        self._empty = manifest.capabilities.supports_empty_containers
        self._name = self._folded(plan.name.encode())
        self._reserved = frozenset(reserved.encode() for reserved in plan.reserved_names)

    def encode(self, value: WireValue, context: CodecContext) -> EncodedParameterContribution:
        """Encode a validated wire value and check the adapter's contribution."""
        if not self._empty and _empty(value):
            msg = f"The {self._plan.name} parameter adapter cannot encode an empty array or object"
            raise ParameterEncodingError(msg)
        try:
            contribution: object = self._adapter.encode_parameter(
                value=freeze_wire(value), plan=self._plan, context=context
            )
        except CodecError:
            raise
        except Exception as error:
            msg = "A parameter adapter failed to encode a value"
            raise CodecAdapterError(msg) from error
        reason: str | None
        match contribution:
            case QueryStringContribution(raw_query=raw) if self._plan.location == "querystring":
                if _is_bytes(raw) and _QUERY.fullmatch(raw) and not _TRIPLET.search(raw):
                    return contribution
                reason = "an empty or malformed query"
            case FragmentContribution(location=location, ordered_fragments=fragments) if (
                location == self._plan.location != "querystring"
            ):
                if (reason := self._rejected(fragments)) is None:
                    return contribution
            case _:
                reason = "a contribution for another location"
        msg = f"A parameter adapter returned {reason}"
        raise CodecAdapterError(msg)

    def decode(self, raw: RawParameter, context: CodecContext) -> WireValue | Unset:
        """Decode the target parameter and require a wire value or UNSET."""
        try:
            decoded = self._adapter.decode_parameter(raw=raw, plan=self._plan, context=context)
        except CodecError:
            raise
        except Exception as error:
            msg = "A parameter adapter failed to decode a value"
            raise CodecAdapterError(msg) from error
        if decoded is UNSET:
            return UNSET
        try:
            wire = freeze_wire(decoded)
        except (TypeError, ValueError) as error:
            msg = "A parameter adapter returned a value outside the wire domain"
            raise CodecAdapterError(msg) from error
        if not self._empty and _empty(wire):
            msg = "A parameter adapter returned an empty array or object its capabilities exclude"
            raise CodecAdapterError(msg)
        return wire

    def _folded(self, name: bytes) -> bytes:
        return name.lower() if self._plan.location == "header" else name

    def _rejected(self, fragments: tuple[ParameterFragment, ...]) -> str | None:
        location = self._plan.location
        if type(fragments) is not tuple or not all(
            _is_fragment(fragment) and _is_bytes(fragment.value) for fragment in fragments
        ):
            return "a malformed fragment"
        if location == "path" and (len(fragments) != 1 or fragments[0].name is not None):
            return "anything but one unnamed path fragment"
        for fragment in fragments:
            name, value = fragment.name, fragment.value
            match location:
                case "path":
                    valid = bool(_PATH_BYTES.fullmatch(value)) and not _TRIPLET.search(value)
                case "query":
                    valid = (
                        isinstance(name, bytes)
                        and bool(_QUERY_NAME.fullmatch(name) and _QUERY_VALUE.fullmatch(value))
                        and not (_TRIPLET.search(name) or _TRIPLET.search(value))
                        and unquote_to_bytes(name.replace(b"+", b" ")) not in self._reserved
                    )
                case "header":
                    valid = (
                        isinstance(name, bytes)
                        and self._folded(name) == self._name
                        and bool(_TOKEN.fullmatch(name) and _HEADER_VALUE.fullmatch(value))
                    )
                case _:
                    valid = (
                        isinstance(name, bytes)
                        and name == self._name
                        and bool(_TOKEN.fullmatch(name) and _COOKIE_OCTETS.fullmatch(value))
                    )
            if not valid:
                return f"a {location} fragment outside its syntax or ownership"
        return None


def _is_bytes(value: object) -> TypeIs[bytes]:
    return isinstance(value, bytes)


def _is_fragment(value: object) -> TypeIs[ParameterFragment]:
    return isinstance(value, ParameterFragment)


def _empty(value: WireValue) -> bool:
    return isinstance(value, (tuple, Mapping)) and not value


def _decimals(value: WireValue) -> WireValue:
    match value:
        case float():
            return Decimal(repr(value))
        case tuple():
            return tuple(_decimals(item) for item in value)
        case Mapping():
            return MappingProxyType({name: _decimals(item) for name, item in value.items()})
        case _:
            return value
