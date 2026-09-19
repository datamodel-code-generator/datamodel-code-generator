"""Read finite builtin declarations from accepted artifacts without executing them."""

from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from datamodel_code_generator._binding_literals import UnsupportedBindingValueError, freeze_literal
from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    LiteralScalar,
    NoneDefaultProvenance,
    SourceExpression,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._generation_contract import (
        AttemptId,
        FieldSlot,
        FinalPythonType,
        FrozenLiteral,
        SymbolId,
    )
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModelFieldBase

Tokens: TypeAlias = tuple[tokenize.TokenInfo, ...]
_PAIR_SIZE: Final = 2
_ANNOTATED_FIELD_MIN_TOKENS: Final = 3

EmissionForm: TypeAlias = Literal["class_field", "typeddict_entry"]


@dataclass(frozen=True, slots=True)
class FrozenImportBindings:
    """Keep actual imports from the final module, including independent alias identity."""

    values: tuple[Import, ...]


@dataclass(frozen=True, slots=True)
class ExpectedFieldDeclaration:
    """Specify a known final field; source text never discovers its graph identity."""

    attempt: AttemptId
    consumer: SymbolId
    slot: FieldSlot
    model_name: str
    native_name: str
    backend: Literal["dataclass", "pydantic_dataclass", "pydantic", "typeddict", "msgspec"]
    type: FinalPythonType
    excluded_by_tag: bool = False
    form: EmissionForm = "class_field"
    entry_key: str | None = None
    entry_ordinal: int | None = None


DefaultKind: TypeAlias = Literal[
    "absent", "none", "literal", "expression", "factory", "msgspec_unset", "pydantic_missing", "opaque"
]


@dataclass(frozen=True, slots=True)
class EmittedFieldFacts:
    """Observe constructor syntax without claiming arbitrary callable execution results."""

    emitted: bool
    emitted_default_kind: DefaultKind
    emitted_default_value: FrozenLiteral | SourceExpression | None
    factory_present: bool
    factory_expression: SourceExpression | None
    unset_default: bool
    unset_type_in_annotation: bool
    null_type_in_annotation: bool
    qualifiers: tuple[str, ...]
    constructor_keywords: tuple[tuple[str, FrozenLiteral | SourceExpression], ...]


@dataclass(frozen=True, slots=True)
class FieldArtifactDeclaration:
    """Retain a matched statement's unexecuted source and exact artifact location."""

    expected: ExpectedFieldDeclaration
    annotation: str
    assignment: str | None
    line: int
    column: int
    facts: EmittedFieldFacts


@dataclass(frozen=True, slots=True)
class ArtifactDefinition:
    """Index a top-level static definition without parsing a whole Python grammar."""

    name: str
    kind: Literal["class", "type_alias", "assignment", "import"]
    line: int


@dataclass(frozen=True, slots=True)
class BuiltinFieldArtifactIndex:
    """Own only immutable accepted-artifact observations, never model instances."""

    digest: str
    definitions: tuple[ArtifactDefinition, ...]
    fields: tuple[FieldArtifactDeclaration, ...]


@dataclass(frozen=True, slots=True)
class _Statement:
    indent: int
    tokens: Tokens


def _statements(body: str) -> Iterator[_Statement]:
    """Translate tokenization errors without imposing the host Python AST grammar."""
    try:
        yield from _read_statements(body)
    except (tokenize.TokenError, IndentationError) as cause:
        msg = "Accepted artifact could not be tokenized"
        raise BindingCaptureError(msg) from cause


def _read_statements(body: str) -> Iterator[_Statement]:
    """Tokenize once, retaining one logical statement while skipping comments."""
    current: list[tokenize.TokenInfo] = []
    indent = 0
    depth = 0
    for token in tokenize.generate_tokens(StringIO(body).readline):
        match token.type:
            case tokenize.INDENT:
                indent += 1
            case tokenize.DEDENT:
                indent -= 1
            case tokenize.COMMENT | tokenize.NL | tokenize.ENCODING | tokenize.ENDMARKER:
                continue
            case tokenize.NEWLINE:
                if current:
                    yield _Statement(indent, tuple(current))
                    current.clear()
            case _:
                if token.type == tokenize.OP:
                    if token.string in {"(", "[", "{"}:
                        depth += 1
                    elif token.string in {")", "]", "}"}:
                        depth -= 1
                if token.string == ";" and token.type == tokenize.OP and depth == 0:
                    if current:
                        yield _Statement(indent, tuple(current))
                        current.clear()
                    continue
                current.append(token)


