"""The naming pass (L2): the names of the models once every model has claimed one.

``name_models`` runs right after ``settle_yielding_names``, when the L1 resolver and the settle have named every model,
and records the naming plan (``parser/_naming.py``) that L3 reads for the name a model was numbered from
(``structure.delete_duplicate_models``, ``structure.replace_duplicate_name_in_module``). Under a naming rule it reads
the claims L1 recorded (``NameClaim``) and gives the planned names through ``GenerationStore.rename_model`` (scheme
``plan``). The parser comes in through ``NamingHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, cast

from typing_extensions import Protocol

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator.parser._naming import (
    Composed,
    NameClaim,
    NamingPlan,
    Ordinal,
    build_record_of,
    composed_name,
    plan_names,
    rw_variant_name,
)
from datamodel_code_generator.reference import NAME_REQUEST_KEY, NameRequest, location_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from datamodel_code_generator._user_keys import ConstructionKeys, ModelNameRecord
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._naming import ClaimRole, ComposedRole, NamingRules
    from datamodel_code_generator.parser._passes._state import SettleRun
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import ModelResolver

__all__ = ["NamingHost", "name_models"]


class NamingHost(Protocol):
    """What the naming pass reads and writes on the parser."""

    _naming_plan: NamingPlan
    planned_names_to_assign: tuple[tuple[str, tuple[str, str | None]], ...]
    yielding_names_to_assign: tuple[tuple[str, tuple[str, str | None]], ...]

    @property
    def results(self) -> list[DataModel]:
        """The models the parse built."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of references and class names."""

    @property
    def _naming_rules(self) -> NamingRules:
        """The naming rules of the run."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the passes change the graph of the generation store."""

    @property
    def _construction_keys(self) -> ConstructionKeys:
        """The class names the options read while models are built key."""


def _claimed_references(results: Sequence[DataModel]) -> list[Any]:
    """Return the references of the models, then those of the bases their names compose from that are no model.

    A read or write variant composes its name from its base model's, which the output leaves out where it writes the
    variants alone, so the base's claim is read too.
    """
    references: list[Any] = [model.reference for model in results]
    seen = {model_key_of(reference) for reference in references}
    pending = list(references)
    while pending:
        reference = pending.pop(0)
        if (
            (record := build_record_of(reference)) is not None
            and isinstance(claim := record.claim_for(reference), Composed)
            and (key := model_key_of(claim.base)) not in seen
        ):
            seen.add(key)
            references.append(claim.base)
            pending.append(claim.base)
    return references


def _claims(references: Sequence[Any], resolver: ModelResolver) -> list[NameClaim]:
    """Return the claim of each of ``references`` (``_claimed_references``) L1 recorded frames for, in their order.

    A model built from an entry of a definitions container is its definition unless another model's builder built it,
    as it builds the parts of an allOf at the location of the definition that holds the allOf.
    """
    claims: list[NameClaim] = []
    documents: dict[str | None, int] = {}
    for reference in references:
        if (record := build_record_of(reference)) is None:
            continue
        owner = record.owner
        role: ClaimRole = "owned" if owner is not None else "definition" if record.definition else "root"
        composed_from, word, position = None, "", 0
        match record.claim_for(reference):
            case Composed() as claim:
                role, composed_from, word = "composed", model_key_of(claim.base), claim.role
            case Ordinal() as claim:
                role, position = "ordinal", claim.position
        location = location_of(reference)
        request: NameRequest | None = reference.__dict__.get(NAME_REQUEST_KEY)
        module, dot, cls = reference.name.rpartition(".")
        claims.append(
            NameClaim(
                key=model_key_of(reference),
                path=reference.path,
                role=role,
                owner=None if owner is None else model_key_of(owner),
                composed_from=composed_from,
                word=word,
                position=position,
                document=documents.setdefault(None if location is None else location.document, len(documents)),
                order=len(claims),
                module=f"{module}{dot}",
                cls=cls,
                base=(reference.duplicate_name or "").rpartition(".")[2] or cls,
                kind="model" if request is None else request.model_type,
                keyed=resolver._named_by_model_name_map(reference.path),  # noqa: SLF001
            )
        )
    return claims


