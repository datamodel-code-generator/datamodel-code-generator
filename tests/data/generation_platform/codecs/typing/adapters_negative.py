"""Deliberately invalid codec adapter typing cases; every error is checked by code and line."""

from typing import Literal

from adapted.model_codecs import (
    CodecContext,
    ModelCodecAdapterV1,
    ParameterCodecAdapterV1,
    ParameterCodecCapabilities,
    ParameterPlanView,
    PresenceTree,
    RawParameter,
    RuntimeModelBindingView,
    SchemaCodecAdapterV1,
    SchemaCodecCapabilities,
    SchemaPlanView,
    WireIssue,
    WireValue,
)
from adapted.models import Keeper, Pet


class PetAdapter:
    api_version: Literal[1] = 1

    def capabilities(self, *, binding: RuntimeModelBindingView) -> SchemaCodecCapabilities:
        return SchemaCodecCapabilities(dialects=("d",), vocabularies=(), keywords=())

    def decode_native(self, *, wire: WireValue, binding: RuntimeModelBindingView, context: CodecContext) -> Pet:
        return Pet.model_validate(wire)

    def encode_native(self, *, value: Pet, binding: RuntimeModelBindingView, context: CodecContext) -> WireValue:
        return None

    def presence(self, *, value: Pet, binding: RuntimeModelBindingView, context: CodecContext) -> PresenceTree | None:
        return None


class VersionTwo:
    api_version: Literal[2] = 2

    def capabilities(self, *, schema_plan: SchemaPlanView) -> SchemaCodecCapabilities:
        return SchemaCodecCapabilities(dialects=("d",), vocabularies=(), keywords=())

    def compile(self, *, schema_plan: SchemaPlanView, offline_registry: object) -> tuple[WireIssue, ...]:
        return ()


class BytesCookie:
    api_version: Literal[1] = 1

    def capabilities(self, *, plan: ParameterPlanView) -> ParameterCodecCapabilities:
        return ParameterCodecCapabilities(locations=("cookie",), styles=(), explode_values=(), value_kinds=("array",))

    def encode_parameter(self, *, value: WireValue, plan: ParameterPlanView, context: CodecContext) -> bytes:
        return b""

    def decode_parameter(self, *, raw: RawParameter, plan: ParameterPlanView, context: CodecContext) -> bytes:
        return b""


_other_model: ModelCodecAdapterV1[Keeper] = PetAdapter()
_wrong_capabilities: ModelCodecAdapterV1[Pet] = PetAdapter()
_wrong_version: SchemaCodecAdapterV1 = VersionTwo()
_wrong_contribution: ParameterCodecAdapterV1 = BytesCookie()
_incomplete_issue = WireIssue(code="schema.type", message="m", instance_pointer="", schema_id="s")
