"""Render acquired native codec types and thin stdlib field maps for one client package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client._compiled_templates import model_bindings as model_bindings_template
from datamodel_code_generator._client.codec_plan import Choice, Class, Fixed, Items, Values
from datamodel_code_generator._client.facade import statement
from datamodel_code_generator._target_contract import OperationId
from datamodel_code_generator._target_module import TargetModule
from datamodel_code_generator._target_render import call, display
from datamodel_code_generator._target_templates import templated

if TYPE_CHECKING:
    from datamodel_code_generator._client.codec_plan import ClientCodecs, CodecKind, Shape
    from datamodel_code_generator._target_contract import TypeUseId
    from datamodel_code_generator._target_module import TypeNames
    from datamodel_code_generator._target_templates import Templated

_CODECS: Final[dict[CodecKind, str]] = {
    "pydantic": "PydanticCodec",
    "pydantic_dataclass": "PydanticDataclassCodec",
    "msgspec": "MsgspecCodec",
    "stdlib": "StdlibCodec",
}
_NAMES: Final = (
    "Class",
    "Items",
    "Fixed",
    "Values",
    "Choice",
    "Field",
    "Model",
    "MODELS",
    *_CODECS.values(),
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
        self.stdlib: set[str] = set()

    def call(self, name: str, *values: str, **keywords: str) -> str:
        self.stdlib.add(name)
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
        codecs = sorted({_CODECS[use.kind] for use in self.plan.uses if use.kind != "stdlib"})
        models: list[ModelEntry] = []
        annotation = ""
        if any(use.kind == "stdlib" for use in self.plan.uses):
            self.stdlib.add("StdlibCodec")
            self.stdlib.add("Model")
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
                models.append(ModelEntry(name, self.call("Model", fields, *(("True",) if model.keyed else ()))))
            annotation = f"{self.module.name('typing', 'Final')}[dict[type, Model]]"
        accessors = []
        entries = []
        for index, use in enumerate(self.plan.uses):
            codec = _CODECS[use.kind]
            static = self.module.hint(use.type)
            arguments = (
                (self.shape(use.shape), "MODELS")
                if use.kind == "stdlib"
                else (self.module.hint(use.type, static=False),)
            )
            final = self.module.name("typing", "Final")
            entries.append(
                Codec(f"codec_{index}", f"{final}[{codec}[{static}]]", call(codec, arguments), _description(use.use))
            )
            accessors.append(UseAccessors(use.use, f"codec_{index}"))
        imports = [
            statement(f".._runtime.model_codecs.{module}", names)
            for module, names in (("native", codecs), ("stdlib", sorted(self.stdlib)))
            if names
        ]
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
