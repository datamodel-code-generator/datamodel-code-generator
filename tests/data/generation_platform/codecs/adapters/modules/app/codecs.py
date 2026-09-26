"""Codec adapters written against the generated package's public `model_codecs` module only."""

from __future__ import annotations

import json
from typing import Literal, get_args
from urllib.parse import quote, unquote

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.validators import extend

from adapted.model_codecs import (
    UNSET,
    CodecCapabilities,
    CodecConfigurationError,
    CodecResourceLimitError,
    FragmentContribution,
    ParameterCodecCapabilities,
    ParameterEncodingError,
    ParameterFragment,
    QueryStringContribution,
    SchemaCodecCapabilities,
    WireIssue,
    freeze_wire,
    presence_of,
    thaw_wire,
)

DIALECT = "https://json-schema.org/draft/2020-12/schema"
JSON_COOKIE = ParameterCodecCapabilities(
    locations=("cookie",),
    styles=("form",),
    explode_values=(False,),
    value_kinds=("array",),
    supports_empty_containers=True,
)
LOOKAROUND = SchemaCodecCapabilities(
    dialects=(DIALECT,), vocabularies=(), keywords=("pattern", "type"), pattern_dialects=("ecma262-u",)
)
PET_MODEL = CodecCapabilities(backends=("pydantic_v2.BaseModel",), native_kinds=("model",), presence="native")
KEEPER_MODEL = CodecCapabilities(backends=("pydantic_v2.BaseModel",), native_kinds=("model",))
NAMES = CodecCapabilities(backends=("pydantic_v2.BaseModel",), native_kinds=("array",))
SCRIPTED = {
    **{
        location: ParameterCodecCapabilities(
            locations=(location,), styles=(style,), explode_values=(False,), value_kinds=("object",)
        )
        for location, style in (("path", "simple"), ("query", "form"), ("header", "simple"), ("cookie", "form"))
    },
    "querystring": ParameterCodecCapabilities(
        locations=("querystring",),
        styles=(),
        explode_values=(),
        value_kinds=("object",),
        media_types=("application/x-www-form-urlencoded",),
    ),
}


class JsonCookie:
    """Carry one array cookie as compact percent-encoded JSON."""

    api_version: Literal[1] = 1

    def capabilities(self, *, plan):
        return JSON_COOKIE

    def encode_parameter(self, *, value, plan, context):
        text = json.dumps(thaw_wire(value), separators=(",", ":"), ensure_ascii=False)
        fragment = ParameterFragment(name=plan.name.encode(), value=quote(text, safe="").encode())
        return FragmentContribution(location="cookie", ordered_fragments=(fragment,))

    def decode_parameter(self, *, raw, plan, context):
        values = [fragment.value for fragment in raw.fragments if fragment.name == plan.name.encode()]
        if not values:
            return UNSET
        if len(values) > 1:
            raise ParameterEncodingError(f"The {plan.name} cookie appears more than once")
        return freeze_wire(json.loads(unquote(values[0].decode("ascii"), errors="strict")))


def json_cookie() -> JsonCookie:
    return JsonCookie()


class _Compiled:
    def __init__(self, validator, schema_id, script):
        self.validator = validator
        self.schema_id = schema_id
        self.script = script

    def validate(self, *, wire, context):
        match self.script and wire:
            case "raise":
                raise RuntimeError("validate failed")
            case "limit":
                raise CodecResourceLimitError("The subject exceeds the matcher budget")
            case "list":
                return []
            case _:
                return tuple(
                    WireIssue(
                        code=f"schema.{error.validator}",
                        message=f"The value fails {error.validator}",
                        instance_pointer="".join(f"/{part}" for part in error.absolute_path),
                        schema_id=self.schema_id,
                        schema_pointer="".join(f"/{part}" for part in error.absolute_schema_path),
                    )
                    for error in self.validator.iter_errors(thaw_wire(wire))
                )


def _pattern(validator, pattern, instance, schema):
    if isinstance(instance, str) and instance != "ab":
        yield ValidationError("The string does not match the pattern")


class Lookaround:
    """Evaluate exactly the pattern ^a(?=b)b$, delegating every other keyword to jsonschema."""

    api_version: Literal[1] = 1

    def __init__(self, script=False, capabilities=LOOKAROUND, root=True):
        self.script = script
        self._capabilities = capabilities
        self.root = root

    def capabilities(self, *, schema_plan):
        return self._capabilities

    def compile(self, *, schema_plan, offline_registry):
        schema = thaw_wire(schema_plan.root.normalized)
        if schema_plan.root.schema_id not in offline_registry.schema_ids:
            raise CodecConfigurationError("The registry does not serve the planned schema")
        if self.root and (
            schema.get("pattern") != "^a(?=b)b$" or not set(schema_plan.required_keywords) <= {"type", "pattern"}
        ):
            raise CodecConfigurationError("The lookaround adapter only compiles ^a(?=b)b$")
        validator = extend(Draft202012Validator, {"pattern": _pattern})(
            schema, registry=offline_registry.as_referencing_registry()
        )
        return _Compiled(validator, schema_plan.root.schema_id, self.script)


def lookaround() -> Lookaround:
    return Lookaround()


def scripted_lookaround():
    return Lookaround(script=True)


def small_lookaround():
    return Lookaround(capabilities=SchemaCodecCapabilities(
        dialects=(DIALECT,), vocabularies=(), keywords=("pattern", "type"), pattern_dialects=("ecma262-u",), max_subject_bytes=2
    ))


