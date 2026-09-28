"""The validation modes a client package allows, and the uses that cannot take them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, TypeVar

from datamodel_code_generator._api_types import Diagnostic
from datamodel_code_generator._generation_contract import OperationId
from datamodel_code_generator._runtime.model_codecs.codec import has_models, needs_schema

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._client.config import ClientValidationConfig
    from datamodel_code_generator._client.plan import ClientPlan
    from datamodel_code_generator._generation_contract import TypeUseId
    from datamodel_code_generator._openapi_codec_plan import CodecPlan
    from datamodel_code_generator._runtime.model_codecs.bindings import UseBinding

ModeT = TypeVar("ModeT", bound=str)

_ENTRIES: Final = frozenset({"pydantic_type_adapter", "msgspec_convert"})
_ROLES: Final = {
    "parameter": "parameter",
    "request_body": "request body",
    "request_encoding_header": "part header",
    "response_body": "response body",
    "response_encoding_header": "part header",
}


def allowed(default: ModeT, overrides: tuple[ModeT, ...]) -> tuple[ModeT, ...]:
    """Return the modes of one axis a package allows: its default first, then each other override once."""
    return (default, *(mode for mode in overrides if mode != default))


def _option(axis: str, overrides: str, mode: str, validation: ClientValidationConfig) -> str:
    """Return the option path of a mode: the axis for its default, else its position among the overrides."""
    if mode == getattr(validation, axis):
        return f"validation.{axis}"
    return f"validation.{overrides}[{getattr(validation, overrides).index(mode)}]"


def _label(use: TypeUseId) -> str:
    """Return how a message names a use: its role, name, status, and media, then its operation's method and path."""
    owner = use.owner
    assert isinstance(owner, OperationId)
    *_, path, method = (token.replace("~1", "/").replace("~0", "~") for token in owner.use_site.pointer.split("/"))
    part = "part" if use.name is not None and use.role.endswith("_body") else None
    described = (
        use.location if use.role == "parameter" else None,
        _ROLES[use.role],
        part,
        use.name,
        use.status,
        use.media,
    )
    return f"{' '.join(word for word in described if word)} of {method.upper()} {path}"


def _refusal(use: TypeUseId, binding: UseBinding, axis: str, mode: str, *, adapted: bool) -> str | None:
    """Return why a use cannot take a request or response mode, or None when it can.

    A part header takes native requests without a validation entry, since they only read it as its type.
    """
    if mode == "schema" or use.role not in _ROLES or binding.direction != axis:
        return None
    match axis, mode:
        case _, _ if adapted:
            return "goes through a registered adapter that validates it against its schema"
        case "request", "native" if (
            binding.converter_strategy not in _ENTRIES and use.role != "request_encoding_header"
        ):
            return f"has no native validation entry in {binding.backend}"
        case "response", "native" if binding.projection_mode == "native" and needs_schema(binding):
            return "tells its union members apart only by their schemas"
        case _:
            pass
    return None


def _unchecked(binding: UseBinding, *, adapted: bool) -> str | None:
    """Return why Pydantic cannot validate an argument a call takes as a native value, or None when it can."""
    if adapted:
        return "goes through a registered adapter that gives Pydantic no schema"
    if binding.backend == "msgspec.Struct" and has_models(binding.type):
        return "holds msgspec Structs that Pydantic cannot validate"
    return None


def argument_uses(plan: ClientPlan) -> frozenset[TypeUseId]:
    """Return the uses of the arguments a call takes as native values: its parameters and bodies other than parts."""
    return frozenset(
        use.id
        for spec in plan.operations
        for use in (
            *(parameter.use for parameter in spec.parameters),
            *(media.use for media in (spec.body.media if spec.body is not None else ()) if media.members is None),
        )
        if use is not None
    )


def admission_problems(
    validation: ClientValidationConfig, codecs: CodecPlan, arguments: frozenset[TypeUseId]
) -> Iterator[Diagnostic]:
    """Yield a diagnostic for each use and allowed mode that the use cannot take.

    Response headers are read only by their decoders, which validate against their schemas, and an envelope keeps its
    strict contract under every mode. Pydantic validates the arguments a call takes as native values.
    """
    axes = (
        ("request", "request_overrides", allowed(validation.request, validation.request_overrides)),
        ("response", "response_overrides", allowed(validation.response, validation.response_overrides)),
        ("arguments", "argument_overrides", allowed(validation.arguments, validation.argument_overrides)),
    )
    adapted = {plan.use for plan in codecs.adapters}
    for use, binding in codecs.bindings:
        for axis, overrides, modes in axes:
            for mode in modes:
                if axis != "arguments":
                    reason, alternative = _refusal(use, binding, axis, mode, adapted=use in adapted), "schema"
                elif mode == "pydantic" and use in arguments:
                    reason, alternative = _unchecked(binding, adapted=use in adapted), "none"
                else:
                    continue
                if reason is None:
                    continue
                yield Diagnostic(
                    code="E_CONFIG_VALUE",
                    severity="error",
                    stage="binding",
                    message=(
                        f"The {mode!r} {axis} validation cannot take the {_label(use)}, which {reason}; "
                        f"select {alternative!r}"
                    ),
                    source_pointer=use.use_site.pointer,
                    option_path=_option(axis, overrides, mode, validation),
                )
