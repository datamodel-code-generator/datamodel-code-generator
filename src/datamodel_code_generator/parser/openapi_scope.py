"""Declaration-aware model collection for the explicitly selected API scope."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Generator, Sequence  # noqa: TC003 - Public annotations support get_type_hints().
from contextlib import contextmanager
from pathlib import Path  # noqa: TC003 - Public annotations support get_type_hints().
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, TypeAlias, cast
from urllib.parse import ParseResult  # noqa: TC003 - Public annotations support get_type_hints().

from typing_extensions import Unpack, override

from datamodel_code_generator import Error, OpenAPIScope, SchemaParseError
from datamodel_code_generator._records import record
from datamodel_code_generator._source import YamlValue  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator._target_unicode import replace_non_word
from datamodel_code_generator._types import OpenAPIParserConfigDict  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.config import OpenAPIParserConfig  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser._api_reference import (
    ApiModelResolver,
    canonical_ref,
    check_pointer_interpretation,
    pointer_fragment,
    pointer_tokens,
)
from datamodel_code_generator.parser._schema_graph import RefOrigin
from datamodel_code_generator.parser.base import Result  # noqa: TC001 - Public annotations support get_type_hints().
from datamodel_code_generator.parser.jsonschema import (
    JsonSchemaObject,
    _locate,
)
from datamodel_code_generator.parser.openapi import OPERATION_NAMES, OpenAPIParser, ReferenceObject
from datamodel_code_generator.parser.openapi_media import (
    MediaOwner,
    encoding_applies,
    encoding_media,
)
from datamodel_code_generator.reference import ModelResolver, SchemaLocation
from datamodel_code_generator.types import DataType  # noqa: TC001 - Public annotations support get_type_hints().

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from datamodel_code_generator.model import DataModel

_FIXED_HTTP_METHODS = frozenset(method.upper() for method in (*OPERATION_NAMES, "query"))


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


@record
class ApiDeclarationFrame:
    """Borrow declaration context only while the existing model engine processes it."""

    declaration: SchemaLocation
    raw_document: dict[str, YamlValue]
    candidate_name: str
    root_use_site: SchemaLocation
    role: SchemaRole
    phase: Literal["operation", "schema", "file"]
    original_parameters: tuple[tuple[SchemaLocation, ...], ...] = ()
    effective_parameters: tuple[SchemaLocation, ...] = ()
    engine_path: tuple[str, ...] = ()
    raw_schema: YamlValue = None
    validated_schema: JsonSchemaObject | None = None
    projection: Literal["value", "item_stream_array"] = "value"
    raw_operation: dict[str, YamlValue] | None = None
    effective_operation: dict[str, YamlValue] | None = None


@record
class _ApiObject:
    """Borrow a resolved object together with its actual declaration document."""

    declaration: SchemaLocation
    document: dict[str, YamlValue]
    value: dict[str, YamlValue]

    @property
    def path(self) -> list[str]:
        """Return raw resolver tokens without reconstructing them from names."""
        return [self.declaration.document, "#", *self.declaration.tokens]


@record
class ApiParameterDeclaration:
    """Keep the original array occurrence separate from its resolved declaration."""

    occurrence: SchemaLocation
    raw: dict[str, YamlValue]
    target: _ApiObject
    key: tuple[str, str]
    name: str


@record
class ApiIgnoredDeclaration:
    """Describe an ignored source region without retaining its children or values."""

    declaration: SchemaLocation
    use_site: SchemaLocation | None
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
        self._processed_declarations: set[SchemaLocation] = set()
        self._projected_declarations: set[SchemaLocation] = set()
        self._declaration_types: dict[SchemaLocation, DataType] = {}
        self._api_roots: set[str] = set()
        self._api_security: YamlValue = None
        self.declaration_uses: list[tuple[SchemaLocation, SchemaLocation, dict[str, YamlValue]]] = []
        self.ignored_declarations: list[ApiIgnoredDeclaration] = []
        self._active_api_objects: set[SchemaLocation] = set()
        self._completed_api_objects: set[SchemaLocation] = set()
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
        self._schema_root_type = self.data_model_root_type
        self._parameter_root_type = self.data_model_type.resolve_type_alias_model_type(
            self.data_model_root_type, self.target_python_version
        )
        self._parameter_root_models: set[DataModel] = set()
        self._claimed_models = 0

    def _get_model_name(self, path_name: str, method: str, suffix: str) -> str:
        """Drop leading separators of a name part instead of prefixing ``Field``; ``/`` alone becomes ``Root``."""
        stripped = replace_non_word(path_name, self.target_python_version).lstrip("_") or "root"
        return super()._get_model_name(stripped, method, suffix)

    def _declared(self, value: object) -> SchemaLocation:
        """Return the location L0 recorded for a JSON object of a loaded document; a value below it is ``.child()``."""
        return cast("SchemaLocation", self._raw_location(value))

    @contextmanager
    def _schema_frame(  # noqa: PLR0913
        self,
        name: str,
        path: list[str],
        raw: dict[str, YamlValue],
        *,
        phase: Literal["schema", "file"] = "schema",
        declaration: SchemaLocation | None = None,
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
                declaration = cast("SchemaLocation", cast("JsonSchemaObject", validated_schema).schema_location)
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
        self._claim_parameter_root_models()
        self.declaration_frames.append(frame)
        previous = self.data_model_root_type
        self.data_model_root_type = self._parameter_root_type if frame.role == "parameter" else self._schema_root_type
        try:
            yield frame
        finally:
            self._claim_parameter_root_models()
            self.declaration_frames.pop()
            self.data_model_root_type = previous

    def _claim_parameter_root_models(self) -> None:
        """Record the parameter aliases registered since the previous declaration frame boundary."""
        models = self.generation_store.models
        if self.data_model_root_type is not self._schema_root_type:
            self._parameter_root_models.update(
                model for model in models[self._claimed_models :] if isinstance(model, self.data_model_root_type)
            )
        self._claimed_models = len(models)

    def _is_root_model(self, model: DataModel) -> bool:
        """Treat parameter aliases as roots, as --use-type-alias does."""
        return isinstance(model, self.data_model_root_type) or model in self._parameter_root_models

    def _parse_raw_or_validated_obj(
        self,
        name: str,
        raw: YamlValue,
        path: list[str],
        validated_obj: JsonSchemaObject | None = None,
    ) -> None:
        """Carry the actual raw-entry declaration through validation and parsing.

        A boolean schema, which has no location of its own, is located at the declaration its holder gives the frame,
        or where the validated schema that the base parser located at its holder says.
        """
        with self._schema_frame(name, path, self.raw_obj, raw_schema=raw, validated_schema=validated_obj) as frame:
            if "" in frame.declaration.tokens:
                msg = "API_EMPTY_DECLARATION_TOKEN"
                raise SchemaParseError(msg, path=path)
            if validated_obj is None and isinstance(raw, bool):
                validated_obj = _locate(self._validate_schema_object(raw, path), frame.declaration)
            super()._parse_raw_or_validated_obj(name, raw, path, validated_obj)  # pyright: ignore[reportUnknownMemberType]
        (self._projected_declarations if frame.projection == "item_stream_array" else self._processed_declarations).add(
            frame.declaration
        )

    @override
    def _parse_raw_at(self, name: str, raw: YamlValue, path: list[str], location: SchemaLocation | None) -> None:
        """Parse a raw schema as the declaration at ``location``, which its holder gives."""
        with self._schema_frame(name, path, self.raw_obj, declaration=location, raw_schema=raw):
            super()._parse_raw_at(name, raw, path, location)

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
            declaration = self._declared(raw).child(*tokens)
            with self._schema_frame(candidate, reserved_path, raw, declaration=declaration):
                super().parse_obj(candidate, obj, reserved_path)  # pyright: ignore[reportUnknownMemberType]
            self._processed_declarations.add(declaration)
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
        raw = _mapping(value, path)
        return self._follow_references(_ApiObject(self._declared(raw), self.raw_obj, raw), set())

    def _follow_references(self, target: _ApiObject, seen: set[str]) -> _ApiObject:
        """Follow an object's references, each in the context of the document that spells it."""
        while "$ref" in target.value:
            if not isinstance(ref := target.value["$ref"], str):
                msg = "API_INVALID_OBJECT: reference must be a string"
                raise SchemaParseError(msg, path=target.path)
            with self._api_object_context(target):
                target = self._reference_target(ref, seen, target.path)
        return target

    def _reference_target(self, ref: str, seen: set[str], path: list[str]) -> _ApiObject:
        """Resolve one reference in the current document context to the object it names."""
        resolved = self._ref_path(ref)
        if resolved in seen:
            msg = "API_REFERENCE_CYCLE: object references do not reach a declaration"
            raise SchemaParseError(msg, path=path)
        seen.add(resolved)
        document = resolved.partition("#")[0]
        raw_document = self._api_documents.get(document)
        if raw_document is None:
            raw_document = self._get_ref_body(document) if document else self.raw_obj
            self._api_documents[document] = raw_document
        tokens = self._checked_pointer(raw_document, resolved)
        raw = self._get_model_by_json_pointer(raw_document, tokens, resolved) if tokens else raw_document
        self._check_ref_target("_reference_target", ref, self._raw_location(raw))
        return _ApiObject(SchemaLocation(document, tuple(tokens)), raw_document, _mapping(raw, [resolved]))

    def get_ref_model(self, ref: str) -> dict[str, YamlValue]:
        """Use the same object-reference boundary for ordinary parser leaves, resolving in the current context."""
        seen: set[str] = set()
        return self._follow_references(self._reference_target(ref, seen, [ref]), seen).value

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
            with self._schema_frame(reference.name, [ref], raw, declaration=self._declared(raw)):
                self.parse_obj(reference.name, self._validate_schema_object(raw, [ref]), [ref])
            return
        models = self._get_model_by_json_pointer(raw, tokens, ref)
        self._parse_raw_at(tokens[-1], models, [*path_parts, "#", *tokens], self._member_location(raw, *tokens))

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
        with self._schema_frame(obj_name, path, raw, phase="file", declaration=self._declared(raw).child(*tokens)):
            super()._parse_file(  # pyright: ignore[reportUnknownMemberType]
                raw,
                obj_name,
                path_parts,
                tokens,
                tokens,
                preserve_root_class_name=preserve_root_class_name,
                ref=canonical if tokens else None,
            )

    @override
    def _document_self_context(self, document: str, raw: dict[str, YamlValue]) -> AbstractContextManager[None]:
        """Return the context in which an API document names itself by its OpenAPI 3.2 `$self`, as each one does."""
        return self.openapi_self_context(raw)

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
        schemas = _mapping(components.get("schemas", {}), ["#/components/schemas"])
        for key in schemas:
            if (declaration := self._declared(schemas).child(key)) in self._processed_declarations:
                continue
            self._parse_raw_member(key, schemas, key, [*path_parts, "#/components", "schemas", key])
            self._processed_declarations.add(declaration)
        categories: tuple[tuple[Literal["parameters", "requestBodies", "responses", "headers"], str], ...] = (
            ("parameters", "Parameter"),
            ("requestBodies", "Request"),
            ("responses", "Response"),
            ("headers", "Header"),
        )
        for category, suffix in categories:
            entries = _mapping(components.get(category, {}), ["#/components", category])
            for key, value in self._named_components(entries, [*path_parts, "#/components", category]).items():
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
        path_items = _mapping(components.get("pathItems", {}), ["#/components/pathItems"])
        self.walk_api_path_items(
            self._named_components(path_items, [*path_parts, "#/components", "pathItems"]),
            [*path_parts, "#/components", "pathItems"],
            scope="pathItems",
        )
        callbacks = _mapping(components.get("callbacks", {}), ["#/components/callbacks"])
        for key, value in self._named_components(callbacks, [*path_parts, "#/components", "callbacks"]).items():
            path = [*path_parts, "#/components", "callbacks", key]
            callback = _mapping(value, path)
            self._walk_callback(callback, path, self._get_model_name(key, "", "Callback"), self._declared(callback))

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
        potential: dict[str, tuple[dict[str, YamlValue], list[str]]] = {}
        for name, schema in self._named_components(schemas, [document, "components", "schemas"]).items():
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
                            potential[local_ref] = schema, refs
                case _:
                    self._validate_member(
                        schema, [document, "components", "schemas", name], self._member_location(schemas, name)
                    )
        for child, (schema, refs) in potential.items():
            for parent in refs:
                resolved = self._ref_path(parent)
                if resolved in self._discriminator_schemas or resolved.partition("#")[0] != document[:-1]:
                    self._discriminator_subtypes[resolved].append(
                        self._schema_key(schema, child, "allOf", RefOrigin.DISCRIMINATOR_SUBTYPE)
                    )

    def _acquire_schema(
        self, name: str, raw: YamlValue, path: list[str], *, declaration: SchemaLocation, role: SchemaRole
    ) -> None:
        """Generate one declaration, retaining actual direct-reference use types.

        A reference whose siblings are only annotations or extensions is used as the model it names, as a property is,
        except that a reference beside only ``nullable: true`` keeps its derived model here instead of becoming
        ``Optional``. ``declaration`` is the location the schema's holder gives it.
        """
        if declaration in self._processed_declarations:
            return
        with self._schema_frame(name, path, self.raw_obj, declaration=declaration, raw_schema=raw, role=role):
            ref, validated = self._reference_use(raw, path)
            if ref is not None:
                self._declaration_types[declaration] = self._parse_schema_or_ref(
                    name, ReferenceObject.model_validate({"$ref": ref}), path
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
                self._parse_raw_or_validated_obj(name, raw, path, validated)
            self._processed_declarations.add(declaration)

    def _reference_use(self, raw: YamlValue, path: list[str]) -> tuple[str | None, JsonSchemaObject | None]:
        """Return the reference ``raw`` is used as, if any, and ``raw`` validated when a reference has siblings.

        A reference is used as the model it names unless a sibling keyword constrains it.
        """
        if not isinstance(raw, dict) or not isinstance(ref := raw.get("$ref"), str):
            return None, None
        validated = self._validate_schema_object(raw, path) if len(raw) > 1 else None
        return (None if validated and validated.has_ref_with_schema_keywords else ref), validated

    def _acquire_item_schema(  # ruff: ignore[too-many-arguments]
        self,
        name: str,
        item: YamlValue,
        path: list[str],
        projected: YamlValue,
        *,
        declaration: SchemaLocation,
        role: SchemaRole,
    ) -> None:
        """Generate an item declaration, located by its media type object, and its stream-array projection."""
        item_path = [*path, "itemSchema"]
        if declaration in self._projected_declarations:
            return
        self._acquire_schema(
            self._get_model_name(name, "", "Item"), item, item_path, declaration=declaration, role=role
        )
        # The existing media helper owns this fresh projection, not the source mapping.
        array_schema = _mapping(projected, path)
        item_ref = "#" + pointer_fragment(declaration.tokens)
        array_schema["items"] = {
            "$ref": self._derived_ref(item_ref, declaration, "itemSchema", item_ref, RefOrigin.ITEM_SCHEMA)
        }
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
                    self._ignore_declaration(target.declaration, "oas_header_ignored", "parameter", wire_name=wire_name)
                    return
                if location != "querystring" and not isinstance(raw.get("name"), str):
                    msg = "API_INVALID_OBJECT: parameter name/in required"
                    raise SchemaParseError(msg, path=path)
            if "schema" in raw and "content" in raw:
                msg = "API_INVALID_OBJECT: schema and content are mutually exclusive"
                raise SchemaParseError(msg, path=path)
            if "schema" in raw:
                schema_path, declaration = [*path, "schema"], self._declared(raw).child("schema")
                self._acquire_schema(name, raw["schema"], schema_path, declaration=declaration, role=role)
                self._record_boolean_schema_location(raw["schema"], schema_path, declaration)
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
            content_path = [*target.path, "content"]
            content = _mapping(target.value.get("content", {}), content_path)
            for media, value in content.items():
                media_path = [*content_path, media]
                medium = _mapping(value, media_path)
                if not isinstance(media, str) and self._reads_media_type(medium, [*content_path, str(media)], owner):
                    self._key_name(content, media, content_path, "media type")
                if (schema := self._get_raw_media_schema(medium)) is not None:
                    if schema.from_item_schema:
                        declaration = self._declared(medium).child("itemSchema")
                        self._acquire_item_schema(
                            name,
                            medium["itemSchema"],
                            media_path,
                            schema.schema,
                            declaration=declaration,
                            role=schema_role,
                        )
                        self._record_boolean_schema_location(
                            medium["itemSchema"], [*media_path, "itemSchema"], declaration
                        )
                    else:
                        declaration = self._declared(medium).child("schema")
                        self._acquire_schema(
                            name, schema.schema, [*media_path, "schema"], declaration=declaration, role=schema_role
                        )
                        self._record_boolean_schema_location(schema.schema, [*media_path, "schema"], declaration)
                self._walk_encoding(name, medium, media, media_path, owner)

    def _reads_media_type(self, medium: dict[str, YamlValue], path: list[str], owner: MediaOwner) -> bool:
        """Return whether walking ``medium`` at ``path`` builds a schema path from its media type or parses it.

        Its item schema, a schema not used as the model its reference names, and an encoding that applies do.
        """
        if (schema := self._get_raw_media_schema(medium)) is not None and (
            schema.from_item_schema or self._reference_use(schema.schema, [*path, "schema"])[0] is None
        ):
            return True
        return "encoding" in medium and encoding_applies(owner, openapi_32=self.schema_features.media_item_schema)

    def _walk_response(self, name: str, target: _ApiObject, *, header_name: str | None = None) -> None:
        """Collect all response media followed by response headers."""
        with self._api_object_context(target):
            self._walk_content(name, target, owner="response")
            headers_path = [*target.path, "headers"]
            headers = _mapping(target.value.get("headers", {}), headers_path)
            for key, value in headers.items():
                if self._key_name(headers, key, headers_path, "header name").lower() == "content-type":
                    self._ignore_declaration(
                        self._declared(target.value).child("headers", key),
                        "oas_header_ignored",
                        "header",
                        wire_name=key,
                    )
                    continue
                header = self._resolve_api_object(value, [*target.path, "headers", key])
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
            declarations.append(ApiParameterDeclaration(self._declared(declared), declared, parameter, key, name))
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
            if scope == "paths" and (
                self._key_name(items, item_name, base_path, "path") == "parameters" or item_name.startswith("x-")
            ):
                continue
            if scope == "paths" and not self._matches_path_pattern(item_name):
                continue
            path = [*base_path, item_name]
            item = _mapping(value, path)
            target = self._resolve_api_object(item, path)
            candidate = self._get_model_name(item_name, "", "PathItem") if scope == "pathItems" else prefix or item_name
            self._walk_path_item(target, self._declared(item), candidate, global_parameters)

    def _walk_path_item(
        self,
        target: _ApiObject,
        use_site: SchemaLocation,
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
                    if not self.use_operation_id_as_name:
                        self._validate_operation_name_keys(prefix, operation_tokens, use_site, target.declaration)
                    self._walk_api_operation(
                        operation,
                        path,
                        use_site.child(*operation_tokens),
                        prefix,
                        common,
                    )
            self._completed_api_objects.add(target.declaration)
        finally:
            self._active_api_objects.remove(target.declaration)

    def _validate_operation_name_keys(
        self, prefix: str, operation_tokens: tuple[str, ...], use_site: SchemaLocation, declaration: SchemaLocation
    ) -> None:
        """Require the keys an operation's model name is built from to be strings: the item's prefix and the method.

        Only a webhook name, the prefix of the path item at ``use_site``, can be another value by then. The method is
        written in the path item's ``declaration``, which a reference can place elsewhere than ``use_site``.
        """
        if not isinstance(prefix, str):
            raise self._key_error(prefix, "webhook name", [str(use_site)])
        if not isinstance(method := operation_tokens[-1], str):
            raise self._key_error(method, "method", [str(declaration.child(*operation_tokens[:-1], str(method)))])

    def _api_operations(self, target: _ApiObject) -> Generator[tuple[tuple[str, ...], YamlValue], None, None]:
        """Preserve source order and literal locations for OpenAPI 3.2 methods."""
        for method, value in target.value.items():
            if method in OPERATION_NAMES or (method == "query" and self.schema_features.media_item_schema):
                yield (method,), value
                continue
            if method != "additionalOperations" or not self.schema_features.media_item_schema:
                continue
            for additional, operation in _mapping(value, [*target.path, method]).items():
                if additional in _FIXED_HTTP_METHODS:
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
            self._parameter_root_models.clear()
            self._claimed_models = 0
            self.model_resolver.original_refs.clear()

    def _walk_api_operation(  # noqa: PLR0914
        self,
        operation: dict[str, YamlValue],
        path: list[str],
        use_site: SchemaLocation,
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
            self._declared(operation),
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
            callbacks_path = [*path, "callbacks"]
            callbacks = _mapping(operation.get("callbacks", {}), callbacks_path)
            for callback_name, value in callbacks.items():
                callback_path = [
                    *callbacks_path,
                    self._key_name(callbacks, callback_name, callbacks_path, "callback name"),
                ]
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

    def _walk_callback(self, raw: dict[str, YamlValue], path: list[str], prefix: str, use_site: SchemaLocation) -> None:
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
                    if self._key_name(target.value, expression, target.path, "callback expression").startswith("x-"):
                        continue
                    ordinal += 1
                    expression_path = [*target.path, expression]
                    child = self._resolve_api_object(_mapping(value, expression_path), expression_path)
                    child_use = use_site.child("callbacks", path[-1], expression)
                    self._walk_path_item(child, child_use, prefix + str(ordinal), [])
            self._completed_api_objects.add(target.declaration)
        finally:
            self._active_api_objects.remove(target.declaration)

    def _ignore_declaration(
        self,
        declaration: SchemaLocation,
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
        self.ignored_declarations.append(ApiIgnoredDeclaration(declaration, use_site, reason, owner, media, wire_name))

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
            self._ignore_declaration(self._declared(medium).child("encoding"), parsed_media, owner, media=media)
            return
        for property_name, value in (encodings := _mapping(medium["encoding"], encoding_path)).items():
            property_path = [*encoding_path, property_name]
            encoding = _mapping(value, property_path)
            headers = encoding.get("headers", {})
            if parsed_media.type != "multipart":
                if isinstance(headers, dict):
                    for key in headers:
                        self._ignore_declaration(
                            self._declared(encoding).child("headers", key),
                            "oas_non_multipart_encoding_headers",
                            owner,
                            media=media,
                            wire_name=key,
                        )
                continue
            headers_path = [*property_path, "headers"]
            for key, header_value in (headers := _mapping(headers, headers_path)).items():
                if self._key_name(headers, key, headers_path, "header name").lower() == "content-type":
                    self._ignore_declaration(
                        self._declared(encoding).child("headers", key),
                        "oas_header_ignored",
                        owner,
                        media=media,
                        wire_name=key,
                    )
                    continue
                property_key = self._key_name(encodings, property_name, encoding_path, "encoding property name")
                header = self._resolve_api_object(header_value, [*property_path, "headers", key])
                with self._api_object_context(header):
                    candidate = (
                        name
                        + self._get_model_name(property_key, "", "")
                        + self._get_model_name(key, "", "EncodingHeader")
                    )
                    self._walk_parameter(
                        candidate,
                        header,
                        header=True,
                        role="response_encoding_header" if owner == "response" else "request_encoding_header",
                    )
