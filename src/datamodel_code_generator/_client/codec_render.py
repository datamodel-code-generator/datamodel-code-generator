"""Render the codecs of one client package's model backend, with thin field maps for a stdlib backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client._compiled_templates import model_bindings as model_bindings_template
from datamodel_code_generator._client.codec_plan import (
    CODEC_RUNTIMES,
    EXTRAS,
    EXTRAS_MODULE,
    Choice,
    Class,
    Fixed,
    Items,
    Values,
)
from datamodel_code_generator._client.facade import statement
from datamodel_code_generator._target_contract import OperationId
from datamodel_code_generator._target_module import TargetModule
from datamodel_code_generator._target_render import call, display
from datamodel_code_generator._target_templates import templated

if TYPE_CHECKING:
    from datamodel_code_generator._client.codec_plan import ClientCodecs, Shape
    from datamodel_code_generator._target_contract import TypeUseId
    from datamodel_code_generator._target_module import TypeNames
    from datamodel_code_generator._target_templates import Templated

_NAMES: Final = (
    "Class",
    "Items",
    "Fixed",
    "Values",
    "Choice",
    "Field",
    "Model",
    "MODELS",
    EXTRAS,
    *(runtime.codec for runtime in CODEC_RUNTIMES.values()),
)


@dataclass(frozen=True, slots=True)
class UseAccessors:
    """The generated names that serve one planned use."""

    use: TypeUseId
    codec: str


@dataclass(frozen=True, slots=True)
class RenderedBindings:
    """The `_generated/model_bindings.py` module and the accessors of every planned use."""

    source: Templated
    uses: tuple[UseAccessors, ...]


@dataclass(frozen=True, slots=True)
class ModelEntry:
    """The stdlib field map of one model: the model's type and its `Model` record."""

    type: str
    value: str


@dataclass(frozen=True, slots=True)
class Codec:
    """The codec of one planned use: its name, its type, its constructor, and the docstring naming the use."""

    name: str
    annotation: str
    value: str
    description: str


def _description(use: TypeUseId) -> str:
    owner = use.owner.use_site.pointer if isinstance(use.owner, OperationId) else use.owner.pointer
    selectors = "".join(f" {part}" for part in (use.location, use.name, use.status, use.media) if part)
    return f"Codec of {owner} {use.role} ({use.direction}{selectors}).".replace("\\", "\\\\").replace('"', '\\"')


class _Renderer:
    def __init__(self, plan: ClientCodecs, types: TypeNames) -> None:
        self.plan = plan
        self.module = TargetModule(types, (*_NAMES, *(f"codec_{index}" for index in range(len(plan.uses)))), level=1)
        self.records: set[str] = set()

    def call(self, name: str, *values: str, **keywords: str) -> str:
        self.records.add(name)
        return call(name, (*values, *(f"{key}={item}" for key, item in keywords.items())))

    def shape(self, shape: Shape) -> str:
        if isinstance(shape, Class):
            return self.module.hint(shape.type, static=False)
        if isinstance(shape, Items):
            return self.call("Items", self.shape(shape.item), *((repr(shape.kind),) if shape.kind != "list" else ()))
        if isinstance(shape, Fixed):
            return self.call("Fixed", display(self.shape(item) for item in shape.items))
        if isinstance(shape, Values):
            return self.call("Values", self.shape(shape.value))
        if isinstance(shape, Choice):
            kinds = display(f"({kind!r}, {self.shape(item)})" for kind, item in shape.kinds)
            tags = display(f"({tag!r}, {self.module.symbol(symbol)})" for tag, symbol in shape.tags)
            return self.call("Choice", kinds, *((repr(shape.tag), tags) if shape.tag is not None else ()))
        return "None"

    def render(self) -> RenderedBindings:
        runtime = self.plan.runtime
        models: list[ModelEntry] = []
        if runtime.mapped and self.plan.uses:
            for model in self.plan.models:
                name = self.module.symbol(model.symbol)
                fields = display(
                    self.call(
                        "Field",
                        repr(field.wire),
                        repr(field.name),
                        *((self.shape(field.shape),) if field.shape is not None else ()),
                        **({"omit_none": "True"} if field.omit_none else {}),
                    )
                    for field in model.fields
                )
                models.append(ModelEntry(name, self.call("Model", fields)))
        annotation = f"{self.module.name('typing', 'Final')}[dict[type, Model]]" if models else ""
        registry = "MODELS" if models else "{}"
        accessors = []
        entries = []
        for index, use in enumerate(self.plan.uses):
            codec = runtime.codec
            static = self.module.hint(use.type)
            arguments = (
                (self.shape(use.shape), registry) if runtime.mapped else (self.module.hint(use.type, static=False),)
            )
            if use.extras:
                arguments = (*arguments, EXTRAS)
            final = self.module.name("typing", "Final")
            entries.append(
                Codec(f"codec_{index}", f"{final}[{codec}[{static}]]", call(codec, arguments), _description(use.use))
            )
            accessors.append(UseAccessors(use.use, f"codec_{index}"))
        names = sorted({runtime.codec, *self.records})
        imports = [statement(f".._runtime.model_codecs.{runtime.module}", names)] if self.plan.uses else []
        if self.plan.extras:
            imports.append(statement(f".._runtime.model_codecs.{EXTRAS_MODULE}", [EXTRAS]))
        source = templated(
            "model_bindings.jinja2",
            model_bindings_template.render,
            imports=self.module.imports(),
            codec_imports=imports,
            models_annotation=annotation,
            models=models,
            codecs=entries,
        )
        return RenderedBindings(source, tuple(accessors))


def render_model_bindings(plan: ClientCodecs, types: TypeNames) -> RenderedBindings:
    """Render one native codec per selected use and the stdlib models its conversions reach."""
    return _Renderer(plan, types).render()
