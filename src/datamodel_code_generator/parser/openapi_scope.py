"""Declaration-aware model collection for the explicitly selected API scope."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Generator, Sequence  # noqa: TC003 - Public annotations support get_type_hints().
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003 - Public annotations support get_type_hints().
from typing import ClassVar, Literal, Protocol, TypeAlias
from urllib.parse import ParseResult  # noqa: TC003 - Public annotations support get_type_hints().

from typing_extensions import Unpack

from datamodel_code_generator import Error, OpenAPIScope, SchemaParseError
from datamodel_code_generator._source import YamlValue  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator._types import OpenAPIParserConfigDict  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.config import OpenAPIParserConfig  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser._api_reference import (
    ApiDeclarationId,
    ApiModelResolver,
    canonical_ref,
    check_pointer_interpretation,
    pointer_fragment,
    pointer_tokens,
)
from datamodel_code_generator.parser.base import Result  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser.openapi import OPERATION_NAMES, OpenAPIParser, ReferenceObject
from datamodel_code_generator.parser.openapi_media import (
    MediaOwner,
    encoding_media,
)
from datamodel_code_generator.reference import ModelResolver
from datamodel_code_generator.types import DataType  # noqa: TC001 - Public annotations support get_type_hints().


def _mapping(value: YamlValue, path: Sequence[str]) -> dict[str, YamlValue]:
    """Validate a raw object at its declaration boundary without copying it."""
    if isinstance(value, dict):
        return value
    msg = "API_INVALID_OBJECT: expected an object"
    raise SchemaParseError(msg, path=list(path))


SchemaRole: TypeAlias = Literal[
    "schema",
    "parameter",
    "request_body",
    "response_body",
    "response_header",
    "request_encoding_header",
    "response_encoding_header",
]


@dataclass(frozen=True, slots=True)
class ApiDeclarationFrame:
    """Borrow declaration context only while the existing model engine processes it."""

    declaration: ApiDeclarationId
    raw_document: dict[str, YamlValue]
    candidate_name: str
    root_use_site: ApiDeclarationId
    role: SchemaRole
    phase: Literal["operation", "schema", "file"]
    original_parameters: tuple[tuple[ApiDeclarationId, ...], ...] = ()
    effective_parameters: tuple[ApiDeclarationId, ...] = ()
    engine_path: tuple[str, ...] = ()
    raw_schema: YamlValue = None
    validated_schema: JsonSchemaObject | None = None
    projection: Literal["value", "item_stream_array"] = "value"
    raw_operation: dict[str, YamlValue] | None = None
    effective_operation: dict[str, YamlValue] | None = None


@dataclass(frozen=True, slots=True)
class _ApiObject:
    """Borrow a resolved object together with its actual declaration document."""

    declaration: ApiDeclarationId
    document: dict[str, YamlValue]
    value: dict[str, YamlValue]

    @property
    def path(self) -> list[str]:
        """Return raw resolver tokens without reconstructing them from names."""
        return [self.declaration.document, "#", *self.declaration.tokens]


@dataclass(frozen=True, slots=True)
class ApiParameterDeclaration:
    """Keep the original array occurrence separate from its resolved declaration."""

    occurrence: ApiDeclarationId
    raw: dict[str, YamlValue]
    target: _ApiObject
    key: tuple[str, str]
    name: str


@dataclass(frozen=True, slots=True)
class ApiIgnoredDeclaration:
    """Describe an ignored source region without retaining its children or values."""

    declaration: ApiDeclarationId
    use_site: ApiDeclarationId | None
    reason: Literal[
        "oas_encoding_request_body_only",
        "oas_encoding_media_not_applicable",
        "oas_non_multipart_encoding_headers",
        "oas_header_ignored",
    ]
    owner: MediaOwner
    media: str | None = None
    wire_name: str | None = None


class _ResultPostprocessor(Protocol):
    """Retain the existing result postprocessor's keyword-only option."""

    @abstractmethod
    def __call__(
        self, results: dict[tuple[str, ...], Result], *, empty_init: bool = False
    ) -> dict[tuple[str, ...], Result]:
        """Process a result tree without changing Result object identities."""


