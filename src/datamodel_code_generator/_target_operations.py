"""Plan what the client and the server decide alike for each operation; each difference is a `TargetPolicy` row.

The targets keep their own scopes and forms, such as the client's resources and the server's router groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeVar

from datamodel_code_generator._api_types import Diagnostic, OperationRef
from datamodel_code_generator._target_contract import LiteralScalar, LiteralSequence
from datamodel_code_generator._target_naming import NameScope, explicit_name, operation_basis, path_placeholders

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._api_generation import TargetRequest
    from datamodel_code_generator._api_types import DiagnosticStage, OperationSelector
    from datamodel_code_generator._runtime.model_codecs.parameters import ParameterLocation
    from datamodel_code_generator._target_contract import (
        FrozenLiteral,
        OperationContract,
        SourceLocation,
        TypeUseBinding,
        TypeUseId,
        WireDeclaration,
    )
    from datamodel_code_generator._target_naming import TargetNames

SettingT = TypeVar("SettingT")

BODYLESS_STATUSES: Final = frozenset({204, 205, 304})
"""The response statuses that never carry content."""

LOCATIONS: Final[dict[object, ParameterLocation]] = {
    "path": "path",
    "query": "query",
    "querystring": "querystring",
    "header": "header",
    "cookie": "cookie",
}
_CLIENT_CONFIG_CODES: Final = frozenset({"E_CONFIG_VALUE", "E_CONFIG_CONFLICT", "E_OPERATION_REF"})


@dataclass(frozen=True, slots=True, kw_only=True)
class TargetPolicy:
    """The rules the client and the server apply differently today, one row each.

    `stage` is the stage a planning diagnostic's code reports at; `duplicate_setting` the code and message, by the
    option and the operation key, of two settings of one option that select one operation; `path_code` the code of
    a path placeholder without a path parameter and of a path parameter without a placeholder, and
    `placeholder_label` whether the first names its operation.
    """

    stage: Callable[[str], DiagnosticStage]
    duplicate_setting: tuple[str, str]
    path_code: str
    placeholder_label: bool

    def problem(
        self, code: str, message: str, source: SourceLocation | None = None, option_path: str | None = None
    ) -> Diagnostic:
        """Return one planning error at its stage."""
        return Diagnostic(
            code=code,
            severity="error",
            stage=self.stage(code),
            message=message,
            source_pointer=None if source is None else source.pointer,
            option_path=option_path,
        )


CLIENT: Final = TargetPolicy(
    stage=lambda code: "config" if code in _CLIENT_CONFIG_CODES else "target",
    duplicate_setting=("E_CONFIG_CONFLICT", "Two settings name {key!r}"),
    path_code="E_METADATA_REQUIRED",
    placeholder_label=True,
)
SERVER: Final = TargetPolicy(
    stage=lambda code: "config" if code.startswith("E_") else "binding" if code.startswith("MC_") else "target",
    duplicate_setting=("E_CONFIG_VALUE", "The {option} setting names {key!r} twice"),
    path_code="F_ROUTE_INVALID",
    placeholder_label=False,
)


class PlanError(Exception):
    """Report every failure of one planning phase."""

    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        """Keep the ordered diagnostics."""
        super().__init__(diagnostics[0].message)
        self.diagnostics = tuple(diagnostics)


def fact(declaration: WireDeclaration, name: str) -> object:
    """Return one scalar wire fact of a declaration."""
    return next(
        (value.value for key, value in declaration.facts if key == name and isinstance(value, LiteralScalar)), None
    )


def operation_literal(operation: OperationContract, name: str) -> FrozenLiteral | None:
    """Return one effective wire fact of an operation as recorded, such as its security or servers."""
    return next((value for key, value in operation.facts if key == name), None)


def label(operation: OperationContract) -> str:
    """Return the method and path that name an operation in messages."""
    return f"{operation.method.upper()} {operation.path}"


def type_uses(declaration: WireDeclaration) -> tuple[TypeUseId, ...]:
    """Return the type uses of a declaration: its own, then those of its children."""
    return (*declaration.schemas, *(use for child in declaration.children for use in child.schemas))


def first_use(uses: Mapping[TypeUseId, TypeUseBinding], ids: Iterable[TypeUseId]) -> TypeUseBinding | None:
    """Return the first type use of a declaration that the batch binds."""
    return next((uses[use] for use in ids if use in uses), None)


def local(operation: OperationContract) -> bool:
    """Return whether the document that names the operation's path item declares the operation itself."""
    return operation.declaration.location.document == operation.id.use_site.document