def _split(tokens: Tokens, separator: str) -> tuple[Tokens, ...]:
    """Split only at this expression level, preserving nested syntax verbatim."""
    depth = 0
    start = 0
    parts: list[Tokens] = []
    for index, token in enumerate(tokens):
        if token.type != tokenize.OP:
            continue
        if token.string in {"(", "[", "{"}:
            depth += 1
        elif token.string in {")", "]", "}"}:
            depth -= 1
        elif token.string == separator and depth == 0:
            parts.append(tokens[start:index])
            start = index + 1
    parts.append(tokens[start:])
    return tuple(parts)


def _text(tokens: Tokens) -> str:
    """Render token spelling without source coordinates or evaluating an expression."""
    return tokenize.untokenize([(token.type, token.string) for token in tokens]).strip()


def _dotted_name(tokens: Tokens) -> str | None:
    if not tokens or len(tokens) % 2 == 0:
        return None
    for index, token in enumerate(tokens):
        if index % 2:
            if token.string != ".":
                return None
        elif token.type != tokenize.NAME:
            return None
    return "".join(token.string for token in tokens)


def _string_literal(tokens: Tokens) -> str | None:
    """Decode only string tokens; names, calls, unpacking and formatted strings fail."""
    if not tokens or any(token.type != tokenize.STRING for token in tokens):
        return None
    try:
        value: object = ast.literal_eval(_text(tokens))
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _import_identity(import_: Import) -> tuple[str, str]:
    if import_.from_ is None:
        return import_.alias or import_.import_.partition(".")[0], import_.import_
    return import_.alias or import_.import_, f"{import_.from_}.{import_.import_}"


def _import_names(tokens: Tokens) -> tuple[tuple[str, str], ...]:
    """Read actual top-level imports, including parenthesized import lists."""
    names = tuple(token.string for token in tokens)
    if names[0] == "from" and "import" in names:
        index = names.index("import")
        module = "".join(names[1:index])
        members = tokens[index + 1 :]
        if members and members[0].string == "(" and members[-1].string == ")":
            members = members[1:-1]
    elif names[0] == "import":
        module = ""
        members = tokens[1:]
    else:
        return ()
    result: list[tuple[str, str]] = []
    for part in _split(members, ","):
        if not part:
            continue
        words = tuple(token.string for token in part)
        if "as" in words:
            index = words.index("as")
            target = _dotted_name(part[:index])
            alias = words[index + 1] if index + 2 == len(words) else None
        else:
            target = _dotted_name(part)
            alias = target if module else target.partition(".")[0] if target is not None else None
        if target is None or alias is None:
            continue
        result.append((alias, f"{module}.{target}" if module else target))
    return tuple(result)


def _unparenthesized(tokens: Tokens) -> Tokens:
    while len(tokens) >= _PAIR_SIZE and tokens[0].string == "(" and tokens[-1].string == ")":
        depth = 0
        closing = -1
        for index, token in enumerate(tokens):
            closing = index
            if token.type == tokenize.OP:
                depth += token.string in {"(", "[", "{"}
                depth -= token.string in {")", "]", "}"}
                if depth == 0:
                    break
        if closing != len(tokens) - 1:
            break
        tokens = tokens[1:-1]
    return tokens


def _resolved_name(tokens: Tokens, bindings: dict[str, str]) -> str | None:
    if (name := _dotted_name(_unparenthesized(tokens))) is None:
        return None
    prefix, separator, suffix = name.partition(".")
    if (module := bindings.get(prefix)) is None:
        return None
    return module + (separator + suffix if separator else "")


def _application(tokens: Tokens, opening: Literal["(", "["]) -> tuple[Tokens, tuple[Tokens, ...]] | None:
    tokens = _unparenthesized(tokens)
    closing = ")" if opening == "(" else "]"
    if not tokens or tokens[-1].string != closing:
        return None
    index = next((index for index, token in enumerate(tokens) if token.string == opening), None)
    if index is None or _dotted_name(tokens[:index]) is None:
        return None
    depth = 0
    for offset, token in enumerate(tokens[index:], index):
        if token.type != tokenize.OP:
            continue
        depth += token.string in {"(", "[", "{"}
        depth -= token.string in {")", "]", "}"}
        if depth == 0 and offset != len(tokens) - 1:
            return None
    return tokens[:index], tuple(part for part in _split(tokens[index + 1 : -1], ",") if part)