class _ResolverNamer:
    """The names the references of a parse hold, and how its resolver names a model (``parser/_naming.py:Namer``)."""

    __slots__ = ("_held", "_requests", "_resolver")

    def __init__(self, resolver: ModelResolver, references: Sequence[Any]) -> None:
        self._resolver = resolver
        self._held = Counter(reference.name for reference in resolver.references.values())
        self._requests: dict[Any, NameRequest] = {
            model_key_of(reference): reference.__dict__.get(NAME_REQUEST_KEY) or NameRequest(reference.original_name)
            for reference in references
        }

    def held(self, name: str) -> int:
        return self._held[name]

    def reserved(self, module: str, cls: str, path: str) -> bool:
        resolver = self._resolver
        return (
            cls in resolver.exclude_names
            or cls in resolver._released_model_names  # noqa: SLF001
            or (bool(resolver._reserved_names) and resolver.reserved_for_another(f"{module}{cls}", path))  # noqa: SLF001
        )

    def suffix(self, kind: str) -> str | None:
        return self._resolver._duplicate_name_suffix(kind)  # noqa: SLF001

    def composed(self, claim: NameClaim, base: str) -> tuple[str, str]:
        resolver = self._resolver
        if claim.word in {"Request", "Response"}:
            name = resolver.get_class_name(
                rw_variant_name(base, claim.word, resolver.class_name_suffix),
                unique=False,
                skip_affix=True,
                preserve_name=True,
            ).name
        else:
            request = self._requests[claim.key]
            name = resolver.get_class_name(
                composed_name(base, cast("ComposedRole", claim.word)),
                unique=False,
                singular_name=request.singular_name,
                singular_name_suffix=request.singular_name_suffix,
                model_type=request.model_type,
                preserve_name=request.preserve_class_name,
            ).name
        module, dot, cls = name.rpartition(".")
        return f"{module}{dot}", cls


def name_models(parser: NamingHost, _run: SettleRun[Any]) -> None:
    """Plan the names of the models after their last claim (``plan_names``) and give them, for L3 to read.

    With no naming rule on, the plan names no model: each keeps the name the L1 resolver and ``settle_yielding_names``
    gave it. A planned name renames the model (scheme ``plan``); where a key of the options read while models are
    built names a renamed model, by the name it had or the one it takes, the parse is done again with the name of every
    reference assigned at its creation (``planned_names_to_assign``), which settles the yielding names too. A run that
    records what it names for the class-name keys of ``--model-name-map`` records the planned names as the names its
    models are given and had at the settle, so the keys name the models of the output (``_record_planned_names``).
    """
    rules = parser._naming_rules  # noqa: SLF001
    resolver = parser.model_resolver
    if rules.legacy:
        parser._naming_plan = NamingPlan(rules)  # noqa: SLF001
        return
    results = parser.results
    references = _claimed_references(results)
    parser._naming_plan = plan = plan_names(  # noqa: SLF001
        rules, _claims(references, resolver), _ResolverNamer(resolver, references)
    )
    if not plan.names:
        return
    renamed: set[str] = set()
    for model in results:
        if (planned := plan.names.get(model_key_of(model.reference))) is not None and planned.cls != model.class_name:
            renamed.update((model.reference.name, model.class_name))
            parser._graph_builder.rename_model(model, class_name=planned.cls, scheme="plan")  # noqa: SLF001
            renamed.update((model.reference.name, model.class_name))
    resolver.refresh_reference_names()
    if (record := resolver.model_name_record) is not None:
        _record_planned_names(record, plan, resolver)
    if parser._construction_keys.name_any(renamed):  # noqa: SLF001
        parser.yielding_names_to_assign = ()
        parser.planned_names_to_assign = tuple(
            (path, _assigned_name(plan, reference))
            for path, reference in resolver.references.items()
            if not resolver._named_by_model_name_map(path)  # noqa: SLF001
        )


def _record_planned_names(record: ModelNameRecord, plan: NamingPlan, resolver: ModelResolver) -> None:
    """Record the planned name of each reference the plan names as the name it was given and had at the settle."""
    names = {
        path: f"{planned.module}{planned.cls}"
        for path, reference in resolver.references.items()
        if (planned := plan.names.get(model_key_of(reference))) is not None
    }
    record.allocated[:] = [
        (path, names.get(path, name), original_name) for path, name, original_name in record.allocated
    ]
    record.at_settle.update((path, name) for path, name in names.items() if path in record.at_settle)


def _assigned_name(plan: NamingPlan, reference: Any) -> tuple[str, str | None]:
    """Return the name and duplicate name a reference is assigned in the parse again: those it ends with.

    A reference the plan names ends with its planned name, numbered from its planned base, also a base of read or
    write variants that no model writes.
    """
    if (planned := plan.names.get(model_key_of(reference))) is None:
        return reference.name, reference.duplicate_name
    return f"{planned.module}{planned.cls}", None if planned.base == planned.cls else planned.base
