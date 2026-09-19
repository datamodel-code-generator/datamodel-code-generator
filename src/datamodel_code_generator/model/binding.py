"""Read finite builtin declarations from accepted artifacts without executing them."""

from __future__ import annotations

import ast
import tokenize
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from itertools import starmap
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

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

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
EmissionForm: TypeAlias = Literal["class_field", "typeddict_entry"]


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


def _expression_tokens(text: str) -> Tokens:
    statements = tuple(_statements(text + "\n"))
    return statements[0].tokens if len(statements) == 1 else ()


def _union_parts(tokens: Tokens) -> tuple[Tokens | None, ...] | None:
    """Preserve source order; the implicit Optional null has no source token."""
    parts = _split(_unparenthesized(tokens), "|")
    return parts if len(parts) > 1 else None


class _TypePlacementMatcher:
    """Corroborate projected identities; source tokens never create type identities."""

    def __init__(self, bindings: dict[str, str], symbols: dict[SymbolId, str]) -> None:
        self.bindings = bindings
        self.symbols = symbols
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
        """Separate field policy wrappers from the existing data-type skeleton."""
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
            # Field-level Optional can enclose a complete union rather than flatten it.
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

    def _match(  # ruff: ignore[too-many-branches] # Exhaustive finite type alternatives.
        self, expected: FinalPythonType, tokens: Tokens | None, path: tuple[int, ...]
    ) -> None:
        if tokens is None:
            if not isinstance(expected, NoneType):
                self._mismatch()
            return
        skeleton, tokens = self._projected_metadata(expected, tokens, path)
        match skeleton:
            case BuiltinType(name):
                matched = _dotted_name(tokens) == name and name not in self.bindings
            case NoneType():
                matched = _text(tokens) == "None"
            case ImportedType(import_, suffix):
                identity = _import_identity(import_)[1]
                matched = _resolved_name(tokens, self.bindings) == ".".join((identity, *suffix))
            case GeneratedSymbolType(symbol):
                matched = _dotted_name(tokens) == self.symbols.get(symbol)
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
            case ConstructorType():
                matched = self._match_constructor(skeleton, tokens, path)
        if not matched:
            self._mismatch()

    def _match_bound(self, expected: BoundType, tokens: Tokens) -> bool:
        return self._match_python_expression(expected.binding.expression, tokens)

    def _match_python_expression(  # ruff: ignore[too-many-branches] -- One case per retained expression kind.
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
        if (application := _application(tokens, "[")) is None:
            self._mismatch()
        callee, children = application
        if tuple_form == "fixed" and not arguments and len(children) == 1 and _text(children[0]) == "()":
            children = ()
        if tuple_form == "ellipsis":
            if not children or _text(children[-1]) != "...":
                self._mismatch()
            children = children[:-1]
        if isinstance(base, BuiltinType) and _resolved_name(callee, self.bindings) == _TYPING_CONTAINER_NAMES.get(
            base.name, ""
        ):
            pass
        else:
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
                    matched = matched and _dotted_name(child) == f"{self.symbols.get(value.symbol)}.{value.name}"
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
        self.symbols = dict(imports.symbols)
        self.symbols.update((field.consumer, field.model_name) for field in expected)
        self.definitions: list[ArtifactDefinition] = []
        self.found: dict[tuple[str, str | int | None], FieldArtifactDeclaration] = {}
        self.defined: set[str] = set()

    def _definition(self, name: str, kind: Literal["class", "type_alias", "assignment"], line: int) -> None:
        if name in self.wanted and name in self.defined:
            msg = "A final symbol is declared more than once in its accepted artifact"
            raise BindingCaptureError(msg)
        self.defined.add(name)
        self.bindings[name] = ""
        self.definitions.append(ArtifactDefinition(name, kind, line))

    def top_level(self, tokens: Tokens) -> str | None:
        """Read only top-level definitions, preserving actual import binding order."""
        for alias, identity, resolution_base in _import_names(tokens):
            if (alias, identity) in self.allowed:
                self.bindings[alias] = resolution_base
            else:
                self.bindings[alias] = ""
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
        if (fields := self.wanted.get(current_class)) is None or (parsed := _class_field(tokens)) is None:
            return
        name, annotation, assignment = parsed
        if (field := fields.get(name)) is None or field.form != "class_field":
            return
        if field.excluded_by_tag:
            msg = "A tag-excluded field is declared in its accepted artifact"
            raise BindingCaptureError(msg)
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
            self._field_facts(field, annotation, assignment),
        )

    def _field_facts(
        self, field: ExpectedFieldDeclaration, annotation: Tokens, assignment: Tokens | None
    ) -> EmittedFieldFacts:
        matcher = _TypePlacementMatcher(self.bindings, self.symbols)
        matcher.match_field(field.type, annotation)
        return replace(_emitted_facts(field, annotation, assignment, self.bindings), meta_layers=tuple(matcher.layers))

    def finish(self, body: str, expected: tuple[ExpectedFieldDeclaration, ...]) -> BuiltinFieldArtifactIndex:
        """Freeze in expected consumer order after proving each requested declaration exists."""
        ordered: list[FieldArtifactDeclaration] = []
        for field in expected:
            if field.excluded_by_tag:
                if field.model_name not in self.defined:
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
    # These are declared data attributes. Constraint/default rendering is owned by
    # emitted, not reconstructed from raw extras or another field getter.
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
        case "pydantic":
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


def _model_parameter_values(  # ruff: ignore[too-many-return-statements] # Finite backend alternatives.
    model: DataModel, backend: BackendName
) -> tuple[dict[str, BackendValue] | None, bool]:
    internal: dict[str, object] = model._internal_template_data  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access] # Read-only builtin-owned keys.
    match backend:
        case "dataclass" | "pydantic_dataclass":
            if (arguments := _raw_mapping(model.dataclass_arguments)) is None:
                return None, False
            # Both builtin dataclass templates omit False/None decorator arguments.
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
        case "pydantic":
            return {}, False


def _model_configuration(model: DataModel, backend: BackendName) -> dict[str, BackendValue] | None:
    if backend not in {"pydantic", "pydantic_dataclass"}:
        return {}
    key = "config_items" if backend == "pydantic" else "_safe_config_items"
    values: object = model._internal_template_data.get(key, ())  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access] # Already normalized by the renderer owner.
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
    configuration = (
        _freeze_settings(_PYDANTIC_CONFIGURATION, _model_configuration(model, backend))
        if backend in {"pydantic", "pydantic_dataclass"}
        else ()
    )
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
    default_kind: Literal["absent", "none", "value", "factory", "missing", "opaque"] = "opaque"
    match emitted.emitted_default_kind:
        case "absent" | "none" | "factory" as kind:
            default_kind = kind
        case "msgspec_unset" | "pydantic_missing":
            default_kind = "missing"
        case "literal" | "expression":
            default_kind = "value"
        case "opaque":
            pass
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