def _literal_or_syntax(tokens: Tokens) -> FrozenLiteral | SourceExpression:
    text = _text(tokens)
    try:
        value: object = ast.literal_eval(text)
        return freeze_literal(value, set())
    except (SyntaxError, ValueError, UnsupportedBindingValueError):
        return SourceExpression(text)


def _constructor_parts(
    tokens: Tokens, bindings: dict[str, str]
) -> tuple[Tokens | None, tuple[tuple[str, Tokens], ...]] | None:
    if (call := _application(tokens, "(")) is None:
        return None
    callee, arguments = call
    if _resolved_name(callee, bindings) not in {"dataclasses.field", "pydantic.Field", "msgspec.field"}:
        return None
    positional: Tokens | None = None
    keywords: list[tuple[str, Tokens]] = []
    for index, argument in enumerate(arguments):
        parts = _split(argument, "=")
        if len(parts) == _PAIR_SIZE and len(parts[0]) == 1 and parts[0][0].type == tokenize.NAME:
            keywords.append((parts[0][0].string, parts[1]))
        elif index == 0 and len(parts) == 1:
            positional = argument
        else:
            msg = "A builtin field constructor contains unsupported argument syntax"
            raise BindingCaptureError(msg)
    return positional, tuple(keywords)


def _annotation_field_calls(tokens: Tokens, bindings: dict[str, str]) -> tuple[Tokens, ...]:
    if (application := _application(tokens, "[")) is None:
        return ()
    callee, arguments = application
    if _resolved_name(callee, bindings) not in {"typing.Annotated", "typing_extensions.Annotated"} or not arguments:
        return ()
    return (*_annotation_field_calls(arguments[0], bindings), *arguments[1:])


def _has_top_marker(tokens: Tokens, bindings: dict[str, str], marker: str) -> bool:
    tokens = _unparenthesized(tokens)
    if (
        marker == "None" and len(tokens) == 1 and tokens[0].type == tokenize.NAME and tokens[0].string == "None"
    ) or _resolved_name(tokens, bindings) == marker:
        return True
    if len(parts := _split(tokens, "|")) > 1:
        return any(_has_top_marker(part, bindings, marker) for part in parts)
    if (application := _application(tokens, "[")) is None:
        return False
    callee, arguments = application
    match _resolved_name(callee, bindings):
        case (
            "typing.Annotated"
            | "typing_extensions.Annotated"
            | "typing.Required"
            | "typing_extensions.Required"
            | "typing.NotRequired"
            | "typing_extensions.NotRequired"
            | "typing.ReadOnly"
            | "typing_extensions.ReadOnly"
        ):
            return bool(arguments) and _has_top_marker(arguments[0], bindings, marker)
        case identity if identity in {
            "typing.Union",
            "typing.Optional",
            "typing_extensions.Union",
            "typing_extensions.Optional",
        }:
            return (marker == "None" and identity in {"typing.Optional", "typing_extensions.Optional"}) or any(
                _has_top_marker(argument, bindings, marker) for argument in arguments
            )
        case _:
            return False


def _qualifiers(tokens: Tokens, bindings: dict[str, str]) -> tuple[str, ...]:
    if (application := _application(tokens, "[")) is None:
        return ()
    callee, arguments = application
    if len(arguments) != 1 or (identity := _resolved_name(callee, bindings)) is None:
        return ()
    module, _, name = identity.rpartition(".")
    if module not in {"typing", "typing_extensions"} or name not in {"Required", "NotRequired", "ReadOnly", "ClassVar"}:
        return ()
    return (name, *_qualifiers(arguments[0], bindings))


def _default_value(
    tokens: Tokens | None, bindings: dict[str, str]
) -> tuple[DefaultKind, FrozenLiteral | SourceExpression | None]:
    if tokens is None or _text(_unparenthesized(tokens)) == "...":
        return "absent", None
    match _resolved_name(tokens, bindings):
        case "msgspec.UNSET":
            return "msgspec_unset", None
        case "pydantic.experimental.missing_sentinel.MISSING":
            return "pydantic_missing", None
        case _:
            pass
    value = _literal_or_syntax(tokens)
    if isinstance(value, LiteralScalar) and value.kind == "none":
        return "none", value
    return ("expression" if isinstance(value, SourceExpression) else "literal"), value


