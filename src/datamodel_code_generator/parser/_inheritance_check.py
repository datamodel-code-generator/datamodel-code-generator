"""How the parser checks the inheritance of the models it writes: the cycles and the orders Python refuses.

``check_model_inheritance`` rejects, with an ``Error``, the builtin classes Python would refuse to create: an
inheritance cycle, a local base written after its subclass, and bases whose method resolution orders conflict, which
it reads through the H14 core (``_mro.py:c3_merge``, rule ``python``). It takes the parser through
``InheritanceCheckHost``, which ``Parser`` satisfies structurally, and ``Parser._check_model_inheritance`` stays the
hook the pipeline calls and subclasses override.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Final

from typing_extensions import Protocol

from datamodel_code_generator import Error, cached_path_exists
from datamodel_code_generator._mro import c3_merge
from datamodel_code_generator._url_redaction import redact_reference

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from datamodel_code_generator.model.base import BaseClassDataType, DataModel
    from datamodel_code_generator.parser._passes._state import ModuleContextView

__all__ = ["InheritanceCheckHost", "check_model_inheritance"]

# Keep these as module-name checks so non-pydantic-v2 outputs do not import the
# pydantic_v2 generator package and its runtime feature gates.
_PYDANTIC_V2_BASE_MODEL_MODULE: Final = "datamodel_code_generator.model.pydantic_v2.base_model"


class InheritanceCheckHost(Protocol):
    """The options and run state of the parser that the inheritance check reads."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model class of the output backend."""

    @property
    def custom_template_dir(self) -> Path | None:
        """The directory of the custom templates (``--custom-template-dir``)."""

    @property
    def _import_overrides(self) -> dict[str, str] | None:
        """The imports that ``--import-overrides`` replaces."""


