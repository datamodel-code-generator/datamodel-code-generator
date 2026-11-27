"""Plan the tool table of a client package: one tool for each planned operation whose call takes and returns JSON.

A tool is a view of an operation of the final client plan. Its name is the client's call path joined with `_`, such as
`pets_get_pet`; its arguments are the operation's parameters by their Python names, without those at a credential's
position, which the client fills, and the body of its JSON media as `body`. An operation whose arguments or success
results are no JSON value, a HEAD operation, and one whose tool name is not a valid tool name or is taken keeps its
method and is left out of the table, with the reasons; one whose `tool` setting is False is left out without one.
"""

from __future__ import annotations

import re
from ast import literal_eval
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client.plan import STREAMED
from datamodel_code_generator._regex_syntax import python_rejects
from datamodel_code_generator._runtime.client.positions import protected_positions, secret_names
from datamodel_code_generator._runtime.model_codecs.media import essence, media_range
from datamodel_code_generator._target_contract import (
    ConstructorType,
    GenericType,
    LiteralScalar,
    SourceExpression,
    UnionType,
)
from datamodel_code_generator._target_operations import CLIENT, operation_docstring
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Container, Iterable, Iterator

    from datamodel_code_generator._client.plan import ClientPlan, MediaSpec, OperationSpec, ParameterSpec
    from datamodel_code_generator._target_contract import (
        FieldUseBinding,
        TypeArgument,
        TypeUseBinding,
        TypeUseId,
        TypeView,
    )

MCP_DEPENDENCY: Final = "mcp>=2.3.0"
"""The requirement of a package whose tools are served over MCP: the release of the SDK its server is tested with."""
_NAME: Final = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_SAFE: Final = frozenset({"get", "head", "options", "trace"})
_IDEMPOTENT: Final = frozenset({*_SAFE, "put", "delete"})
_RESULTS: Final = frozenset({"json", "text"})


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolArgumentSpec:
    """One argument of a tool: its name, the keyword of the operation's method, its type use, and requiredness."""

    name: str
    use: TypeUseBinding
    required: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolSpec:
    """One tool of the table: its name, its operation, its arguments, and what its HTTP method promises.

    `media_type` is the JSON request media a call names beside its body, when the operation sends none by default, and
    `description` is the text an agent reads, as the operation's method docstring says it.
    """

    name: str
    description: str
    operation: OperationSpec
    arguments: tuple[ToolArgumentSpec, ...]
    media_type: str | None
    safe: bool
    idempotent: bool


def plan_tools(
    plan: ClientPlan, coded: Container[TypeUseId], *, schema: bool
) -> tuple[tuple[ToolSpec, ...], list[tuple[str, str]]]:
    """Return the tools of a client plan's operations, and each operation left out with why, in operation order.

    `coded` holds the uses that have a codec, through which a tool validates an argument, and `schema` puts the
    operation's summary and description in its tool's, as `--use-schema-description` does in docstrings. An operation
    whose `tool` setting is False has no tool and takes no tool name, without a reason.
    """
    headers, queries = secret_names(plan.security_schemes)
    cookies = protected_positions(plan.security_schemes)[2]
    secret = {"header": headers, "query": queries, "cookie": cookies}
    tools: list[ToolSpec] = []
    left_out: list[tuple[str, str]] = []
    named: dict[str, OperationSpec] = {}
    for spec in plan.operations:
        if not spec.tool:
            continue
        name = "_".join((*spec.resource.split("."), spec.name))
        arguments = [
            item
            for parameter in spec.parameters
            if not _credential(parameter, secret)
            and (item := _argument(parameter.python_name, parameter.use, coded, required=parameter.required))
        ]
        body = None if spec.body is None else _body_media(spec)
        reasons = list(_reasons(spec, arguments, body, coded, secret))
        if not _NAME.fullmatch(name):
            reasons.append(
                f"its tool name {stable_repr(name)} is not 1 to 128 ASCII letters, digits, '_', '.', or '-'; the name "
                "key of --client-operations names its method"
            )
        elif (other := named.get(name)) is not None:
            reasons.append(
                f"its tool name {stable_repr(name)} is that of {_subject(other)}; the name key of --client-operations "
                "names its method"
            )
        if reasons:
            left_out.append((_subject(spec), "; ".join(reasons)))
            continue
        named[name] = spec
        media_type = None
        if spec.body is not None and body is not None and body.use is not None:
            arguments.append(ToolArgumentSpec(name="body", use=body.use, required=spec.body.required))
            media_type = body.media_type if spec.body.default is None else None
        method = spec.contract.method
        tools.append(
            ToolSpec(
                name=name,
                description=operation_docstring(
                    spec.contract, schema=schema, verb=CLIENT.verb, description=spec.description
                ),
                operation=spec,
                arguments=tuple(arguments),
                media_type=media_type,
                safe=method in _SAFE,
                idempotent=method in _IDEMPOTENT,
            )
        )
    return tuple(tools), left_out