def declared_by(declaration: WireDeclaration, operation: OperationContract) -> bool:
    """Return whether the document that names an operation's path item declares one of its declarations."""
    return declaration.declaration.location.document == operation.id.use_site.document


def tags(operation: OperationContract) -> tuple[str, ...]:
    """Return an operation's tags in declaration order."""
    if not isinstance(value := operation_literal(operation, "tags"), LiteralSequence):
        return ()
    return tuple(item.value for item in value.items if isinstance(item, LiteralScalar) and isinstance(item.value, str))


def group_tag(operation: OperationContract) -> str | None:
    """Return the tag that groups an operation: its first, or None for an operation without one."""
    return found[0] if (found := tags(operation)) else None


def derived_name(names: TargetNames, operation: OperationContract) -> str:
    """Return the snake_case name an operation derives from its operationId, or from its method and path."""
    basis = operation_basis(operation.method, operation.path, operation.operation_id, names.python_version)
    return names.function(basis)


def type_names(names: TargetNames, items: Iterable[tuple[str, OperationContract, tuple[str, ...]]]) -> list[str]:
    """Claim the PascalCase type name of each named operation in its scopes, as model classes are told apart."""
    return names.claim(
        NameScope(), [(names.pascal(name), local(operation), scopes) for name, operation, scopes in items], camel=True
    )


def argument_names(
    names: TargetNames, scope: NameScope, arguments: Sequence[tuple[str, str | None, bool]], scopes: tuple[str, ...]
) -> list[str]:
    """Name each argument: its given name, or one claimed in order after its wire name as a model field's.

    Each argument is its wire name, its given name or None, and whether the operation's document declares it.
    """
    bases = {index: names.argument(wire_name) for index, (wire_name, given, _) in enumerate(arguments) if given is None}
    claimed = dict(
        zip(
            bases,
            names.claim(scope, [(base, arguments[index][2], scopes) for index, base in bases.items()]),
            strict=True,
        )
    )
    return [given or claimed[index] for index, (_, given, _) in enumerate(arguments)]


def alias(  # noqa: PLR0913
    policy: TargetPolicy,
    names: TargetNames,
    operation: OperationContract,
    location: str,
    wire_name: str,
    *,
    source: SourceLocation,
    problems: list[Diagnostic],
) -> str | None:
    """Return the `--aliases` entry naming a parameter's argument, reporting one that is no identifier."""
    if (found := names.alias(wire_name)) is not None and not explicit_name(found, names.python_version):
        message = (
            f"The --aliases entry {found!r} of the {location} parameter {wire_name!r} of {label(operation)} "
            "is not an identifier"
        )
        problems.append(policy.problem("E_CONFIG_VALUE", message, source))
    return found


def resolve_settings(
    policy: TargetPolicy,
    request: TargetRequest,
    entries: Iterable[tuple[str, str, OperationSelector, SettingT]],
    problems: list[Diagnostic],
) -> dict[str, SettingT]:
    """Resolve the operation selector of each setting to its operation key, reporting one no operation answers.

    Each entry is the setting's subject and option path as messages name them, its selector, and its value.
    """
    resolved: dict[str, SettingT] = {}
    code, duplicate = policy.duplicate_setting
    for subject, option, selector, value in entries:
        reference = OperationRef(pointer=selector) if isinstance(selector, str) else selector
        if (operation := request.resolve(reference)) is None:
            from datamodel_code_generator._target_documents import named_document  # noqa: PLC0415

            named = named_document(reference.document, reference.document)
            message = f"The {subject} {reference.pointer!r}{named} {request.unresolved}"
            problems.append(policy.problem("E_OPERATION_REF", message, option_path=option))
        elif (key := operation.id.use_site.pointer) in resolved:
            problems.append(policy.problem(code, duplicate.format(option=option, key=key), option_path=option))
        else:
            resolved[key] = value
    return resolved


def placeholder_problems(
    policy: TargetPolicy, operation: OperationContract, placeholders: Iterable[str], declared: Container[str]
) -> Iterator[Diagnostic]:
    """Yield each path placeholder no declared path parameter fills, then each path parameter no placeholder names."""
    named = f" of {label(operation)}" if policy.placeholder_label else ""
    for placeholder in dict.fromkeys(placeholders):
        if placeholder not in declared:
            message = f"The path placeholder {placeholder!r}{named} has no path parameter"
            yield policy.problem(policy.path_code, message, operation.id.use_site)
    template = path_placeholders(operation.path)
    for declaration in operation.parameters:
        if fact(declaration, "in") == "path" and declaration.name not in template:
            message = f"The path parameter {declaration.name!r} of {label(operation)} names no path placeholder"
            yield policy.problem(policy.path_code, message, declaration.use_site)
