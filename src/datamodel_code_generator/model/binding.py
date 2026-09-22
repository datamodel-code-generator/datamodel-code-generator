"""Read finite builtin declarations from accepted artifacts without executing them."""

from __future__ import annotations

import ast
import keyword
import tokenize
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from itertools import accumulate, pairwise, starmap
from typing import TYPE_CHECKING, Final, Literal, NoReturn, TypeAlias, cast

from datamodel_code_generator._binding_literals import UnsupportedBindingValueError, freeze_argument, freeze_literal
from datamodel_code_generator._generation_contract import (
    AnnotatedType,
    BindingCaptureError,
    BoundType,
    BuiltinType,
    ConstructorType,
    GeneratedSymbolType,
    GenericType,
    ImportedExpression,
    ImportedType,
    LiteralScalar,
    LiteralType,
    ModelFieldFacts,
    NoneDefaultProvenance,
    NoneType,
    SourceExpression,
    UnionType,
)
from datamodel_code_generator._python_type_annotation import (
    PythonTypeBoundName,
    PythonTypeEllipsis,
    PythonTypeLiteralValue,
    PythonTypeModelField,
    PythonTypeName,
    PythonTypeParameterList,
    PythonTypeQualifiedName,
    PythonTypeRuntimeSymbol,
    PythonTypeStarred,
    PythonTypeSubscript,
    PythonTypeTuple,
    PythonTypeUnion,
)
from datamodel_code_generator.model.base import DataModel

Tokens: TypeAlias = tuple[tokenize.TokenInfo, ...]
_PAIR_SIZE: Final = 2
_ANNOTATED_FIELD_MIN_TOKENS: Final = 3
_TYPING_CONTAINER_NAMES: Final = {
    "list": "typing.List",
    "dict": "typing.Dict",
    "set": "typing.Set",
    "frozenset": "typing.FrozenSet",
    "tuple": "typing.Tuple",
}

BackendName: TypeAlias = Literal["dataclass", "pydantic_dataclass", "pydantic", "typeddict", "msgspec"]
EmissionForm: TypeAlias = Literal["class_field", "typeddict_entry", "alias_value", "root_alias_value"]