def rejected_patterns(fields: Iterable[FieldUseBinding]) -> frozenset[str]:
    """Return the patterns of the models' fields that Python's `re` rejects, decided statically (`python_rejects`).

    They are those of each field's call and of its constrained types, as the models write them and as their JSON
    schemas hold them; an MCP client, which checks a result against its schema with Python's `re`, cannot read them.
    """
    return frozenset(
        pattern
        for member in fields
        if (facts := member.model_facts) is not None
        for pattern in (*_keyword_patterns(facts.backend.emitted.constructor_keywords), *_type_patterns(facts.type))
        if python_rejects(pattern)
    )


def _type_patterns(value: TypeView) -> Iterator[str]:
    """Yield the patterns of a type's constrained strings, those of its arguments and members included."""
    match value:
        case ConstructorType():
            yield from _keyword_patterns(value.keywords)
        case GenericType():
            for item in value.arguments:
                yield from _type_patterns(item)
        case UnionType():
            for item in value.members:
                yield from _type_patterns(item)


def _keyword_patterns(keywords: Iterable[tuple[str, TypeArgument]]) -> Iterator[str]:
    """Yield the pattern a call's `pattern` keyword writes as a string; a compiled one is one Python's `re` read."""
    for name, value in keywords:
        if name == "pattern" and isinstance(text := _written(value), str):
            yield text


def _written(value: TypeArgument) -> object:
    """Return the value a keyword argument writes: a literal's, that of the source text of one, else None."""
    if isinstance(value, SourceExpression):
        return literal_eval(value.text)
    return value.value if isinstance(value, LiteralScalar) else None


def _argument(
    name: str, use: TypeUseBinding | None, coded: Container[TypeUseId], *, required: bool = False
) -> ToolArgumentSpec | None:
    """Return the argument of a type use that has a codec, or None."""
    if use is None or use.type is None or use.id not in coded:
        return None
    return ToolArgumentSpec(name=name, use=use, required=required)


def _credential(parameter: ParameterSpec, secret: dict[str, frozenset[str]]) -> bool:
    """Return whether a parameter sits at a credential's position, which the client fills and no tool argument may."""
    if (names := secret.get(parameter.location)) is None:
        return False
    return (parameter.wire_name.lower() if parameter.location == "header" else parameter.wire_name) in names


def _body_media(spec: OperationSpec) -> MediaSpec | None:
    """Return the request media a tool sends its body as: the one a call sends by default, else the first JSON one."""
    assert spec.body is not None
    media = spec.body.media
    if (default := spec.body.default) is not None:
        return next(item for item in media if item.media_type == default)
    return next((item for item in media if item.kind == "json" and not media_range(item.media_type)), None)


def _reasons(
    spec: OperationSpec,
    arguments: list[ToolArgumentSpec],
    body: MediaSpec | None,
    coded: Container[TypeUseId],
    secret: dict[str, frozenset[str]],
) -> Iterator[str]:
    """Yield why an operation has no tool: an argument or a success result that is no JSON value, or a HEAD method."""
    if spec.head:
        yield "a HEAD response has no body to return"
    taken = {item.name for item in arguments}
    for parameter in spec.parameters:
        if parameter.python_name not in taken and not _credential(parameter, secret):
            yield f"its {parameter.location} parameter {stable_repr(parameter.wire_name)} has no model type"
    if spec.body is not None and (
        body is None or body.members is not None or body.kind == "binary" or _argument("body", body.use, coded) is None
    ):
        declared = ", ".join(item.media_type for item in spec.body.media) or "undeclared"
        yield f"its request body ({declared}) takes no JSON value of a model type"
    for response in spec.responses:
        if not response.success or response.bodyless:
            continue
        for media in response.media:
            if essence(media.media_type) in STREAMED:
                yield f"its {response.status} response is a {media.media_type} stream"
            elif media.kind not in _RESULTS or media.members is not None:
                yield f"its {response.status} {media.media_type} response is no JSON value"


def _subject(spec: OperationSpec) -> str:
    """Return how a warning names an operation: its method and path, and its client method."""
    return f"{spec.contract.method.upper()} {spec.contract.path} (client.{spec.resource}.{spec.name})"