class ApiOpenAPIParser(OpenAPIParser):
    """Collect API schema declarations through the ordinary model engine."""

    _supports_api_scope: ClassVar[bool] = True
    _model_resolver_factory = staticmethod(ApiModelResolver)
    model_resolver: ApiModelResolver

    @property
    def _result_modules_postprocessor(self) -> _ResultPostprocessor:
        """Allow an empty API inventory while preserving ordinary dot processing."""
        return self._postprocess_api_results

    def _postprocess_api_results(
        self, results: dict[tuple[str, ...], Result], *, empty_init: bool = False
    ) -> dict[tuple[str, ...], Result]:
        if not results:
            return results
        return super()._result_modules_postprocessor(results, empty_init=empty_init)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        config: OpenAPIParserConfig | None = None,
        **options: Unpack[OpenAPIParserConfigDict],
    ) -> None:
        """Initialize declaration ownership exclusively on the API path."""
        self.declaration_frames: list[ApiDeclarationFrame] = []
        self._processed_declarations: set[ApiDeclarationId] = set()
        self._projected_declarations: set[ApiDeclarationId] = set()
        self._declaration_types: dict[ApiDeclarationId, DataType] = {}
        self._api_roots: set[str] = set()
        self._api_security: YamlValue = None
        self.declaration_uses: list[tuple[ApiDeclarationId, ApiDeclarationId, dict[str, YamlValue]]] = []
        self.ignored_declarations: list[ApiIgnoredDeclaration] = []
        self._active_api_objects: set[ApiDeclarationId] = set()
        self._completed_api_objects: set[ApiDeclarationId] = set()
        # Parser._iter_source_uncached supports mappings; the legacy constructor annotation omits them.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            source,  # type: ignore[arg-type]
            config=config,
            **options,
        )
        self._api_documents: dict[str, dict[str, YamlValue]] = self.model_resolver.loaded_documents
        if OpenAPIScope.Api not in self.open_api_scopes:
            msg = "ApiOpenAPIParser requires OpenAPIScope.Api"
            raise Error(msg)

    def _declaration_id(self, path: Sequence[str]) -> ApiDeclarationId:
        ref = self.model_resolver.join_path(tuple(path))
        return ApiDeclarationId(ref.partition("#")[0], pointer_tokens(ref) or ())

    @contextmanager
    def _schema_frame(  # noqa: PLR0913
        self,
        name: str,
        path: list[str],
        raw: dict[str, YamlValue],
        *,
        phase: Literal["schema", "file"] = "schema",
        declaration: ApiDeclarationId | None = None,
        raw_schema: YamlValue = None,
        validated_schema: JsonSchemaObject | None = None,
        projection: Literal["value", "item_stream_array"] = "value",
        role: SchemaRole | None = None,
    ) -> Generator[ApiDeclarationFrame, None, None]:
        parent = self.declaration_frames[-1] if self.declaration_frames else None
        if declaration is None:
            if parent and self.model_resolver.join_path(parent.engine_path) == self.model_resolver.join_path(
                tuple(path)
            ):
                declaration, projection = parent.declaration, parent.projection
            else:
                declaration = self._declaration_id(path)
        frame = ApiDeclarationFrame(
            declaration,
            raw,
            name,
            parent.root_use_site if parent else declaration,
            role or (parent.role if parent and parent.declaration == declaration else "schema"),
            phase,
            engine_path=tuple(path),
            raw_schema=raw_schema,
            validated_schema=validated_schema,
            projection=projection,
        )
        self.declaration_frames.append(frame)
        try:
            yield frame
        finally:
            self.declaration_frames.pop()

    def _parse_raw_or_validated_obj(
        self,
        name: str,
        raw: YamlValue,
        path: list[str],
        validated_obj: JsonSchemaObject | None = None,
    ) -> None:
        """Carry the actual raw-entry declaration through validation and parsing."""
        with self._schema_frame(name, path, self.raw_obj, raw_schema=raw, validated_schema=validated_obj) as frame:
            if "" in frame.declaration.tokens:
                msg = "API_EMPTY_DECLARATION_TOKEN"
                raise SchemaParseError(msg, path=path)
            super()._parse_raw_or_validated_obj(name, raw, path, validated_obj)  # pyright: ignore[reportUnknownMemberType]
        (self._projected_declarations if frame.projection == "item_stream_array" else self._processed_declarations).add(
            frame.declaration
        )

    def parse_obj(self, name: str, obj: JsonSchemaObject, path: list[str]) -> None:
        """Accept only an owned entry frame or an actual reserved declaration."""
        if self.declaration_frames and self.model_resolver.join_path(
            (frame := self.declaration_frames[-1]).engine_path
        ) == self.model_resolver.join_path(tuple(path)):
            super().parse_obj(frame.candidate_name, obj, path)  # pyright: ignore[reportUnknownMemberType]
            (
                self._projected_declarations
                if frame.projection == "item_stream_array"
                else self._processed_declarations
            ).add(frame.declaration)
            return
        ref = canonical_ref(ModelResolver.join_path(tuple(path)))
        if ref in self.reserved_refs.get(tuple(self.model_resolver.current_root), ()):
            raw = self._api_documents.get(ref.partition("#")[0], self.raw_obj)
            tokens = self._checked_pointer(raw, ref)
            reserved_path = [*self.model_resolver.current_root, "#", *tokens]
            candidate = tokens[-1] if tokens else name
            with self._schema_frame(candidate, reserved_path, raw):
                super().parse_obj(candidate, obj, reserved_path)  # pyright: ignore[reportUnknownMemberType]
            self._processed_declarations.add(self._declaration_id(reserved_path))
            return
        msg = "API_DECLARATION_CONTEXT_REQUIRED"
        raise SchemaParseError(msg, path=path)

    def _checked_pointer(self, raw: dict[str, YamlValue], ref: str) -> list[str]:
        """Reject ambiguous legacy recovery using already-loaded source mappings."""
        tokens = pointer_tokens(ref)
        if tokens is None:
            msg = "API_DECLARATION_CONTEXT_REQUIRED"
            raise SchemaParseError(msg, path=[ref])
        for original in self.model_resolver.original_refs.get(ref, (ref,)):
            check_pointer_interpretation(raw, original, ref)
        if "" in tokens:
            msg = "API_EMPTY_DECLARATION_TOKEN"
            raise SchemaParseError(msg, path=[ref])
        return list(tokens)

    def _resolve_api_object(self, value: YamlValue, path: list[str]) -> _ApiObject:
        """Resolve each object-reference edge once, retaining its declaration identity."""
        target = _ApiObject(self._declaration_id(path), self.raw_obj, _mapping(value, path))
        if "$ref" not in target.value:
            return target
        seen: set[str] = set()
        while "$ref" in target.value:
            if not isinstance(ref := target.value["$ref"], str):
                msg = "API_INVALID_OBJECT: reference must be a string"
                raise SchemaParseError(msg, path=target.path)
            with self._api_object_context(target):
                resolved = self.model_resolver.resolve_ref(ref)
                if resolved in seen:
                    msg = "API_REFERENCE_CYCLE: object references do not reach a declaration"
                    raise SchemaParseError(msg, path=target.path)
                seen.add(resolved)
                document = resolved.partition("#")[0]
                raw_document = self._api_documents.get(document)
                if raw_document is None:
                    raw_document = self._get_ref_body(document) if document else self.raw_obj
                    self._api_documents[document] = raw_document
                tokens = self._checked_pointer(raw_document, resolved)
                raw = self._get_model_by_json_pointer(raw_document, tokens, resolved) if tokens else raw_document
                target = _ApiObject(ApiDeclarationId(document, tuple(tokens)), raw_document, _mapping(raw, [resolved]))
        return target

    def get_ref_model(self, ref: str) -> dict[str, YamlValue]:
        """Use the same object-reference boundary for ordinary parser leaves."""
        return self._resolve_api_object({"$ref": ref}, [*self.model_resolver.current_root, "#"]).value

    @contextmanager
    def _api_object_context(self, target: _ApiObject) -> Generator[None, None, None]:
        """Borrow external declaration context and restore it even after failure."""
        if target.document is self.raw_obj and target.declaration.document == "/".join(
            self.model_resolver.current_root
        ):
            yield
            return
        previous = self.raw_obj
        self.raw_obj = target.document
        try:
            with (
                self._inherited_ref_context(target.declaration.document + "#"),
                self.openapi_self_context(target.document),
            ):
                yield
        finally:
            self.raw_obj = previous

    def parse_json_pointer(self, raw: dict[str, YamlValue], ref: str, path_parts: list[str]) -> None:
        """Reuse the existing decoded lookup with declaration-side raw tokens."""
        tokens = self._checked_pointer(raw, ref)
        if not tokens:
            reference = self.model_resolver.add_ref(ref)
            with self._schema_frame(reference.name, [ref], raw):
                self.parse_obj(reference.name, self._validate_schema_object(raw, [ref]), [ref])
            return
        models = self._get_model_by_json_pointer(raw, tokens, ref)
        self.parse_raw_obj(tokens[-1], models, [*path_parts, "#", *tokens])

    def _parse_file(  # noqa: PLR0913
        self,
        raw: dict[str, YamlValue],
        obj_name: str,
        path_parts: list[str],
        object_paths: list[str] | None = None,
        reference_paths: list[str] | None = None,  # noqa: ARG002
        *,
        preserve_root_class_name: bool = False,
        ref: str | None = None,
    ) -> None:
        """Let the existing file engine own IDs, resources, definitions, and closure."""
        tokens = object_paths or []
        path = [*path_parts, "#", *tokens]
        canonical = self.model_resolver.join_path(tuple(path))
        self._checked_pointer(raw, ref or canonical)
        with self._schema_frame(obj_name, path, raw, phase="file"):
            super()._parse_file(  # pyright: ignore[reportUnknownMemberType]
                raw,
                obj_name,
                path_parts,
                tokens,
                tokens,
                preserve_root_class_name=preserve_root_class_name,
                ref=canonical if tokens else None,
            )

    def _parse_specification(self, specification: dict[str, YamlValue], path_parts: list[str]) -> None:
        """Visit declaration categories in their fixed API order."""
        _ = self.schema_features
        _ = self._ref_sibling_keywords_enabled
        document = "/".join(path_parts)
        self._api_security = specification.get("security")
        self._api_roots.add(document)
        self._api_documents[document] = specification
        if self.openapi_include_info_version:
            self._update_openapi_info_version(specification)
        self._collect_discriminator_schemas()
        components = _mapping(specification.get("components", {}), [*path_parts, "#/components"])
        for key, value in _mapping(components.get("schemas", {}), ["#/components/schemas"]).items():
            path = [*path_parts, "#/components", "schemas", key]
            if self._declaration_id(path) in self._processed_declarations:
                continue
            self.parse_raw_obj(key, value, path)
            self._processed_declarations.add(self._declaration_id(path))
        categories: tuple[tuple[Literal["parameters", "requestBodies", "responses", "headers"], str], ...] = (
            ("parameters", "Parameter"),
            ("requestBodies", "Request"),
            ("responses", "Response"),
            ("headers", "Header"),
        )
        for category, suffix in categories:
            for key, value in _mapping(components.get(category, {}), ["#/components", category]).items():
                path = [*path_parts, "#/components", category, key]
                target = self._resolve_api_object(value, path)
                name = self._get_model_name(key, "", suffix)
                match category:
                    case "parameters" | "headers":
                        self._walk_parameter(
                            name,
                            target,
                            header=category == "headers",
                            role="response_header" if category == "headers" else "parameter",
                        )
                    case "requestBodies":
                        self._walk_content(name, target, owner="request_body")
                    case _:
                        self._walk_response(name, target)
        self.walk_api_path_items(_mapping(specification.get("paths", {}), ["#/paths"]), [*path_parts, "#/paths"])
        self.walk_api_path_items(
            _mapping(specification.get("webhooks", {}), ["#/webhooks"]), [*path_parts, "#/webhooks"], scope="webhooks"
        )
        self.walk_api_path_items(
            _mapping(components.get("pathItems", {}), ["#/components/pathItems"]),
            [*path_parts, "#/components", "pathItems"],
            scope="pathItems",
        )
        for key, value in _mapping(components.get("callbacks", {}), ["#/components/callbacks"]).items():
            path = [*path_parts, "#/components", "callbacks", key]
            self._walk_callback(
                _mapping(value, path), path, self._get_model_name(key, "", "Callback"), self._declaration_id(path)
            )

    def _collect_discriminator_document(self, raw: dict[str, YamlValue]) -> None:
        """Use the same canonical keys for parent registrations and subtype edges."""
        document = self.model_resolver.join_path(tuple(self.model_resolver.current_root))
        if document in self._discriminator_documents:
            return
        self._discriminator_documents.add(document)
        self._api_documents[document.partition("#")[0]] = raw
        self._register_discriminator_schema("#", raw)
        components = _mapping(raw.get("components", {}), [document, "components"])
        schemas = _mapping(components.get("schemas", {}), [document, "components", "schemas"])
        potential: dict[str, list[str]] = {}
        for name, schema in schemas.items():
            local_ref = "#" + pointer_fragment(("components", "schemas", name))
            match schema:
                case bool():
                    continue
                case dict():
                    self._register_discriminator_schema(local_ref, schema)
                    if isinstance(all_of := schema.get("allOf"), list):
                        refs = [
                            ref
                            for item in all_of
                            if isinstance(item, dict) and isinstance(ref := item.get("$ref"), str)
                        ]
                        if refs:
                            potential[local_ref] = refs
                case _:
                    self._validate_schema_object(schema, [document, "components", "schemas", name])
        for child, refs in potential.items():
            for parent in refs:
                resolved = self.model_resolver.resolve_ref(parent)
                if resolved in self._discriminator_schemas or resolved.partition("#")[0] != document[:-1]:
                    self._discriminator_subtypes[resolved].append(self.model_resolver.resolve_ref(child))

    def _acquire_schema(self, name: str, raw: YamlValue, path: list[str], *, role: SchemaRole) -> None:
        """Generate one declaration, retaining actual direct-reference use types."""
        declaration = self._declaration_id(path)
        if declaration in self._processed_declarations:
            return
        with self._schema_frame(name, path, self.raw_obj, raw_schema=raw, role=role):
            if isinstance(raw, dict) and len(raw) == 1 and isinstance(raw.get("$ref"), str):
                self._declaration_types[declaration] = self._parse_schema_or_ref(
                    name, ReferenceObject.model_validate(raw), path
                )
            elif declaration.document not in self._api_roots:
                self._parse_file(
                    self.raw_obj,
                    name,
                    list(self.model_resolver.current_root),
                    list(declaration.tokens),
                    ref=self.model_resolver.join_path(tuple(path)),
                )
            else:
                self.parse_raw_obj(name, raw, path)
            self._processed_declarations.add(declaration)

    def _acquire_item_schema(
        self, name: str, item: YamlValue, path: list[str], projected: YamlValue, *, role: SchemaRole
    ) -> None:
        """Generate an item declaration and its distinct stream-array projection."""
        item_path = [*path, "itemSchema"]
        declaration = self._declaration_id(item_path)
        if declaration in self._projected_declarations:
            return
        self._acquire_schema(self._get_model_name(name, "", "Item"), item, item_path, role=role)
        # The existing media helper owns this fresh projection, not the source mapping.
        array_schema = _mapping(projected, path)
        array_schema["items"] = {"$ref": "#" + pointer_fragment(declaration.tokens)}
        projected_path = self._media_schema_path(path, from_item_schema=True)
        with self._schema_frame(
            name,
            projected_path,
            self.raw_obj,
            declaration=declaration,
            raw_schema=array_schema,
            projection="item_stream_array",
            role=role,
        ):
            self.parse_raw_obj(name, array_schema, projected_path)

    def _walk_parameter(
        self, name: str, target: _ApiObject, *, header: bool = False, role: SchemaRole = "parameter"
    ) -> None:
        """Collect an individual parameter or header without aggregate wrappers."""
        with self._api_object_context(target):
            raw, path = target.value, target.path
            if not header:
                location = raw.get("in")
                if not isinstance(location, str) or location not in {
                    "path",
                    "query",
                    "header",
                    "cookie",
                    "querystring",
                }:
                    msg = "API_INVALID_OBJECT: invalid parameter location"
                    raise SchemaParseError(msg, path=path)
                if location == "querystring" and (
                    not self.schema_features.querystring_parameter
                    or any(key in raw for key in ("schema", "style", "explode"))
                    or "content" not in raw
                ):
                    msg = "API_INVALID_OBJECT: querystring requires OpenAPI 3.2 and content only"
                    raise SchemaParseError(msg, path=path)
                if (
                    location == "header"
                    and isinstance(wire_name := raw.get("name"), str)
                    and wire_name.lower() in {"accept", "content-type", "authorization"}
                ):
                    self._ignore_declaration(path, "oas_header_ignored", "parameter", wire_name=wire_name)
                    return
                if location != "querystring" and not isinstance(raw.get("name"), str):
                    msg = "API_INVALID_OBJECT: parameter name/in required"
                    raise SchemaParseError(msg, path=path)
            if "schema" in raw and "content" in raw:
                msg = "API_INVALID_OBJECT: schema and content are mutually exclusive"
                raise SchemaParseError(msg, path=path)
            if "schema" in raw:
                self._acquire_schema(name, raw["schema"], [*path, "schema"], role=role)
                return
            content = _mapping(raw.get("content", {}), [*path, "content"])
            if len(content) != 1:
                msg = "API_INVALID_OBJECT: parameter/header content must have one medium"
                raise SchemaParseError(msg, path=path)
            self._walk_content(name, target, owner="header" if header else "parameter", role=role)

    def _parameter_key(self, parameter: YamlValue) -> tuple[str, str] | None:
        """Extend ordinary identity only for version-permitted nameless querystrings."""
        if isinstance(parameter, dict) and parameter.get("in") == "querystring" and "name" not in parameter:
            return "", "querystring"
        return super()._parameter_key(parameter)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]

    def _walk_content(
        self, name: str, target: _ApiObject, *, owner: MediaOwner, role: SchemaRole | None = None
    ) -> None:
        """Use existing media-schema and itemSchema projection decisions."""
        schema_role: SchemaRole = role or ("request_body" if owner == "request_body" else "response_body")
        with self._api_object_context(target):
            for media, value in _mapping(target.value.get("content", {}), [*target.path, "content"]).items():
                media_path = [*target.path, "content", media]
                medium = _mapping(value, media_path)
                if (schema := self._get_raw_media_schema(medium)) is not None:
                    if schema.from_item_schema:
                        self._acquire_item_schema(
                            name, medium["itemSchema"], media_path, schema.schema, role=schema_role
                        )
                    else:
                        self._acquire_schema(name, schema.schema, [*media_path, "schema"], role=schema_role)
                self._walk_encoding(name, medium, media, media_path, owner)

    def _walk_response(self, name: str, target: _ApiObject, *, header_name: str | None = None) -> None:
        """Collect all response media followed by response headers."""
        with self._api_object_context(target):
            self._walk_content(name, target, owner="response")
            for key, value in _mapping(target.value.get("headers", {}), [*target.path, "headers"]).items():
                header_path = [*target.path, "headers", key]
                header = self._resolve_api_object(_mapping(value, header_path), header_path)
                if key.lower() == "content-type":
                    self._ignore_declaration(header_path, "oas_header_ignored", "header", wire_name=key)
                    continue
                with self._api_object_context(header):
                    self._walk_parameter(
                        self._get_model_name((header_name or name) + key, "", "Header"),
                        header,
                        header=True,
                        role="response_header",
                    )

    def _parameter_declarations(self, raw: YamlValue, path: list[str]) -> list[ApiParameterDeclaration]:
        """Resolve each parameter once and retain its original array occurrence."""
        if not isinstance(raw, list):
            msg = "API_INVALID_OBJECT: expected parameter array"
            raise SchemaParseError(msg, path=path)
        declarations: list[ApiParameterDeclaration] = []
        seen: set[tuple[str, str]] = set()
        for index, value in enumerate(raw):
            parameter_path = [*path, str(index)]
            declared = _mapping(value, parameter_path)
            parameter = self._resolve_api_object(declared, parameter_path)
            with self._api_object_context(parameter):
                key = self._parameter_key(parameter.value)
            if key is None:
                msg = "API_INVALID_OBJECT: parameter name/in required"
                raise SchemaParseError(msg, path=parameter.path)
            name, location = key
            if location == "header":
                key = name.lower(), location
            if key in seen:
                msg = "API_INVALID_OBJECT: duplicate parameter identity"
                raise SchemaParseError(msg, path=parameter_path)
            seen.add(key)
            declarations.append(
                ApiParameterDeclaration(self._declaration_id(parameter_path), declared, parameter, key, name)
            )
        return declarations

    def walk_api_path_items(
        self,
        items: dict[str, YamlValue],
        base_path: list[str],
        *,
        scope: Literal["paths", "webhooks", "pathItems"] = "paths",
        prefix: str = "",
    ) -> None:
        """Retain literal route keys and parameter declaration indices during traversal."""
        global_parameters = (
            self._parameter_declarations(items.get("parameters", []), [*base_path, "parameters"])
            if scope == "paths"
            else []
        )
        for item_name, value in items.items():
            if scope == "paths" and (item_name == "parameters" or item_name.startswith("x-")):
                continue
            if scope == "paths" and not self._matches_path_pattern(item_name):
                continue
            path = [*base_path, item_name]
            target = self._resolve_api_object(_mapping(value, path), path)
            candidate = self._get_model_name(item_name, "", "PathItem") if scope == "pathItems" else prefix or item_name
            self._walk_path_item(target, self._declaration_id(path), candidate, global_parameters)

    def _walk_path_item(
        self,
        target: _ApiObject,
        use_site: ApiDeclarationId,
        prefix: str,
        global_parameters: list[ApiParameterDeclaration],
    ) -> None:
        """Visit each path-item declaration once, retaining cyclic/reference edges."""
        self.declaration_uses.append((use_site, target.declaration, target.value))
        if target.declaration in self._active_api_objects or target.declaration in self._completed_api_objects:
            return
        self._active_api_objects.add(target.declaration)
        try:
            with self._api_object_context(target):
                common = (
                    global_parameters,
                    self._parameter_declarations(target.value.get("parameters", []), [*target.path, "parameters"]),
                )
                for operation_tokens, value in self._api_operations(target):
                    path = [*target.path, *operation_tokens]
                    operation = _mapping(value, path)
                    self._walk_api_operation(
                        operation,
                        path,
                        ApiDeclarationId(use_site.document, (*use_site.tokens, *operation_tokens)),
                        prefix,
                        common,
                    )
            self._completed_api_objects.add(target.declaration)
        finally:
            self._active_api_objects.remove(target.declaration)

    def _api_operations(self, target: _ApiObject) -> Generator[tuple[tuple[str, ...], YamlValue], None, None]:
        """Preserve source order and literal locations for OpenAPI 3.2 methods."""
        for method, value in target.value.items():
            if method in OPERATION_NAMES or (method == "query" and self.schema_features.media_item_schema):
                yield (method,), value
                continue
            if method != "additionalOperations" or not self.schema_features.media_item_schema:
                continue
            for additional, operation in _mapping(value, [*target.path, method]).items():
                if (method_name := additional.lower()) in OPERATION_NAMES or method_name == "query":
                    msg = "API_INVALID_OBJECT: fixed methods cannot appear in additionalOperations"
                    raise SchemaParseError(msg, path=[*target.path, method, additional])
                yield (method, additional), operation

    def dispose(self) -> None:
        """Release API-only borrowed documents and use types with the parser graph."""
        try:
            super().dispose()  # pyright: ignore[reportUnknownMemberType]
        finally:
            self.declaration_frames.clear()
            self.declaration_uses.clear()
            self.ignored_declarations.clear()
            self._processed_declarations.clear()
            self._projected_declarations.clear()
            self._declaration_types.clear()
            self._api_documents.clear()
            self._api_roots.clear()
            self._api_security = None
            self._active_api_objects.clear()
            self._completed_api_objects.clear()
            self.model_resolver.original_refs.clear()

    def _walk_api_operation(  # noqa: PLR0914
        self,
        operation: dict[str, YamlValue],
        path: list[str],
        use_site: ApiDeclarationId,
        prefix: str,
        common: tuple[list[ApiParameterDeclaration], list[ApiParameterDeclaration]],
    ) -> None:
        """Process an operation's schema uses before depth-first callbacks and tags."""
        parameters = self._parameter_declarations(operation.get("parameters", []), [*path, "parameters"])
        overridden = {entry.key for entry in parameters}
        common_parameters = (*common[0], *common[1])
        effective = [*parameters, *(entry for entry in common_parameters if entry.key not in overridden)]
        if (querystrings := sum(entry.key[1] == "querystring" for entry in effective)) and (
            querystrings > 1 or any(entry.key[1] == "query" for entry in effective)
        ):
            msg = "API_INVALID_OBJECT: querystring cannot coexist with query or another querystring"
            raise SchemaParseError(msg, path=path)
        operation_id = operation.get("operationId")
        if self.use_operation_id_as_name and not operation_id:
            msg = (
                "All operations must have an operationId when --use_operation_id_as_name is set."
                f"The following path was missing an operationId: {prefix}"
            )
            raise Error(msg)
        name = (
            self._get_model_name(str(operation_id), "", "")
            if self.use_operation_id_as_name
            else self._get_model_name(prefix, path[-1], "")
        )
        effective_operation = operation
        if common_parameters:
            effective_operation = operation.copy()
            effective_operation["parameters"] = [entry.raw for entry in effective]
        if self._api_security is not None and "security" not in operation:
            if effective_operation is operation:
                effective_operation = operation.copy()
            effective_operation["security"] = self._api_security
        frame = ApiDeclarationFrame(
            self._declaration_id(path),
            self.raw_obj,
            name,
            use_site,
            "schema",
            "operation",
            (
                tuple(entry.occurrence for entry in common[0]),
                tuple(entry.occurrence for entry in common[1]),
                tuple(entry.occurrence for entry in parameters),
            ),
            tuple(entry.target.declaration for entry in effective),
            engine_path=tuple(path),
            raw_operation=operation,
            effective_operation=effective_operation,
        )
        self.declaration_frames.append(frame)
        try:
            for entry in effective:
                parameter = entry.target
                wire_name, location = entry.name, entry.key[1]
                candidate = (
                    name
                    + location.capitalize()
                    + (self._get_model_name(wire_name, "", "Parameter") if wire_name else "")
                )
                self._walk_parameter(candidate, parameter)
            if "requestBody" in operation:
                self._walk_content(
                    name + "Request",
                    self._resolve_api_object(operation["requestBody"], [*path, "requestBody"]),
                    owner="request_body",
                )
            for status, response in _mapping(operation.get("responses", {}), [*path, "responses"]).items():
                response_path = [*path, "responses", str(status)]
                suffix = str(status).capitalize() if self.use_status_code_in_response_name else ""
                self._walk_response(
                    name + "Response" + suffix,
                    self._resolve_api_object(response, response_path),
                    header_name=name + "Response" + str(status).capitalize(),
                )
            for callback_name, value in _mapping(operation.get("callbacks", {}), [*path, "callbacks"]).items():
                callback_path = [*path, "callbacks", callback_name]
                self._walk_callback(
                    _mapping(value, callback_path),
                    callback_path,
                    name + self._get_model_name(callback_name, "", "Callback"),
                    use_site,
                )
            if OpenAPIScope.Tags in self.open_api_scopes:
                tags = operation.get("tags", [])
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    msg = "API_INVALID_OBJECT: expected string tags"
                    raise SchemaParseError(msg, path=path)
                self.parse_tags(name + "Tags", [str(tag) for tag in tags], [*path, "tags"])
        finally:
            self.declaration_frames.pop()

    def _walk_callback(
        self, raw: dict[str, YamlValue], path: list[str], prefix: str, use_site: ApiDeclarationId
    ) -> None:
        """Traverse callback expressions in declaration order without expanding cycles."""
        target = self._resolve_api_object(raw, path)
        self.declaration_uses.append((use_site, target.declaration, target.value))
        if target.declaration in self._active_api_objects or target.declaration in self._completed_api_objects:
            return
        self._active_api_objects.add(target.declaration)
        try:
            with self._api_object_context(target):
                ordinal = 0
                for expression, value in target.value.items():
                    if expression.startswith("x-"):
                        continue
                    ordinal += 1
                    expression_path = [*target.path, expression]
                    child = self._resolve_api_object(_mapping(value, expression_path), expression_path)
                    child_use = ApiDeclarationId(
                        use_site.document, (*use_site.tokens, "callbacks", path[-1], expression)
                    )
                    self._walk_path_item(child, child_use, prefix + str(ordinal), [])
            self._completed_api_objects.add(target.declaration)
        finally:
            self._active_api_objects.remove(target.declaration)

    def _ignore_declaration(
        self,
        path: list[str],
        reason: Literal[
            "oas_encoding_request_body_only",
            "oas_encoding_media_not_applicable",
            "oas_non_multipart_encoding_headers",
            "oas_header_ignored",
        ],
        owner: MediaOwner,
        *,
        media: str | None = None,
        wire_name: str | None = None,
    ) -> None:
        """Retain only the ignored occurrence and its finite applicability reason."""
        use_site = self.declaration_frames[-1].root_use_site if self.declaration_frames else None
        self.ignored_declarations.append(
            ApiIgnoredDeclaration(self._declaration_id(path), use_site, reason, owner, media, wire_name)
        )

    def _walk_encoding(
        self, name: str, medium: dict[str, YamlValue], media: str, path: list[str], owner: MediaOwner
    ) -> None:
        """Acquire only applicable multipart encoding headers in source order."""
        if "encoding" not in medium:
            return
        try:
            parsed_media = encoding_media(media, owner, openapi_32=self.schema_features.media_item_schema)
        except ValueError as exc:
            raise SchemaParseError(str(exc), path=path, original_error=exc) from exc
        encoding_path = [*path, "encoding"]
        if isinstance(parsed_media, str):
            self._ignore_declaration(encoding_path, parsed_media, owner, media=media)
            return
        for property_name, value in _mapping(medium["encoding"], encoding_path).items():
            property_path = [*encoding_path, property_name]
            encoding = _mapping(value, property_path)
            headers = encoding.get("headers", {})
            if parsed_media.type != "multipart":
                if isinstance(headers, dict):
                    for key in headers:
                        self._ignore_declaration(
                            [*property_path, "headers", key],
                            "oas_non_multipart_encoding_headers",
                            owner,
                            media=media,
                            wire_name=key,
                        )
                continue
            for key, header_value in _mapping(headers, [*property_path, "headers"]).items():
                header_path = [*property_path, "headers", key]
                header = self._resolve_api_object(_mapping(header_value, header_path), header_path)
                if key.lower() == "content-type":
                    self._ignore_declaration(header_path, "oas_header_ignored", owner, media=media, wire_name=key)
                    continue
                with self._api_object_context(header):
                    candidate = (
                        name
                        + self._get_model_name(property_name, "", "")
                        + self._get_model_name(key, "", "EncodingHeader")
                    )
                    self._walk_parameter(
                        candidate,
                        header,
                        header=True,
                        role="response_encoding_header" if owner == "response" else "request_encoding_header",
                    )