def _emitted_facts(
    field: ExpectedFieldDeclaration, annotation: Tokens, assignment: Tokens | None, bindings: dict[str, str]
) -> EmittedFieldFacts:
    qualifiers = _qualifiers(annotation, bindings)
    if field.backend == "typeddict":
        return EmittedFieldFacts(
            emitted=True,
            emitted_default_kind="absent",
            emitted_default_value=None,
            factory_present=False,
            factory_expression=None,
            unset_default=False,
            unset_type_in_annotation=False,
            null_type_in_annotation=_has_top_marker(annotation, bindings, "None"),
            qualifiers=qualifiers,
            constructor_keywords=(),
        )
    default = assignment
    keywords: list[tuple[str, Tokens]] = []
    calls = _annotation_field_calls(annotation, bindings)
    if assignment is not None:
        calls = (*calls, assignment)
    for call in calls:
        if (parts := _constructor_parts(call, bindings)) is None:
            continue
        positional, named = parts
        if call is assignment:
            default = positional
        keywords.extend(named)
    factory: SourceExpression | None = None
    for name, value in keywords:
        match name:
            case "default_factory":
                factory = SourceExpression(_text(value))
            case "default":
                default = value
            case _:
                pass
    kind, frozen_default = ("factory", None) if factory is not None else _default_value(default, bindings)
    return EmittedFieldFacts(
        emitted=True,
        emitted_default_kind=kind,
        emitted_default_value=frozen_default,
        factory_present=factory is not None,
        factory_expression=factory,
        unset_default=kind == "msgspec_unset",
        unset_type_in_annotation=_has_top_marker(annotation, bindings, "msgspec.UnsetType"),
        null_type_in_annotation=_has_top_marker(annotation, bindings, "None"),
        qualifiers=qualifiers,
        constructor_keywords=tuple((name, _literal_or_syntax(value)) for name, value in keywords),
    )


def _functional_entries(tokens: Tokens, name: str, bindings: dict[str, str]) -> tuple[tuple[str, Tokens], ...]:
    """Read only the builtin functional TypedDict literal dictionary form."""
    if (call := _application(tokens, "(")) is None:
        msg = "Expected a builtin functional TypedDict call"
        raise BindingCaptureError(msg)
    callee, arguments = call
    if _resolved_name(callee, bindings) not in {"typing.TypedDict", "typing_extensions.TypedDict"}:
        msg = "Functional TypedDict callee does not match its actual import"
        raise BindingCaptureError(msg)
    if len(arguments) < _PAIR_SIZE or _string_literal(arguments[0]) != name:
        msg = "Functional TypedDict name does not match its final symbol"
        raise BindingCaptureError(msg)
    fields = arguments[1]
    if not fields or fields[0].string != "{" or fields[-1].string != "}":
        msg = "Functional TypedDict fields must be a literal dictionary"
        raise BindingCaptureError(msg)
    entries: list[tuple[str, Tokens]] = []
    for part in _split(fields[1:-1], ","):
        if not part:
            continue
        pair = _split(part, ":")
        if len(pair) != _PAIR_SIZE or (key := _string_literal(pair[0])) is None or not pair[1]:
            msg = "Functional TypedDict contains a nonliteral entry"
            raise BindingCaptureError(msg)
        entries.append((key, pair[1]))
    return tuple(entries)


def _class_field(tokens: Tokens) -> tuple[str, Tokens, Tokens | None] | None:
    if len(tokens) < _ANNOTATED_FIELD_MIN_TOKENS or tokens[0].type != tokenize.NAME or tokens[1].string != ":":
        return None
    parts = _split(tokens[2:], "=")
    if not parts or len(parts) > _PAIR_SIZE or not parts[0]:
        msg = "An accepted field declaration has unsupported assignment syntax"
        raise BindingCaptureError(msg)
    return tokens[0].string, parts[0], parts[1] if len(parts) == _PAIR_SIZE else None


class _ArtifactIndexBuilder:
    """Keep statement lookup linear in final fields and artifact tokens."""

    def __init__(self, expected: tuple[ExpectedFieldDeclaration, ...], imports: FrozenImportBindings) -> None:
        self.wanted: dict[str, dict[str, ExpectedFieldDeclaration]] = {}
        for field in expected:
            if field.attempt != field.slot.attempt:
                msg = "A field expectation mixes capture attempts"
                raise BindingCaptureError(msg)
            if not field.excluded_by_tag:
                fields = self.wanted.setdefault(field.model_name, {})
                if field.native_name in fields:
                    msg = "Duplicate field expectation in one consumer"
                    raise BindingCaptureError(msg)
                fields[field.native_name] = field
        self.bindings: dict[str, str] = {}
        self.allowed = dict(_import_identity(import_) for import_ in imports.values)
        self.definitions: list[ArtifactDefinition] = []
        self.found: dict[tuple[str, str], FieldArtifactDeclaration] = {}
        self.defined: set[str] = set()

    def _definition(self, name: str, kind: Literal["class", "type_alias", "assignment"], line: int) -> None:
        if name in self.wanted and name in self.defined:
            msg = "A final symbol is declared more than once in its accepted artifact"
            raise BindingCaptureError(msg)
        self.defined.add(name)
        self.bindings.pop(name, None)
        self.definitions.append(ArtifactDefinition(name, kind, line))

    def top_level(self, tokens: Tokens) -> str | None:
        """Read only top-level definitions, preserving actual import binding order."""
        for alias, identity in _import_names(tokens):
            if self.allowed.get(alias) == identity:
                self.bindings[alias] = identity
            else:
                self.bindings.pop(alias, None)
            self.definitions.append(ArtifactDefinition(alias, "import", tokens[0].start[0]))
        match tokens:
            case (head, second, _, *_) if second.type == tokenize.NAME and head.string == "class":
                self._definition(second.string, "class", head.start[0])
                return second.string
            case (head, second, _, *_) if second.type == tokenize.NAME and head.string == "type":
                self._definition(second.string, "type_alias", head.start[0])
            case (head, second, _, *_) if head.type == tokenize.NAME and second.string in {"=", ":"}:
                name = head.string
                self._definition(name, "assignment", head.start[0])
                if (fields := self.wanted.get(name)) and next(iter(fields.values())).form == "typeddict_entry":
                    self._functional_fields(tokens[2:], name, tuple(fields.values()))
            case _:
                pass
        return None

    def _functional_fields(self, tokens: Tokens, name: str, fields: tuple[ExpectedFieldDeclaration, ...]) -> None:
        entries = _functional_entries(tokens, name, self.bindings)
        if len(entries) != len(fields):
            msg = "Functional TypedDict entry count differs from final field ownership"
            raise BindingCaptureError(msg)
        for ordinal, (key, annotation) in enumerate(entries):
            field = fields[ordinal]
            if field.form != "typeddict_entry" or field.entry_key != key or field.entry_ordinal != ordinal:
                msg = "Functional TypedDict entry order differs from final field ownership"
                raise BindingCaptureError(msg)
            self.found[name, field.native_name] = FieldArtifactDeclaration(
                field,
                _text(annotation),
                None,
                annotation[0].start[0],
                annotation[0].start[1],
                _emitted_facts(field, annotation, None, self.bindings),
            )

    def class_field(self, current_class: str, tokens: Tokens) -> None:
        """Match only own annotated statements; function and nested-class bodies are skipped."""
        if (fields := self.wanted.get(current_class)) is None or (parsed := _class_field(tokens)) is None:
            return
        name, annotation, assignment = parsed
        if (field := fields.get(name)) is None or field.form != "class_field":
            return
        key = current_class, name
        if key in self.found:
            msg = "An accepted class declares the same expected field more than once"
            raise BindingCaptureError(msg)
        self.found[key] = FieldArtifactDeclaration(
            field,
            _text(annotation),
            _text(assignment) if assignment is not None else None,
            tokens[0].start[0],
            tokens[0].start[1],
            _emitted_facts(field, annotation, assignment, self.bindings),
        )

    def finish(self, body: str, expected: tuple[ExpectedFieldDeclaration, ...]) -> BuiltinFieldArtifactIndex:
        """Freeze in expected consumer order after proving each requested declaration exists."""
        ordered: list[FieldArtifactDeclaration] = []
        for field in expected:
            if field.excluded_by_tag:
                continue
            if (declaration := self.found.get((field.model_name, field.native_name))) is None:
                msg = "An expected final field is absent from its accepted artifact"
                raise BindingCaptureError(msg)
            ordered.append(declaration)
        return BuiltinFieldArtifactIndex(sha256(body.encode()).hexdigest(), tuple(self.definitions), tuple(ordered))