@dataclass(frozen=True, slots=True)
class FrozenImportBindings:
    """Keep actual imports from the final module, including independent alias identity."""

    values: tuple[Import, ...]
    symbols: tuple[tuple[SymbolId, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExpectedFieldDeclaration:
    """Specify a known final field; source text never discovers its graph identity."""

    attempt: AttemptId
    consumer: SymbolId
    slot: FieldSlot
    model_name: str
    native_name: str
    backend: BackendName
    type: FinalPythonType
    excluded_by_tag: bool = False
    form: EmissionForm = "class_field"
    entry_key: str | None = None
    entry_ordinal: int | None = None


DefaultKind: TypeAlias = Literal[
    "absent", "none", "literal", "expression", "factory", "msgspec_unset", "pydantic_missing", "opaque"
]
_PROVENANCE_DEFAULTS: Final[dict[DefaultKind, Literal["absent", "none", "value", "factory", "missing", "opaque"]]] = {
    "absent": "absent",
    "none": "none",
    "literal": "value",
    "expression": "value",
    "factory": "factory",
    "msgspec_unset": "missing",
    "pydantic_missing": "missing",
    "opaque": "opaque",
}


@dataclass(frozen=True, slots=True)
class MetaLayer:
    """Locate emitted metadata on an already projected structural type node."""

    node_path: tuple[int, ...]
    ordinal: int
    keywords: tuple[tuple[str, FrozenLiteral | SourceExpression], ...]
    line: int
    column: int


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
    meta_layers: tuple[MetaLayer, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldArtifactDeclaration:
    """Retain a matched statement's unexecuted source and exact artifact location."""

    expected: ExpectedFieldDeclaration
    annotation: str | None
    assignment: str | None
    line: int | None
    column: int | None
    facts: EmittedFieldFacts


@dataclass(frozen=True, slots=True)
class ArtifactDefinition:
    """Index a top-level static definition without parsing a whole Python grammar."""

    name: str
    kind: Literal["class", "type_alias", "assignment", "import", "unverified"]
    line: int
    signature: SourceExpression
    decorators: tuple[SourceExpression, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactModelDeclaration:
    """Retain own field inventory and class settings from the accepted builtin syntax."""

    name: str
    fields: tuple[str, ...]
    settings: tuple[SourceExpression, ...]


@dataclass(frozen=True, slots=True)
class BuiltinFieldArtifactIndex:
    """Own only immutable accepted-artifact observations, never model instances."""

    digest: str
    definitions: tuple[ArtifactDefinition, ...]
    fields: tuple[FieldArtifactDeclaration, ...]
    models: tuple[ArtifactModelDeclaration, ...] = ()
    namespace: tuple[tuple[str, str], ...] = ()
    invalid_models: tuple[str, ...] = ()


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


def _import_names(tokens: Tokens) -> tuple[tuple[str, str, str], ...]:
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
    result: list[tuple[str, str, str]] = []
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
        identity = f"{module}.{target}" if module else target
        resolution_base = identity if module or "as" in words else target.partition(".")[0]
        result.append((alias, identity, resolution_base))
    return tuple(result)


def _bracket_delta(token: tokenize.TokenInfo) -> int:
    if token.type != tokenize.OP:
        return 0
    return (token.string in {"(", "[", "{"}) - (token.string in {")", "]", "}"})


def _unparenthesized(tokens: Tokens) -> Tokens:
    """Strip only parentheses enclosing the whole expression; tokenized brackets always balance."""
    while (
        len(tokens) >= _PAIR_SIZE
        and tokens[0].string == "("
        and tokens[-1].string == ")"
        and all(accumulate(map(_bracket_delta, tokens[:-1])))
    ):
        tokens = tokens[1:-1]
    return tokens


def _resolved_name(tokens: Tokens, bindings: dict[str, str]) -> str | None:
    if (name := _dotted_name(_unparenthesized(tokens))) is None:
        return None
    prefix, separator, suffix = name.partition(".")
    if not (module := bindings.get(prefix)):
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


def _same_expression(left: Tokens, right: Tokens) -> bool:
    """Allow literal quote/wrapping changes without evaluating arbitrary expressions."""
    same_length = len(left) == len(right)
    if same_length and all(
        first.type == second.type and first.string == second.string for first, second in zip(left, right, strict=True)
    ):
        return True
    if not isinstance(value := _literal_or_syntax(left), SourceExpression):
        return value == _literal_or_syntax(right)
    return same_length and all(
        first.type == second.type
        and (
            first.string == second.string
            or (first.type == tokenize.STRING and _literal_or_syntax((first,)) == _literal_or_syntax((second,)))
        )
        for first, second in zip(left, right, strict=True)
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
    return _annotation_entries(arguments[1], "Functional TypedDict")


def _annotation_entries(fields: Tokens, context: str) -> tuple[tuple[str, Tokens], ...]:
    """Read only literal string keys and retained annotation tokens."""
    if not fields or fields[0].string != "{" or fields[-1].string != "}":
        msg = f"{context} fields must be a literal dictionary"
        raise BindingCaptureError(msg)
    entries: list[tuple[str, Tokens]] = []
    for part in _split(fields[1:-1], ","):
        if not part:
            continue
        pair = _split(part, ":")
        if len(pair) != _PAIR_SIZE or (key := _string_literal(pair[0])) is None or not pair[1]:
            msg = f"{context} contains a nonliteral entry"
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


def _expression_tokens(text: str) -> Tokens:
    statements = tuple(_statements(text + "\n"))
    return statements[0].tokens if len(statements) == 1 else ()


def _union_parts(tokens: Tokens) -> tuple[Tokens | None, ...] | None:
    """Preserve source order; the implicit Optional null has no source token."""
    parts = _split(_unparenthesized(tokens), "|")
    return parts if len(parts) > 1 else None


class _TypePlacementMatcher:
    """Corroborate projected identities; source tokens never create type identities."""

    def __init__(
        self, bindings: dict[str, str], symbols: dict[SymbolId, set[str]], expected_bindings: dict[str, str]
    ) -> None:
        self.bindings = bindings
        self.symbols = symbols
        self.expected_bindings = expected_bindings
        self.layers: list[MetaLayer] = []
        self.layer_counts: dict[tuple[int, ...], int] = {}

    def _union(self, tokens: Tokens) -> tuple[Tokens | None, ...] | None:
        if (parts := _union_parts(tokens)) is not None:
            return self._flatten_unions(parts)
        if (application := _application(tokens, "[")) is None:
            return None
        callee, arguments = application
        match _resolved_name(callee, self.bindings):
            case "typing.Union" | "typing_extensions.Union":
                return self._flatten_unions(arguments)
            case "typing.Optional" | "typing_extensions.Optional" if len(arguments) == 1:
                return (*(self._union(arguments[0]) or arguments), None)
            case _:
                return None

    def _flatten_unions(self, children: tuple[Tokens | None, ...]) -> tuple[Tokens | None, ...]:
        return tuple(
            nested
            for child in children
            for nested in ((self._union(child) or (child,)) if child is not None else (None,))
        )

    def _metadata(self, tokens: Tokens, path: tuple[int, ...], required: tuple[MetadataCall, ...] = ()) -> Tokens:
        matched = 0
        while (application := _application(tokens, "[")) is not None:
            callee, arguments = application
            if _resolved_name(callee, self.bindings) not in {"typing.Annotated", "typing_extensions.Annotated"}:
                break
            if len(arguments) < _PAIR_SIZE:
                self._mismatch()
            for metadata in arguments[1:]:
                if (call := _application(metadata, "(")) is None:
                    continue
                identity = _resolved_name(call[0], self.bindings)
                if matched < len(required) and identity == _import_identity(required[matched].import_)[1]:
                    if not self._match_keywords(required[matched].keywords, call[1]):
                        self._mismatch()
                    matched += 1
                if identity != "msgspec.Meta":
                    continue
                keywords: list[tuple[str, FrozenLiteral | SourceExpression]] = []
                for argument in call[1]:
                    pair = _split(argument, "=")
                    if len(pair) != _PAIR_SIZE or len(pair[0]) != 1 or pair[0][0].type != tokenize.NAME:
                        self._mismatch()
                    keywords.append((pair[0][0].string, _literal_or_syntax(pair[1])))
                ordinal = self.layer_counts.get(path, 0)
                self.layer_counts[path] = ordinal + 1
                self.layers.append(
                    MetaLayer(path, ordinal, tuple(keywords), metadata[0].start[0], metadata[0].start[1])
                )
            tokens = arguments[0]
        if matched != len(required):
            self._mismatch()
        return tokens

    def _projected_metadata(
        self, expected: FinalPythonType, tokens: Tokens, path: tuple[int, ...]
    ) -> tuple[UnannotatedPythonType, Tokens]:
        if not isinstance(expected, AnnotatedType):
            return expected, self._metadata(tokens, path)
        required = expected.metadata
        expected = expected.base
        while isinstance(expected, AnnotatedType):
            required += expected.metadata
            expected = expected.base
        return expected, self._metadata(tokens, path, required)

    def match_field(self, expected: FinalPythonType, tokens: Tokens) -> None:
        """Separate field policy wrappers from the existing data-type skeleton.

        A field-level union such as ``Union[Annotated[...], UnsetType]`` can enclose the complete projected union.
        """
        expected, tokens = self._projected_metadata(expected, tokens, ())
        while (application := _application(tokens, "[")) is not None:
            callee, arguments = application
            if (
                _resolved_name(callee, self.bindings)
                not in {
                    "typing.Required",
                    "typing_extensions.Required",
                    "typing.NotRequired",
                    "typing_extensions.NotRequired",
                    "typing.ReadOnly",
                    "typing_extensions.ReadOnly",
                    "typing.ClassVar",
                    "typing_extensions.ClassVar",
                }
                or len(arguments) != 1
            ):
                break
            tokens = self._metadata(arguments[0], ())
        if isinstance(expected, BoundType) and self._match_bound(expected, tokens):
            return
        expected_members = (
            expected.members
            if isinstance(expected, UnionType)
            else tuple(
                BoundType(replace(expected.binding, expression=item)) for item in expected.binding.expression.items
            )
            if isinstance(expected, BoundType) and isinstance(expected.binding.expression, PythonTypeUnion)
            else (expected,)
        )
        if (actual := self._union(tokens)) is None:
            self._match(expected, tokens, ())
            return
        has_null = any(
            isinstance(member, NoneType)
            or (
                isinstance(member, BoundType)
                and isinstance(member.binding.expression, PythonTypeName)
                and member.binding.expression.value == "None"
            )
            for member in expected_members
        )
        actual = tuple(member for member in actual if not (member is None or _text(member) == "None") or has_null)
        actual = tuple(
            member
            for member in actual
            if member is None
            or _resolved_name(member, self.bindings)
            not in {
                "msgspec.UnsetType",
                "pydantic.experimental.missing_sentinel.MISSING",
            }
        )
        if len(expected_members) != len(actual):
            if len(actual) == 1 and actual[0] is not None:
                self._match(expected, actual[0], ())
                return
            self._mismatch()
        for index, (member, annotation) in enumerate(zip(expected_members, actual, strict=True)):
            self._match(member, annotation, (index,) if isinstance(expected, UnionType) else ())

    @staticmethod
    def _mismatch() -> NoReturn:
        msg = "Accepted field annotation does not match its projected type"
        raise BindingCaptureError(msg)

    def _match(  # ruff: ignore[too-many-branches]
        self, expected: FinalPythonType, tokens: Tokens | None, path: tuple[int, ...]
    ) -> None:
        if tokens is None:
            if not isinstance(expected, NoneType):
                self._mismatch()
            return
        if len(tokens) == 1 and tokens[0].type == tokenize.STRING and (forward := _string_literal(tokens)) is not None:
            statements = tuple(_statements(forward))
            if len(statements) != 1:
                self._mismatch()
            tokens = statements[0].tokens
        skeleton, tokens = self._projected_metadata(expected, tokens, path)
        match skeleton:
            case BuiltinType(name):
                actual = _dotted_name(tokens)
                matched = actual is not None and (
                    (actual == name and name not in self.bindings)
                    or (
                        actual.partition(".")[0] in self.bindings
                        and _resolved_name(tokens, self.bindings) == f"builtins.{name}"
                    )
                )
            case NoneType():
                matched = _text(tokens) == "None"
            case ImportedType(import_, suffix):
                identity = _import_identity(import_)[1]
                matched = _resolved_name(tokens, self.bindings) == ".".join((identity, *suffix))
            case GeneratedSymbolType(symbol):
                matched = self._symbol_name(symbol, _dotted_name(tokens))
            case BoundType():
                matched = self._match_bound(skeleton, tokens)
            case GenericType():
                self._match_generic(skeleton, tokens, path)
                return
            case UnionType(members, _):
                if (children := self._union(tokens)) is None or len(children) != len(members):
                    self._mismatch()
                for index, (member, child) in enumerate(zip(members, children, strict=True)):
                    self._match(member, child, (*path, index))
                return
            case LiteralType():
                matched = self._match_literal(skeleton, tokens)
            case _:
                matched = self._match_constructor(skeleton, tokens, path)
        if not matched:
            self._mismatch()

    def _symbol_name(self, symbol: SymbolId, name: str | None) -> bool:
        if name is None or name not in self.symbols.get(symbol, ()):
            return False
        prefix = name.partition(".")[0]
        if (expected := self.expected_bindings.get(prefix)) is not None:
            return self.bindings.get(prefix) == expected
        return True

    def _match_bound(self, expected: BoundType, tokens: Tokens) -> bool:
        return self._match_python_expression(expected.binding.expression, tokens)

    def _match_python_expression(  # ruff: ignore[too-many-branches]
        self, expected: PythonTypeExpr, tokens: Tokens
    ) -> bool:
        tokens = _unparenthesized(tokens)
        match expected:
            case PythonTypeRuntimeSymbol(module, parts):
                identity = (*((self.bindings.get(module, module),) if module else ()), *parts)
                matched = _resolved_name(tokens, self.bindings) == ".".join(identity)
            case PythonTypeBoundName(_, module, name):
                matched = (
                    _resolved_name(tokens, self.bindings) == f"{module}.{name}"
                    if module
                    else _resolved_name(tokens, self.bindings) == name
                )
            case PythonTypeName(name):
                matched = _dotted_name(tokens) == name and name not in self.bindings
            case PythonTypeQualifiedName(parts):
                matched = _dotted_name(tokens) == ".".join(parts)
            case PythonTypeSubscript(base, arguments):
                matched = (
                    (application := _application(tokens, "[")) is not None
                    and self._match_python_expression(base, application[0])
                    and self._match_python_arguments(arguments, application[1])
                )
            case PythonTypeModelField():
                matched = self._match_native_field(expected, tokens)
            case PythonTypeUnion(items):
                children = self._union(tokens)
                matched = (
                    children is not None
                    and len(children) == len(items)
                    and all(
                        self._match_python_expression(item, child)
                        if child is not None
                        else isinstance(item, PythonTypeName) and item.value == "None"
                        for item, child in zip(items, children, strict=True)
                    )
                )
            case PythonTypeParameterList(items):
                matched = (
                    bool(tokens)
                    and tokens[0].string == "["
                    and tokens[-1].string == "]"
                    and self._match_python_arguments(items, tuple(part for part in _split(tokens[1:-1], ",") if part))
                )
            case PythonTypeTuple(items):
                matched = self._match_python_arguments(items, tuple(part for part in _split(tokens, ",") if part))
            case PythonTypeStarred(value):
                matched = bool(tokens) and tokens[0].string == "*" and self._match_python_expression(value, tokens[1:])
            case PythonTypeLiteralValue(value):
                matched = _literal_or_syntax(tokens) == freeze_literal(value, set())
            case PythonTypeEllipsis():
                matched = _text(tokens) == "..."
            case _:
                matched = False
        return matched

    def _match_native_field(self, expected: PythonTypeModelField, tokens: Tokens) -> bool:
        for index in reversed(expected.arguments):
            suffix = (".", "__args__", "[", str(index), "]")
            width = len(suffix)
            if len(tokens) < width or tuple(token.string for token in tokens[-width:]) != suffix:
                return False
            tokens = tokens[:-width]
        if expected.pydantic:
            if tuple(token.string for token in tokens[-2:]) != (".", "annotation"):
                return False
            tokens = tokens[:-2]
        if not tokens or tokens[-1].string != "]":
            return False
        depth = 0
        for index in range(len(tokens) - 1, -1, -1):
            token = tokens[index]
            if token.type != tokenize.OP:
                continue
            if token.string == "]":
                depth += 1
            elif token.string == "[":
                depth -= 1
                if depth == 0:
                    member = "model_fields" if expected.pydantic else "__annotations__"
                    return (
                        tuple(token.string for token in tokens[max(0, index - 2) : index]) == (".", member)
                        and _string_literal(tokens[index + 1 : -1]) == expected.field_name
                        and self._match_python_expression(expected.model, tokens[: index - 2])
                    )
        return False

    def _match_python_arguments(self, expected: tuple[PythonTypeExpr, ...], children: tuple[Tokens, ...]) -> bool:
        if not expected:
            return not children or (len(children) == 1 and not _unparenthesized(children[0]))
        return len(expected) == len(children) and all(
            starmap(self._match_python_expression, zip(expected, children, strict=True))
        )

    def _match_generic(self, expected: GenericType, tokens: Tokens, path: tuple[int, ...]) -> None:
        base, arguments, tuple_form = expected.base, expected.arguments, expected.tuple_form
        if not arguments and tuple_form == "not_tuple":
            callee, children = tokens, ()
        elif (application := _application(tokens, "[")) is not None:
            callee, children = application
        else:
            self._mismatch()
        if tuple_form == "fixed" and not arguments and len(children) == 1 and _text(children[0]) == "()":
            children = ()
        if not isinstance(base, BuiltinType) or _resolved_name(callee, self.bindings) != _TYPING_CONTAINER_NAMES.get(
            base.name, ""
        ):
            self._match(base, callee, (*path, -1))
        if len(children) != len(arguments):
            self._mismatch()
        for index, (argument, child) in enumerate(zip(arguments, children, strict=True)):
            self._match(argument, child, (*path, index))

    def _match_literal(self, expected: LiteralType, tokens: Tokens) -> bool:
        values = expected.values
        application = _application(tokens, "[")
        matched = (
            application is not None
            and _resolved_name(application[0], self.bindings)
            in {
                "typing.Literal",
                "typing_extensions.Literal",
            }
            and len(application[1]) == len(values)
        )
        if matched and application is not None:
            for value, child in zip(values, application[1], strict=True):
                if isinstance(value, LiteralScalar):
                    matched = matched and _literal_or_syntax(child) == value
                else:
                    name = _dotted_name(child)
                    matched = (
                        matched
                        and name is not None
                        and name.endswith("." + value.name)
                        and self._symbol_name(value.symbol, name[: -(len(value.name) + 1)])
                    )
        return matched

    def _match_constructor(self, expected: ConstructorType, tokens: Tokens, path: tuple[int, ...]) -> bool:
        callee, keywords = expected.callable, expected.keywords
        if (application := _application(tokens, "(")) is None:
            self._mismatch()
        self._match(callee, application[0], path)
        return self._match_keywords(keywords, application[1])

    def _match_keywords(self, keywords: tuple[tuple[str, TypeArgument], ...], arguments: tuple[Tokens, ...]) -> bool:
        actual_keywords = tuple(_split(argument, "=") for argument in arguments)
        matched = len(actual_keywords) == len(keywords)
        if matched:
            for (name, value), pair in zip(keywords, actual_keywords, strict=True):
                if len(pair) != _PAIR_SIZE or _text(pair[0]) != name:
                    matched = False
                    break
                matched = matched and self._match_argument(value, pair[1])
        return matched

    def _match_imported_expression(self, import_: Import, prefix: str, suffix: str, tokens: Tokens) -> bool:
        marker = "__dcg_import_binding__"
        expression = _expression_tokens(f"{prefix}{marker}{suffix}")
        positions = tuple(index for index, token in enumerate(expression) if token.string == marker)
        if len(positions) != 1:
            return False
        start = positions[0]
        end = start
        while end < len(tokens):
            token = tokens[end]
            if (end - start) % 2:
                if token.string != ".":
                    break
            elif token.type != tokenize.NAME:
                break
            end += 1
        return (
            end > start
            and _same_expression(tokens[:start], expression[:start])
            and _same_expression(tokens[end:], expression[start + 1 :])
            and _resolved_name(tokens[start:end], self.bindings) == _import_identity(import_)[1]
        )

    def _match_argument(self, expected: TypeArgument, tokens: Tokens) -> bool:
        match expected:
            case ImportedExpression(import_, prefix, suffix):
                return self._match_imported_expression(import_, prefix, suffix, tokens)
            case SourceExpression(text):
                return _same_expression(tokens, _expression_tokens(text))
            case LiteralScalar("decimal", Decimal() as value):
                application = _application(tokens, "(")
                if (
                    application is None
                    or _resolved_name(application[0], self.bindings) != "decimal.Decimal"
                    or len(application[1]) != 1
                    or (literal := _string_literal(application[1][0])) is None
                ):
                    return False
                try:
                    actual = Decimal(literal)
                except InvalidOperation:
                    return False
                return actual.is_finite() and actual == value
            case _:
                return _literal_or_syntax(tokens) == expected


class _ArtifactIndexBuilder:
    """Keep statement lookup linear in final fields and artifact tokens."""

    def __init__(self, expected: tuple[ExpectedFieldDeclaration, ...], imports: FrozenImportBindings) -> None:
        self.wanted: dict[str, dict[str | int | None, ExpectedFieldDeclaration]] = {}
        for field in expected:
            if field.attempt != field.slot.attempt:
                msg = "A field expectation mixes capture attempts"
                raise BindingCaptureError(msg)
            fields = self.wanted.setdefault(field.model_name, {})
            key = field.entry_ordinal if field.form == "typeddict_entry" else field.native_name
            if key in fields:
                msg = "Duplicate field expectation in one consumer"
                raise BindingCaptureError(msg)
            fields[key] = field
        self.bindings: dict[str, str] = {}
        self.allowed = {_import_identity(import_) for import_ in imports.values}
        self.symbols: dict[SymbolId, set[str]] = {}
        for symbol, name in (*imports.symbols, *((field.consumer, field.model_name) for field in expected)):
            self.symbols.setdefault(symbol, set()).add(name)
        self.expected_bindings = {
            alias: identity for value in imports.values for alias, identity in (_import_identity(value),)
        }
        self.definitions: list[ArtifactDefinition] = []
        self.found: dict[tuple[str, str | int | None], FieldArtifactDeclaration] = {}
        self.defined: set[str] = set()
        self.decorators: list[SourceExpression] = []
        self.own_fields: dict[str, list[str]] = {}
        self.settings: dict[str, list[SourceExpression]] = {}
        self.current_symbol: str | None = None
        self.invalid_models: dict[str, None] = {}

    def _definition(self, name: str, kind: Literal["class", "type_alias", "assignment"], tokens: Tokens) -> None:
        self.current_symbol = name
        if name in self.wanted and name in self.defined:
            msg = "A final symbol is declared more than once in its accepted artifact"
            raise BindingCaptureError(msg)
        self.defined.add(name)
        self.bindings[name] = ""
        self.definitions.append(
            ArtifactDefinition(name, kind, tokens[0].start[0], SourceExpression(_text(tokens)), tuple(self.decorators))
        )
        self.decorators.clear()

    def top_level(self, tokens: Tokens) -> str | None:
        """Read only top-level definitions, preserving actual import binding order."""
        self.current_symbol = None
        if tokens[0].string == "@":
            self.decorators.append(SourceExpression(_text(tokens)))
            return None
        for alias, identity, resolution_base in _import_names(tokens):
            if (alias, identity) in self.allowed:
                self.bindings[alias] = resolution_base
            else:
                self.bindings[alias] = ""
            self.definitions.append(
                ArtifactDefinition(alias, "import", tokens[0].start[0], SourceExpression(_text(tokens)))
            )
        if tokens[0].string in {"from", "import"}:
            return None
        match tokens:
            case (head, second, _, *_) if second.type == tokenize.NAME and head.string == "class":
                self._definition(second.string, "class", tokens)
                self.own_fields.setdefault(second.string, [])
                self.settings.setdefault(second.string, [])
                return second.string
            case (head, second, _, *_) if second.type == tokenize.NAME and head.string == "type":
                self._definition(second.string, "type_alias", tokens)
                self._alias_value(second.string, tokens)
            case (head, second, _, *_) if head.type == tokenize.NAME and second.string in {"=", ":"}:
                name = head.string
                if name not in self.wanted:
                    self._verify_static_expression(tokens[2:])
                self._definition(name, "assignment", tokens)
                if (fields := self.wanted.get(name)) and next(iter(fields.values())).form == "typeddict_entry":
                    self._functional_fields(tokens[2:], name, tuple(fields.values()))
                else:
                    self._alias_value(name, tokens)
            case _:
                self._unverified_writes(tokens)
        return None

    def _verify_static_expression(self, tokens: Tokens) -> None:
        for index, (previous, token) in enumerate(pairwise(tokens)):
            if token.string != "(" or not (
                previous.string in {")", "]"}
                or (
                    previous.type == tokenize.NAME
                    and not keyword.iskeyword(previous.string)
                    and previous.string not in {"match", "case"}
                )
            ):
                continue
            start = index
            while start >= _PAIR_SIZE and tokens[start - 1].string == "." and tokens[start - 2].type == tokenize.NAME:
                start -= _PAIR_SIZE
            if _resolved_name(tokens[start : index + 1], self.bindings) in {
                "typing.TypedDict",
                "typing_extensions.TypedDict",
                "typing.TypeAliasType",
                "typing_extensions.TypeAliasType",
                "pydantic.Field",
                "pydantic.constr",
                "pydantic.conint",
                "pydantic.confloat",
                "pydantic.condecimal",
                "pydantic.condate",
                "pydantic.conbytes",
                "pydantic.conlist",
                "pydantic.conset",
                "pydantic.confrozenset",
                "msgspec.Meta",
            }:
                continue
            msg = "Executable artifact statements require an explicit export adapter"
            raise BindingCaptureError(msg)

    def _unverified_writes(self, tokens: Tokens) -> None:  # ruff: ignore[too-many-branches] -- Keep the finite module-write grammar together.
        if (call := _application(tokens, "(")) is not None:
            match call[0]:
                case (owner, dot, method) if (
                    owner.string in self.defined and dot.string == "." and method.string == "model_rebuild"
                ):
                    return
                case _:
                    msg = "Executable artifact statements require an explicit export adapter"
                    raise BindingCaptureError(msg)
        if tokens[0].string not in {"def", "class", "async"}:
            self._verify_static_expression(tokens)
        names = [alias for alias, _, _ in _import_names(tokens)]
        names.extend(
            previous.string
            for previous, token in pairwise(tokens)
            if token.string == ":=" and previous.type == tokenize.NAME
        )
        words = tuple(token.string for token in tokens)
        match words:
            case ("def", name, *_) | ("async", "def", name, *_) | ("class", name, *_):
                names.append(name)
            case ("del", *rest):
                names.extend(rest)
            case (name, operator, *_) if operator in {
                "+=",
                "-=",
                "*=",
                "/=",
                "//=",
                "%=",
                "**=",
                "@=",
                "&=",
                "|=",
                "^=",
                ">>=",
                "<<=",
            }:
                names.append(name)
            case ("for", *_) | ("async", "for", *_) if "in" in words:
                names.extend(words[1 : words.index("in")])
            case ("with", *_) | ("async", "with", *_) | ("except", *_):
                names.extend(words[index + 1] for index, word in enumerate(words[:-1]) if word == "as")
            case ("case", *_):
                names.extend(token.string for token in tokens[1:] if token.type == tokenize.NAME)
            case ("if" | "elif" | "else" | "while" | "try" | "finally" | "match", *_):
                pass
            case _:
                if len(parts := _split(tokens, "=")) > 1:
                    names.extend(token.string for token in parts[0] if token.type == tokenize.NAME)
        for name in names:
            self.bindings[name] = ""
            self.definitions.append(
                ArtifactDefinition(name, "unverified", tokens[0].start[0], SourceExpression(_text(tokens)))
            )
        if (
            words[0] in {"if", "elif", "else", "for", "while", "with", "try", "except", "finally", "match", "case"}
            and len(parts := _split(tokens, ":")) > 1
            and parts[-1]
        ):
            self._unverified_writes(parts[-1])

    def module_block(self, tokens: Tokens) -> None:
        """Reject conditional module bindings without accepting a nested definition as final."""
        self._unverified_writes(tokens)

    def _alias_value(self, name: str, tokens: Tokens) -> None:
        fields = self.wanted.get(name)
        if not fields or (field := next(iter(fields.values()))).form not in {"alias_value", "root_alias_value"}:
            return
        parts = _split(tokens, "=")
        if len(parts) != _PAIR_SIZE or not (annotation := parts[1]):
            msg = "A builtin alias has no unique value expression"
            raise BindingCaptureError(msg)
        if field.form == "root_alias_value":
            if (
                (application := _application(annotation, "[")) is None
                or _resolved_name(application[0], self.bindings) != "pydantic.RootModel"
                or len(application[1]) != 1
            ):
                msg = "A builtin root alias does not match its RootModel value"
                raise BindingCaptureError(msg)
            annotation = application[1][0]
        elif (call := _application(annotation, "(")) is not None and _resolved_name(call[0], self.bindings) in {
            "typing.TypeAliasType",
            "typing_extensions.TypeAliasType",
        }:
            if len(call[1]) != _PAIR_SIZE or _string_literal(call[1][0]) != name:
                msg = "A builtin TypeAliasType value does not match its symbol"
                raise BindingCaptureError(msg)
            annotation = call[1][1]
        self._class_declaration(name, field, annotation, None, tokens)

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
            self.found[name, ordinal] = FieldArtifactDeclaration(
                field,
                _text(annotation),
                None,
                annotation[0].start[0],
                annotation[0].start[1],
                self._field_facts(field, annotation, None),
            )

    def class_field(self, current_class: str, tokens: Tokens) -> None:
        """Match only own annotated statements; function and nested-class bodies are skipped."""
        if (parsed := _class_field(tokens)) is not None:
            self.own_fields[current_class].append(parsed[0])
        elif len(tokens) > _PAIR_SIZE and tokens[0].type == tokenize.NAME and tokens[1].string == "=":
            self.settings[current_class].append(SourceExpression(_text(tokens)))
        if (fields := self.wanted.get(current_class)) is None:
            return
        if (
            (extra := fields.get("__pydantic_extra__")) is not None
            and extra.backend == "pydantic"
            and len(tokens) > _PAIR_SIZE
            and tuple(token.string for token in tokens[:2]) == ("__annotations__", "=")
        ):
            for key, annotation in _annotation_entries(tokens[2:], "Pydantic extra annotations"):
                if key == extra.native_name:
                    self._class_declaration(current_class, extra, annotation, None, tokens)
            return
        if parsed is None:
            return
        name, annotation, assignment = parsed
        if (field := fields.get(name)) is None or field.form != "class_field":
            return
        self._class_declaration(current_class, field, annotation, assignment, tokens)

    def _class_declaration(
        self,
        current_class: str,
        field: ExpectedFieldDeclaration,
        annotation: Tokens,
        assignment: Tokens | None,
        tokens: Tokens,
    ) -> None:
        if field.excluded_by_tag:
            msg = "A tag-excluded field is declared in its accepted artifact"
            raise BindingCaptureError(msg)
        key = current_class, field.native_name
        if key in self.found:
            msg = "An accepted class declares the same expected field more than once"
            raise BindingCaptureError(msg)
        self.found[key] = FieldArtifactDeclaration(
            field,
            _text(annotation),
            _text(assignment) if assignment is not None else None,
            tokens[0].start[0],
            tokens[0].start[1],
            self._field_facts(field, annotation, assignment),
        )

    def _field_facts(
        self, field: ExpectedFieldDeclaration, annotation: Tokens, assignment: Tokens | None
    ) -> EmittedFieldFacts:
        matcher = _TypePlacementMatcher(self.bindings, self.symbols, self.expected_bindings)
        matcher.match_field(field.type, annotation)
        return replace(_emitted_facts(field, annotation, assignment, self.bindings), meta_layers=tuple(matcher.layers))

    def finish(
        self, body: str, expected: tuple[ExpectedFieldDeclaration, ...], *, collect_errors: bool
    ) -> BuiltinFieldArtifactIndex:
        """Freeze in expected consumer order after proving each requested declaration exists."""
        ordered: list[FieldArtifactDeclaration] = []
        for field in expected:
            if field.excluded_by_tag:
                if field.model_name not in self.defined:
                    if collect_errors:
                        self.invalid_models[field.model_name] = None
                        continue
                    msg = "A tag-excluded field's final symbol is absent from its accepted artifact"
                    raise BindingCaptureError(msg)
                ordered.append(
                    FieldArtifactDeclaration(
                        field,
                        None,
                        None,
                        None,
                        None,
                        EmittedFieldFacts(
                            emitted=False,
                            emitted_default_kind="absent",
                            emitted_default_value=None,
                            factory_present=False,
                            factory_expression=None,
                            unset_default=False,
                            unset_type_in_annotation=False,
                            null_type_in_annotation=False,
                            qualifiers=(),
                            constructor_keywords=(),
                        ),
                    )
                )
                continue
            key = field.entry_ordinal if field.form == "typeddict_entry" else field.native_name
            if (declaration := self.found.get((field.model_name, key))) is None:
                if collect_errors:
                    self.invalid_models[field.model_name] = None
                    continue
                msg = "An expected final field is absent from its accepted artifact"
                raise BindingCaptureError(msg)
            ordered.append(declaration)
        return BuiltinFieldArtifactIndex(
            sha256(body.encode()).hexdigest(),
            tuple(self.definitions),
            tuple(ordered),
            tuple(
                ArtifactModelDeclaration(name, tuple(fields), tuple(self.settings[name]))
                for name, fields in self.own_fields.items()
            ),
            tuple(self.bindings.items()),
            tuple(self.invalid_models),
        )


def index_builtin_field_declarations(
    body: str,
    *,
    expected: tuple[ExpectedFieldDeclaration, ...],
    imports: FrozenImportBindings,
    collect_errors: bool = False,
) -> BuiltinFieldArtifactIndex:
    """Associate known fields with their actual class or functional declarations."""
    builder = _ArtifactIndexBuilder(expected, imports)
    current_class: str | None = None
    local_scope: int | None = None
    function_scope: int | None = None
    for statement in _statements(body):
        if local_scope is not None and statement.indent <= local_scope:
            local_scope = None
        if function_scope is not None and statement.indent <= function_scope:
            function_scope = None
        is_function = statement.tokens[0].string == "def" or tuple(token.string for token in statement.tokens[:2]) == (
            "async",
            "def",
        )
        executable = _split(statement.tokens, ":")[0] if is_function else statement.tokens
        if len(executable) > 1 and executable[1].string in {"=", ":"}:
            executable = executable[2:]
        if function_scope is None and any(
            token.type == tokenize.NAME
            and token.string in {"exec", "eval", "globals", "locals", "vars", "setattr", "__builtins__", "__import__"}
            for token in executable
        ):
            msg = "Dynamic artifact bindings require an explicit export adapter"
            raise BindingCaptureError(msg)
        if function_scope is None and is_function:
            function_scope = statement.indent
        try:
            if statement.indent == 0:
                current_class = builder.top_level(statement.tokens)
            elif statement.indent == 1 and current_class is not None:
                builder.class_field(current_class, statement.tokens)
            elif local_scope is None:
                builder.module_block(statement.tokens)
            if local_scope is None and (statement.tokens[0].string == "class" or is_function):
                local_scope = statement.indent
        except BindingCaptureError:
            model = builder.current_symbol if statement.indent == 0 else current_class
            if not collect_errors or model is None:
                raise
            builder.invalid_models[model] = None
    return builder.finish(body, expected, collect_errors=collect_errors)


def same_emitted_field_facts(expected: EmittedFieldFacts, actual: EmittedFieldFacts) -> bool:
    """Compare accepted semantic values while allowing formatter-only token locations."""

    def same_value(
        left: FrozenLiteral | SourceExpression | None, right: FrozenLiteral | SourceExpression | None
    ) -> bool:
        if isinstance(left, SourceExpression) and isinstance(right, SourceExpression):
            return _same_expression(_expression_tokens(left.text), _expression_tokens(right.text))
        return left == right

    def same_keywords(
        left: tuple[tuple[str, FrozenLiteral | SourceExpression], ...],
        right: tuple[tuple[str, FrozenLiteral | SourceExpression], ...],
    ) -> bool:
        return len(left) == len(right) and all(
            left_key == right_key and same_value(left_value, right_value)
            for (left_key, left_value), (right_key, right_value) in zip(left, right, strict=True)
        )

    if (
        not same_value(expected.emitted_default_value, actual.emitted_default_value)
        or not same_value(expected.factory_expression, actual.factory_expression)
        or not same_keywords(expected.constructor_keywords, actual.constructor_keywords)
        or len(expected.meta_layers) != len(actual.meta_layers)
    ):
        return False
    if any(
        left.node_path != right.node_path
        or left.ordinal != right.ordinal
        or not same_keywords(left.keywords, right.keywords)
        for left, right in zip(expected.meta_layers, actual.meta_layers, strict=True)
    ):
        return False
    return expected == replace(
        actual,
        emitted_default_value=expected.emitted_default_value,
        factory_expression=expected.factory_expression,
        constructor_keywords=expected.constructor_keywords,
        meta_layers=expected.meta_layers,
    )


def split_artifact_models(index: BuiltinFieldArtifactIndex) -> dict[str, BuiltinFieldArtifactIndex]:
    """Partition module evidence once so per-model validation stays linear in output size."""
    definitions: dict[str, list[ArtifactDefinition]] = {}
    fields: dict[str, list[FieldArtifactDeclaration]] = {}
    models: dict[str, list[ArtifactModelDeclaration]] = {}
    for definition in index.definitions:
        if definition.kind != "import":
            definitions.setdefault(definition.name, []).append(definition)
    for field in index.fields:
        fields.setdefault(field.expected.model_name, []).append(field)
    for model in index.models:
        models.setdefault(model.name, []).append(model)
    namespace = dict(index.namespace)
    invalid = set(index.invalid_models)
    result: dict[str, BuiltinFieldArtifactIndex] = {}
    for name in definitions.keys() | fields.keys() | models.keys():
        own_definitions, own_fields, own_models = (
            tuple(definitions.get(name, ())),
            tuple(fields.get(name, ())),
            tuple(models.get(name, ())),
        )
        sources = (
            *(
                source.text
                for definition in own_definitions
                for source in (definition.signature, *definition.decorators)
            ),
            *(source.text for model in own_models for source in model.settings),
            *(text for field in own_fields for text in (field.annotation, field.assignment) if text is not None),
        )
        names = dict.fromkeys(
            token.string for text in sources for token in _expression_tokens(text) if token.type == tokenize.NAME
        )
        result[name] = replace(
            index,
            definitions=own_definitions,
            fields=own_fields,
            models=own_models,
            namespace=tuple((name, namespace[name]) for name in names if name in namespace),
            invalid_models=(name,) if name in invalid else (),
        )
    return result


def same_artifact_model_facts(
    expected: BuiltinFieldArtifactIndex, actual: BuiltinFieldArtifactIndex, *, model_name: str | None = None
) -> bool:
    """Corroborate bases, decorators, enum values, own fields, and adopted class settings."""

    def same_source(left: SourceExpression, right: SourceExpression) -> bool:
        return _same_expression(_expression_tokens(left.text), _expression_tokens(right.text))

    def same_sequence(left: tuple[SourceExpression, ...], right: tuple[SourceExpression, ...]) -> bool:
        return len(left) == len(right) and all(starmap(same_source, zip(left, right, strict=True)))

    left_definitions = tuple(
        value
        for value in expected.definitions
        if value.kind != "import" and (model_name is None or value.name == model_name)
    )
    right_definitions = tuple(
        value
        for value in actual.definitions
        if value.kind != "import" and (model_name is None or value.name == model_name)
    )
    left_models = tuple(value for value in expected.models if model_name is None or value.name == model_name)
    right_models = tuple(value for value in actual.models if model_name is None or value.name == model_name)
    if len(left_definitions) != len(right_definitions) or len(left_models) != len(right_models):
        return False
    namespace = dict(actual.namespace)
    sources = (
        *(source.text for definition in left_definitions for source in (definition.signature, *definition.decorators)),
        *(source.text for model in left_models for source in model.settings),
        *(
            text
            for field in expected.fields
            if model_name is None or field.expected.model_name == model_name
            for text in (field.annotation, field.assignment)
            if text is not None
        ),
    )
    used = {token.string for text in sources for token in _expression_tokens(text) if token.type == tokenize.NAME}
    expected_namespace = dict(expected.namespace)
    if any(expected_namespace.get(name) != namespace.get(name) for name in used):
        return False
    if any(
        left.name != right.name
        or left.kind != right.kind
        or not same_source(left.signature, right.signature)
        or not same_sequence(left.decorators, right.decorators)
        for left, right in zip(left_definitions, right_definitions, strict=True)
    ):
        return False
    return all(
        left.name == right.name and left.fields == right.fields and same_sequence(left.settings, right.settings)
        for left, right in zip(left_models, right_models, strict=True)
    )


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
    backend: BackendName | None = None
    constructor_init: bool | None = None
    kw_only: bool | None = None


@dataclass(frozen=True, slots=True)
class KnownBackendValue:
    """Retain a finite declaration, including explicit None and False."""

    value: TypeArgument
    state: Literal["known"] = "known"


@dataclass(frozen=True, slots=True)
class RuntimeBackendValue:
    """Identify an effect that generation must never execute to discover."""

    reason: Literal["factory_result", "fields_set", "expression"]
    state: Literal["runtime"] = "runtime"


@dataclass(frozen=True, slots=True)
class OpaqueBackendValue:
    """Keep unavailable semantics distinct from builtin defaults."""

    reason: Literal["custom_origin", "unsupported_value", "model_policy_required"]
    state: Literal["opaque"] = "opaque"


BackendValue: TypeAlias = KnownBackendValue | RuntimeBackendValue | OpaqueBackendValue


@dataclass(frozen=True, slots=True)
class BackendFieldFacts:
    """Freeze builtin field declarations separately from their runtime effects."""

    backend: BackendName
    declarations: tuple[tuple[str, BackendValue], ...]
    emitted: EmittedFieldFacts
    constructor_init: BackendValue
    init_var: BackendValue
    kw_only: BackendValue
    factory_result: BackendValue
    fields_set: BackendValue


def _backend_value(value: object) -> BackendValue:
    try:
        return KnownBackendValue(freeze_argument(value))
    except UnsupportedBindingValueError:
        return OpaqueBackendValue("unsupported_value")


def _constructor_setting(name: str, emitted: EmittedFieldFacts, *, fallback: bool | None) -> BackendValue:
    for key, value in reversed(emitted.constructor_keywords):
        if key == name:
            return (
                RuntimeBackendValue("expression") if isinstance(value, SourceExpression) else KnownBackendValue(value)
            )
    return _backend_value(fallback) if fallback is not None else OpaqueBackendValue("model_policy_required")


def freeze_builtin_field_facts(
    field: DataModelFieldBase, *, emitted: EmittedFieldFacts, projection: FieldProjectionContext
) -> BackendFieldFacts:
    """Read finite raw declarations and accepted syntax, without backend getter calls."""
    if (backend := projection.backend) is None:
        msg = "A builtin field projection requires its established backend"
        raise BindingCaptureError(msg)
    names = (
        "name",
        "original_name",
        "alias",
        "validation_aliases",
        "serialization_alias",
        "use_serialization_alias",
    )
    if not projection.builtin_semantics:
        opaque = OpaqueBackendValue("custom_origin")
        return BackendFieldFacts(
            backend, tuple((name, opaque) for name in names), emitted, opaque, opaque, opaque, opaque, opaque
        )
    declarations = (
        ("name", _backend_value(field.name)),
        ("original_name", _backend_value(field.original_name)),
        ("alias", _backend_value(field.alias)),
        ("validation_aliases", _backend_value(field.validation_aliases)),
        ("serialization_alias", _backend_value(field.serialization_alias)),
        ("use_serialization_alias", _backend_value(field.use_serialization_alias)),
    )
    if backend == "typeddict":
        constructor_init = init_var = kw_only = _backend_value(None)
    else:
        constructor_init = (
            _backend_value(value=False)
            if not emitted.emitted or "ClassVar" in emitted.qualifiers
            else _constructor_setting("init", emitted, fallback=projection.constructor_init)
        )
        init_var = _constructor_setting("init_var", emitted, fallback="InitVar" in emitted.qualifiers)
        kw_only = _constructor_setting("kw_only", emitted, fallback=projection.kw_only)
    return BackendFieldFacts(
        backend,
        declarations,
        emitted,
        constructor_init,
        init_var,
        kw_only,
        RuntimeBackendValue("factory_result") if emitted.factory_present else _backend_value(None),
        RuntimeBackendValue("fields_set") if backend == "pydantic" else _backend_value(None),
    )


def freeze_model_field_facts(
    field: DataModelFieldBase,
    *,
    type_value: FinalPythonType,
    emitted: EmittedFieldFacts,
    projection: FieldProjectionContext,
) -> ModelFieldFacts:
    """Read final data attributes once, using the same accepted default observation."""
    return ModelFieldFacts(
        field.required,
        field.nullable,
        field.has_default,
        "default_factory" in field.extras,
        field.type_has_null,
        field.read_only,
        field.write_only,
        field.alias,
        tuple(field.validation_aliases) if field.validation_aliases is not None else None,
        field.serialization_alias,
        field.use_serialization_alias,
        type_value,
        freeze_builtin_field_facts(field, emitted=emitted, projection=projection),
        freeze_none_default_provenance(field, emitted=emitted, projection=projection),
    )


@dataclass(frozen=True, slots=True)
class BackendSetting:
    """Distinguish an omitted declaration from explicit None and unknown presence."""

    name: str
    present: bool | None
    value: BackendValue


@dataclass(frozen=True, slots=True)
class ModelProjectionContext:
    """Carry final backend and type identities without invoking model callbacks."""

    backend: BackendName
    builtin_semantics: bool
    functional_typeddict: bool = False
    extra_items: FinalPythonType | None = None


@dataclass(frozen=True, slots=True)
class BackendModelFacts:
    """Keep finite adopted model declarations independently of runtime defaults."""

    backend: BackendName
    parameters: tuple[BackendSetting, ...]
    configuration: tuple[BackendSetting, ...]
    functional_typeddict: bool
    extra_items_present: bool | None
    extra_items: FinalPythonType | None


_DATACLASS_PARAMETERS: Final = (
    "init",
    "repr",
    "eq",
    "order",
    "unsafe_hash",
    "frozen",
    "match_args",
    "kw_only",
    "slots",
    "weakref_slot",
)
_MSGSPEC_PARAMETERS: Final = (
    "tag",
    "tag_field",
    "array_like",
    "forbid_unknown_fields",
    "omit_defaults",
    "kw_only",
    "frozen",
    "rename",
)
_PYDANTIC_CONFIGURATION: Final = (
    "extra",
    "strict",
    "validate_by_name",
    "populate_by_name",
    "validate_by_alias",
    "frozen",
    "alias_generator",
)


def _model_parameters(backend: BackendName) -> tuple[str, ...]:
    match backend:
        case "dataclass" | "pydantic_dataclass":
            return _DATACLASS_PARAMETERS
        case "msgspec":
            return _MSGSPEC_PARAMETERS
        case "typeddict":
            return ("total", "closed")
        case _:
            return ()


def _raw_mapping(value: object) -> dict[str, object] | None:
    if type(value) is dict and all(type(key) is str for key in cast("dict[object, object]", value)):
        return cast("dict[str, object]", value)
    return None


def _syntax_value(value: object) -> BackendValue:
    if type(value) is not str:
        return OpaqueBackendValue("unsupported_value")
    return KnownBackendValue(_literal_or_syntax(_expression_tokens(value)))


def _freeze_settings(names: tuple[str, ...], values: dict[str, BackendValue] | None) -> tuple[BackendSetting, ...]:
    if values is None:
        return tuple(BackendSetting(name, None, OpaqueBackendValue("custom_origin")) for name in names)
    return tuple(
        BackendSetting(name, name in values, values[name] if name in values else _backend_value(None)) for name in names
    )


def _model_parameter_values(  # ruff: ignore[too-many-return-statements]
    model: DataModel, backend: BackendName
) -> tuple[dict[str, BackendValue] | None, bool]:
    internal: dict[str, object] = model._internal_template_data  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
    match backend:
        case "dataclass" | "pydantic_dataclass":
            if (arguments := _raw_mapping(model.dataclass_arguments)) is None:
                return None, False
            return {
                name: _backend_value(value)
                for name, value in arguments.items()
                if value is not False and value is not None
            }, False
        case "msgspec":
            if (raw := _raw_mapping(model.extra_template_data.get("base_class_kwargs", {}))) is None:
                return None, False
            if (adopted := _raw_mapping(internal.get("base_class_kwargs", {}))) is None:
                return None, False
            values = {name: _backend_value(value) for name, value in raw.items() if name in _MSGSPEC_PARAMETERS}
            values.update(
                (name, _syntax_value(value)) for name, value in adopted.items() if name in _MSGSPEC_PARAMETERS
            )
            return values, False
        case "typeddict":
            if (arguments := _raw_mapping(internal.get("typed_dict_kwargs", {}))) is None:
                return None, False
            return {name: _syntax_value(value) for name, value in arguments.items()}, "extra_items" in arguments
        case _:
            pass
    return {}, False


def _model_configuration(
    model: DataModel, backend: Literal["pydantic", "pydantic_dataclass"]
) -> dict[str, BackendValue] | None:
    key = "config_items" if backend == "pydantic" else "_safe_config_items"
    values: object = model._internal_template_data.get(key, ())  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
    if type(values) not in {tuple, list}:
        return None
    settings: dict[str, BackendValue] = {}
    for item in cast("tuple[object, ...] | list[object]", values):
        if type(item) is not tuple:
            return None
        pair = cast("Sequence[object]", item)
        if len(pair) != _PAIR_SIZE or type(pair[0]) is not str:
            return None
        if pair[0] in _PYDANTIC_CONFIGURATION:
            settings[pair[0]] = _syntax_value(pair[1])
    return settings


def freeze_builtin_model_facts(model: DataModel, *, projection: ModelProjectionContext) -> BackendModelFacts:
    """Read final finite template inputs without rendering or recomputing configuration."""
    backend = projection.backend
    if not projection.builtin_semantics:
        return BackendModelFacts(
            backend,
            _freeze_settings(_model_parameters(backend), None),
            _freeze_settings(_PYDANTIC_CONFIGURATION, None) if backend in {"pydantic", "pydantic_dataclass"} else (),
            projection.functional_typeddict,
            None,
            None,
        )
    values, extra_present = _model_parameter_values(model, backend)
    if values is not None and extra_present != (projection.extra_items is not None):
        msg = "TypedDict extra_items does not match its captured final type"
        raise BindingCaptureError(msg)
    configuration: tuple[BackendSetting, ...] = ()
    match backend:
        case "pydantic" | "pydantic_dataclass":
            configuration = _freeze_settings(_PYDANTIC_CONFIGURATION, _model_configuration(model, backend))
        case _:
            pass
    return BackendModelFacts(
        backend,
        _freeze_settings(_model_parameters(backend), values),
        configuration,
        projection.functional_typeddict,
        extra_present if values is not None else None,
        projection.extra_items,
    )


def freeze_none_default_provenance(
    field: DataModelFieldBase, *, emitted: EmittedFieldFacts, projection: FieldProjectionContext
) -> NoneDefaultProvenance:
    """Prove ordinary None synthesis from producer facts and accepted syntax, never a getter."""
    default_kind = _PROVENANCE_DEFAULTS[emitted.emitted_default_kind]
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


def freeze_alias_nullability(
    field: DataModelFieldBase,
    *,
    type_value: FinalPythonType,
    emitted: EmittedFieldFacts,
    aliases: Container[SymbolId],
    opaque_type: bool,
) -> tuple[bool | None, set[SymbolId]]:
    """Corroborate top-level alias null producers without traversing container items or getters."""
    direct = field.nullable is True or (field.nullable is None and field.required and field.type_has_null)
    unknown = opaque_type
    references: set[SymbolId] = set()
    pending = [type_value]
    while pending:
        match pending.pop():
            case NoneType():
                direct = True
            case UnionType(members, _):
                pending.extend(members)
            case GeneratedSymbolType(reference) if reference in aliases:
                references.add(reference)
            case AnnotatedType() | BoundType():
                unknown = True
            case _:
                pass
    if direct and emitted.null_type_in_annotation:
        return True, references
    if unknown or bool(direct) != emitted.null_type_in_annotation:
        return None, references
    return False, references


def _annotation_null_origin(
    field: DataModelFieldBase,
    *,
    emitted: EmittedFieldFacts,
    projection: FieldProjectionContext,
    unknown: bool,
    fallback: bool,
) -> Literal["optional_fallback", "schema", "model_configuration", "preexisting_type", "none", "opaque"]:
    if not emitted.null_type_in_annotation and projection.preexisting_null is False:
        return "none"
    if unknown:
        return "opaque"
    if projection.explicit_nullable:
        return "schema"
    if projection.preexisting_null:
        return "preexisting_type"
    if projection.configuration_nullable or (projection.original_required and not field.required):
        return "model_configuration"
    return "optional_fallback" if fallback else "opaque"


@dataclass(frozen=True, slots=True)
class FinalReferencePolicy:
    """Read the final nullable/alias policy without evaluating reference getters."""

    nullable: bool
    is_alias: bool
    serialize_as_any: bool


def freeze_reference_policy(model: DataModel, *, serialize_as_any: bool) -> FinalReferencePolicy:
    """Project raw builtin state for a final reference; never compute a type hint."""
    return FinalReferencePolicy(
        model._nullable,  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access] -- Read the stored flag without invoking its property.
        model.IS_ALIAS,
        serialize_as_any and any(isinstance(child, DataModel) and child.fields for child in model.reference.children),
    )


if TYPE_CHECKING:
    from collections.abc import Container, Iterator, Sequence

    from datamodel_code_generator._generation_contract import (
        AttemptId,
        FieldSlot,
        FinalPythonType,
        FrozenLiteral,
        MetadataCall,
        SymbolId,
        TypeArgument,
        UnannotatedPythonType,
    )
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModelFieldBase
