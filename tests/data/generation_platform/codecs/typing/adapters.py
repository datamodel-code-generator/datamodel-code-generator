"""Positive codec adapter typing cases, written only against a generated package's public model_codecs module."""

from typing import Literal

from adapted.model_codecs import (
    UNSET,
    CodecCapabilities,
    CodecContext,
    CompiledSchemaValidatorV1,
    EncodedParameterContribution,
    FragmentContribution,
    ModelCodecAdapterV1,
    OfflineSchemaRegistry,
    ParameterCodecAdapterV1,
    ParameterCodecCapabilities,
    ParameterFragment,
    ParameterPlanView,
    PresenceTree,
    RawParameter,
    RuntimeModelBindingView,
    SchemaCodecAdapterV1,
    SchemaCodecCapabilities,
    SchemaPlanView,
    Unset,
    WireIssue,
    WireValue,
    freeze_wire,
    presence_of,
    thaw_wire,
)
from adapted.models import Keeper, Pet


class PetAdapter:
    api_version: Literal[1] = 1

    def capabilities(self, *, binding: RuntimeModelBindingView) -> CodecCapabilities:
        return CodecCapabilities(backends=("pydantic_v2.BaseModel",), native_kinds=("model",), presence="native")

    def decode_native(self, *, wire: WireValue, binding: RuntimeModelBindingView, context: CodecContext) -> Pet:
        return Pet.model_validate(thaw_wire(wire))

    def encode_native(self, *, value: Pet, binding: RuntimeModelBindingView, context: CodecContext) -> WireValue:
        return freeze_wire(value.model_dump(mode="json", by_alias=True))

    def presence(self, *, value: Pet, binding: RuntimeModelBindingView, context: CodecContext) -> PresenceTree | None:
        return presence_of(value.model_dump(mode="json", by_alias=True, exclude_unset=True))


class KeeperAdapter:
    api_version: Literal[1] = 1

    def capabilities(self, *, binding: RuntimeModelBindingView) -> CodecCapabilities:
        return CodecCapabilities(backends=("pydantic_v2.BaseModel",), native_kinds=("model",))

    def decode_native(self, *, wire: WireValue, binding: RuntimeModelBindingView, context: CodecContext) -> Keeper:
        return Keeper.model_validate(thaw_wire(wire))

    def encode_native(self, *, value: Keeper, binding: RuntimeModelBindingView, context: CodecContext) -> WireValue:
        return freeze_wire(value.model_dump(mode="json", by_alias=True))

    def presence(self, *, value: Keeper, binding: RuntimeModelBindingView, context: CodecContext) -> PresenceTree | None:
        return None


class JsonCookie:
    api_version: Literal[1] = 1

    def capabilities(self, *, plan: ParameterPlanView) -> ParameterCodecCapabilities:
        return ParameterCodecCapabilities(
            locations=("cookie",), styles=("form",), explode_values=(False,), value_kinds=("array",)
        )

    def encode_parameter(
        self, *, value: WireValue, plan: ParameterPlanView, context: CodecContext
    ) -> EncodedParameterContribution:
        fragment = ParameterFragment(name=plan.name.encode(), value=b"%5B%5D")
        return FragmentContribution(location="cookie", ordered_fragments=(fragment,))

    def decode_parameter(self, *, raw: RawParameter, plan: ParameterPlanView, context: CodecContext) -> WireValue | Unset:
        return freeze_wire([]) if raw.fragments else UNSET


class Compiled:
    def validate(self, *, wire: WireValue, context: CodecContext) -> tuple[WireIssue, ...]:
        return ()


class Lookaround:
    api_version: Literal[1] = 1

    def capabilities(self, *, schema_plan: SchemaPlanView) -> SchemaCodecCapabilities:
        return SchemaCodecCapabilities(dialects=(schema_plan.root.dialect,), vocabularies=(), keywords=("type",))

    def compile(self, *, schema_plan: SchemaPlanView, offline_registry: OfflineSchemaRegistry) -> CompiledSchemaValidatorV1:
        offline_registry.as_referencing_registry()
        return Compiled()


def pet_model() -> PetAdapter:
    return PetAdapter()


def keeper_model() -> KeeperAdapter:
    return KeeperAdapter()


def json_cookie() -> JsonCookie:
    return JsonCookie()


def lookaround() -> Lookaround:
    return Lookaround()


pet_adapter: ModelCodecAdapterV1[Pet] = pet_model()
keeper_adapter: ModelCodecAdapterV1[Keeper] = keeper_model()
cookie_adapter: ParameterCodecAdapterV1 = json_cookie()
schema_adapter: SchemaCodecAdapterV1 = lookaround()