def check_model_inheritance(parser: InheritanceCheckHost, contexts: Sequence[ModuleContextView]) -> None:  # noqa: PLR0912, PLR0914, PLR0915
    """Reject invalid builtin inheritance; an existing topological order needs no DFS."""
    backend = parser.data_model_type.__module__
    ordinary_backends = {
        _PYDANTIC_V2_BASE_MODEL_MODULE,
        "datamodel_code_generator.model.pydantic_v2.dataclass",
        "datamodel_code_generator.model.dataclass",
    }
    typed_dict = backend == "datamodel_code_generator.model.typed_dict"
    struct = backend == "datamodel_code_generator.model.msgspec"
    if backend not in ordinary_backends and not typed_dict and not struct:
        return
    if (
        typed_dict
        and (custom_template_dir := parser.custom_template_dir) is not None
        and cached_path_exists(custom_template_dir / "TypedDictClass.jinja2")
    ):
        return

    def builtin(model: DataModel) -> bool:
        return type(model) is parser.data_model_type and not (
            parser.custom_template_dir is not None and model._uses_custom_root_template  # noqa: SLF001
        )

    def emitted_bases(model: DataModel) -> list[BaseClassDataType]:
        if not builtin(model) or (typed_dict and getattr(model, "is_functional_syntax", False)):
            return []
        return model.base_classes

    if not any(base.reference for ctx in contexts for model in ctx.models for base in emitted_bases(model)):
        return

    models = [model for ctx in contexts for model in ctx.models]
    positions = [(ctx_index, index) for ctx_index, ctx in enumerate(contexts) for index in range(len(ctx.models))]
    identities = {id(model): index for index, model in enumerate(models)}
    for index, model in enumerate(models):
        references = [base.reference for base in emitted_bases(model) if base.reference]
        if len(references) > 1 or (references and identities.get(id(references[0].source), index) >= index):
            break
    else:
        if struct:
            for ctx in contexts:
                parser.data_model_type.render_module_code(ctx.models)
        return

    paths: dict[str, list[DataModel]] = defaultdict(list)
    for model in models:
        paths[model.path].append(model)
    parents: list[list[int]] = [[] for _ in models]
    closed = [backend in ordinary_backends and builtin(model) for model in models]
    overrides = parser._import_overrides or {}  # noqa: SLF001
    ordered = True
    for index, model in enumerate(models):
        ctx_index, _ = positions[identities[id(model)]]
        ctx = contexts[ctx_index]
        for base in emitted_bases(model):
            reference = base.reference
            if reference is None:
                terminal = base.import_
                closed[index] = closed[index] and (
                    backend == _PYDANTIC_V2_BASE_MODEL_MODULE
                    and terminal is not None
                    and terminal.from_ == "pydantic"
                    and terminal.import_ == "BaseModel"
                    and base.type_hint == (terminal.alias or terminal.import_)
                )
                continue
            candidates = paths.get(reference.path, [])
            if len(candidates) != 1 or reference.source is not candidates[0]:
                closed[index] = False
                continue
            parent = candidates[0]
            parent_index = identities[id(parent)]
            parent_ctx_index, _ = positions[parent_index]
            imported = ctx.imports.reference_paths.get(reference.path)
            binding = (
                parent.class_name
                if ctx_index == parent_ctx_index
                else (imported.alias or imported.import_)
                if imported is not None and imported.import_ not in overrides
                else None
            )
            if base.type_hint != binding:
                closed[index] = False
                continue
            parents[index].append(parent_index)
            ordered = ordered and parent_index < index

    if struct:
        for ctx in contexts:
            parser.data_model_type.render_module_code(ctx.models)

    def label(index: int) -> str:
        model = models[index]
        return f"{model.class_name} ({redact_reference(model.path)})"

    postorder = list(range(len(models))) if ordered else []
    if not ordered:
        completed: set[int] = set()
        for start in range(len(models)):
            if start in completed:
                continue
            active = {start: 0}
            stack = [(start, iter(parents[start]))]
            while stack:
                index, remaining = stack[-1]
                if (parent := next(remaining, None)) is None:
                    stack.pop()
                    active.pop(index)
                    completed.add(index)
                    postorder.append(index)
                elif parent in active:
                    cycle = [item for item, _ in stack[active[parent] :]]
                    cycle.append(parent)
                    msg = "Generated model inheritance cycle: " + " -> ".join(map(label, cycle))
                    raise Error(msg)
                elif parent not in completed:
                    active[parent] = len(stack)
                    stack.append((parent, iter(parents[parent])))

        for index, direct in enumerate(parents):
            ctx_index, position = positions[identities[id(models[index])]]
            for parent in direct:
                parent_ctx_index, parent_position = positions[parent]
                if ctx_index == parent_ctx_index and parent_position >= position:
                    msg = f"Generated model {label(index)} requires local base {label(parent)} before its definition."
                    raise Error(msg)

    for index in postorder:
        closed[index] = closed[index] and all(closed[parent] for parent in parents[index])
    joins = [index for index in postorder if closed[index] and len(parents[index]) > 1]
    if not joins:
        return
    join_set = set(joins)
    uses: Counter[int] = Counter()
    for index in joins:
        for direct_parent in parents[index]:
            parent = direct_parent
            while parent not in join_set and parents[parent]:
                parent = parents[parent][0]
            if parent in join_set:
                uses[parent] += 1
    linearizations: dict[int, list[int]] = {}
    for index in joins:
        sequences: list[list[int]] = []
        consumed: list[int] = []
        for direct_parent in parents[index]:
            parent = direct_parent
            sequence: list[int] = []
            while parent not in join_set:
                sequence.append(parent)
                if not parents[parent]:
                    break
                parent = parents[parent][0]
            if parent in join_set:
                sequence.extend(linearizations[parent])
                consumed.append(parent)
            sequences.append(sequence)
        sequences.append(parents[index])
        if (merged := c3_merge([sequence.copy() for sequence in sequences], str, rule="python")) is None:
            msg = (
                f"Generated model {label(index)} has inconsistent method resolution order for generated bases "
                + ", ".join(map(label, parents[index]))
                + "."
            )
            raise Error(msg)
        if uses[index]:
            linearizations[index] = [index, *merged]
        for parent in consumed:
            uses[parent] -= 1
            if not uses[parent]:
                del linearizations[parent]