def box_lookaround():
    return Lookaround(
        capabilities=SchemaCodecCapabilities(
            dialects=(DIALECT,),
            vocabularies=(),
            keywords=("$ref", "items", "multipleOf", "pattern", "properties", "required", "type"),
            pattern_dialects=("ecma262-u",),
        ),
        root=False,
    )


class PetModel:
    """Construct and read Pet models through Pydantic's own alias-aware API."""

    api_version: Literal[1] = 1

    def capabilities(self, *, binding):
        return PET_MODEL

    def decode_native(self, *, wire, binding, context):
        data = thaw_wire(wire)
        match data.get("badge"):
            case "raise":
                raise RuntimeError("decode failed")
            case "limit":
                raise CodecResourceLimitError("The value exceeds the adapter's budget")
            case _:
                return binding.native_type.model_validate_json(json.dumps(data))

    def encode_native(self, *, value, binding, context):
        match getattr(value, "name", None):
            case "raise":
                raise RuntimeError("encode failed")
            case "limit":
                raise CodecResourceLimitError("The value exceeds the adapter's budget")
            case "object":
                return object()
            case _:
                return freeze_wire(value.model_dump(mode="json", by_alias=True))

    def presence(self, *, value, binding, context):
        if value.name == "presence":
            return "every field"
        return presence_of(value.model_dump(mode="json", by_alias=True, exclude_unset=True))


def pet_model() -> PetModel:
    return PetModel()


class KeeperModel(PetModel):
    """Read Keeper models without official presence."""

    def capabilities(self, *, binding):
        return KEEPER_MODEL

    def presence(self, *, value, binding, context):
        return None


def keeper_model() -> KeeperModel:
    return KeeperModel()


class CatModel(KeeperModel):
    """Build models whose Literal fields name enum members, reading the resolved native field types."""

    def decode_native(self, *, wire, binding, context):
        data = thaw_wire(wire)
        for field in binding.binding.fields:
            members = {member.value: member for member in get_args(binding.native_field_types[field.field_id])}
            if field.wire_name in data:
                data[field.wire_name] = members.get(data[field.wire_name], data[field.wire_name])
        return binding.native_type.model_validate(data)


def cat_model() -> CatModel:
    return CatModel()


class NamesModel:
    """Read and write lists of names, whose native type is a parameterized list."""

    api_version: Literal[1] = 1

    def capabilities(self, *, binding):
        return NAMES

    def decode_native(self, *, wire, binding, context):
        return list(thaw_wire(wire))

    def encode_native(self, *, value, binding, context):
        return freeze_wire(list(value))

    def presence(self, *, value, binding, context):
        return None


def names_model() -> NamesModel:
    return NamesModel()


class Scripted:
    """Return whatever contribution or decoded value the case describes, to exercise the boundary verifier."""

    api_version = 1

    def __init__(self, location):
        self.location = location

    def capabilities(self, *, plan):
        return SCRIPTED[self.location]

    def encode_parameter(self, *, value, plan, context):
        spec = thaw_wire(value)
        match spec.get("kind"):
            case "raise":
                raise RuntimeError("encode failed")
            case "error":
                raise ParameterEncodingError("The value cannot be encoded")
            case "querystring":
                return QueryStringContribution(raw_query=spec["query"].encode())
            case "object":
                return object()
            case "raw":
                return FragmentContribution(location=spec["location"], ordered_fragments=tuple(spec["fragments"]))
            case "text":
                return FragmentContribution(
                    location=spec["location"],
                    ordered_fragments=tuple(ParameterFragment(name=name.encode(), value=text) for name, text in spec["fragments"]),
                )
            case kind:
                fragments = [
                    ParameterFragment(name=None if name is None else name.encode(), value=text.encode())
                    for name, text in spec["fragments"]
                ]
                return FragmentContribution(
                    location=spec["location"], ordered_fragments=fragments if kind == "list" else tuple(fragments)
                )

    def decode_parameter(self, *, raw, plan, context):
        text = (raw.raw_query or b"").decode() or b"".join(fragment.value for fragment in raw.fragments).decode()
        match text:
            case "unset":
                return UNSET
            case "raise":
                raise RuntimeError("decode failed")
            case "error":
                raise ParameterEncodingError("The value cannot be decoded")
            case "object":
                return object()
            case _:
                return json.loads(text)


def scripted_path():
    return Scripted("path")


def scripted_query():
    return Scripted("query")


def scripted_header():
    return Scripted("header")


def scripted_cookie():
    return Scripted("cookie")


def scripted_querystring():
    return Scripted("querystring")


class Rogue:
    """Break one startup rule of the version 1 contract."""

    def __init__(self, api_version, capabilities, failure="runtime"):
        self.api_version = api_version
        self._capabilities = capabilities
        self._failure = failure

    def capabilities(self, **_):
        if self._capabilities is None:
            raise RuntimeError("capabilities failed")
        return self._capabilities

    def compile(self, *, schema_plan, offline_registry):
        if self._failure == "configuration":
            raise CodecConfigurationError("The schema is outside this adapter")
        raise RuntimeError("compile failed")


def version_two():
    return Rogue(2, LOOKAROUND)


def other_capabilities():
    return Rogue(1, SchemaCodecCapabilities(dialects=(DIALECT,), vocabularies=(), keywords=("pattern", "type", "x")))


def failing_capabilities():
    return Rogue(1, None)


def failing_compile():
    return Rogue(1, LOOKAROUND)


def refusing_compile():
    return Rogue(1, LOOKAROUND, "configuration")