def index_builtin_field_declarations(
    body: str, *, expected: tuple[ExpectedFieldDeclaration, ...], imports: FrozenImportBindings
) -> BuiltinFieldArtifactIndex:
    """Associate known fields with their actual class or functional declarations."""
    builder = _ArtifactIndexBuilder(expected, imports)
    current_class: str | None = None
    for statement in _statements(body):
        if statement.indent == 0:
            current_class = builder.top_level(statement.tokens)
        elif statement.indent == 1 and current_class is not None:
            builder.class_field(current_class, statement.tokens)
    return builder.finish(body, expected)


@dataclass(frozen=True, slots=True)
class FieldProjectionContext:
    """Carry established producer facts; unknown source policy cannot prove omission."""

    original_required: bool | None
    schema_default: bool | None
    explicit_model_default: bool | None
    explicit_nullable: bool | None
    preexisting_null: bool | None
    configuration_nullable: bool
    builtin_semantics: bool


def freeze_none_default_provenance(
    field: DataModelFieldBase, *, emitted: EmittedFieldFacts, projection: FieldProjectionContext
) -> NoneDefaultProvenance:
    """Prove ordinary None synthesis from producer facts and accepted syntax, never a getter."""
    default_kind: Literal["absent", "none", "value", "factory", "missing", "opaque"]
    match emitted.emitted_default_kind:
        case "absent" | "none" | "factory" | "opaque" as kind:
            default_kind = kind
        case "msgspec_unset" | "pydantic_missing":
            default_kind = "missing"
        case "literal" | "expression":
            default_kind = "value"
    unknown = (
        not projection.builtin_semantics
        or type(field.extras) is not dict
        or any(
            value is None
            for value in (
                projection.original_required,
                projection.schema_default,
                projection.explicit_model_default,
                projection.explicit_nullable,
                projection.preexisting_null,
            )
        )
    )
    fallback = (
        not unknown
        and projection.original_required is False
        and not projection.configuration_nullable
        and not field.required
        and field.nullable is None
        and field.type_has_null is not True
        and projection.preexisting_null is False
        and "default_factory" not in field.extras
    )
    annotation = _annotation_null_origin(
        field, emitted=emitted, projection=projection, unknown=unknown, fallback=fallback
    )
    origin: Literal[
        "synthesized_optional_fallback",
        "schema_default",
        "explicit_model_default",
        "explicit_nullable",
        "runtime_or_opaque",
        "not_applicable",
    ]
    if default_kind != "none":
        origin = "not_applicable"
    elif unknown:
        origin = "runtime_or_opaque"
    elif projection.explicit_model_default:
        origin = "explicit_model_default"
    elif projection.schema_default:
        origin = "schema_default"
    elif projection.explicit_nullable:
        origin = "explicit_nullable"
    elif fallback and not emitted.factory_present and field.default is None and not field.has_default:
        origin = "synthesized_optional_fallback"
    else:
        origin = "runtime_or_opaque"
    return NoneDefaultProvenance(default_kind, origin, annotation)


def _annotation_null_origin(
    field: DataModelFieldBase,
    *,
    emitted: EmittedFieldFacts,
    projection: FieldProjectionContext,
    unknown: bool,
    fallback: bool,
) -> Literal["optional_fallback", "schema", "model_configuration", "preexisting_type", "none", "opaque"]:
    annotation: Literal["optional_fallback", "schema", "model_configuration", "preexisting_type", "none", "opaque"]
    if not emitted.null_type_in_annotation:
        annotation = "none"
    elif unknown:
        annotation = "opaque"
    elif projection.explicit_nullable:
        annotation = "schema"
    elif projection.preexisting_null:
        annotation = "preexisting_type"
    elif projection.configuration_nullable or (projection.original_required and not field.required):
        annotation = "model_configuration"
    elif fallback:
        annotation = "optional_fallback"
    else:
        annotation = "opaque"
    return annotation
