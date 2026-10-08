"""Render acquired native codec types and thin stdlib field maps for one client package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._client.codec_plan import Choice, Class, Fixed, Items, Values
from datamodel_code_generator._codec_type_source import Namespace, TypeSource
from datamodel_code_generator._python_layout import Group, layout
from datamodel_code_generator._target_contract import GeneratedSymbolType, OperationId

if TYPE_CHECKING:
    from datamodel_code_generator._client.codec_plan import ClientCodecs, CodecKind, Shape
    from datamodel_code_generator._python_layout import Doc
    from datamodel_code_generator._target_contract import TypeUseId

_WIDTH: Final = 120
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
    "Final",
    *_CODECS.values(),
)


@dataclass(frozen=True, slots=True)
class UseAccessors:
    """The generated names that serve one planned use."""

    use: TypeUseId
    codec: str


@dataclass(frozen=True, slots=True)
class RenderedBindings:
    """The source of `_generated/model_bindings.py` and the accessors of every planned use."""

    source: str
    uses: tuple[UseAccessors, ...]


def _description(use: TypeUseId) -> str:
    owner = use.owner.use_site.pointer if isinstance(use.owner, OperationId) else use.owner.pointer
    selectors = "".join(f" {part}" for part in (use.location, use.name, use.status, use.media) if part)
    return f"Codec of {owner} {use.role} ({use.direction}{selectors}).".replace("\\", "\\\\").replace('"', '\\"')


class _Renderer:
    def __init__(self, plan: ClientCodecs) -> None:
        self.plan = plan
        self.namespace = Namespace((*_NAMES, *(f"codec_{index}" for index in range(len(plan.uses)))))
        self.source = TypeSource(self.namespace, dict(plan.imports))
        self.stdlib: set[str] = set()

    def call(self, name: str, *values: Doc, **keywords: Doc) -> Group:
        self.stdlib.add(name)
        return Group(
            f"{name}(", (*(("", item) for item in values), *((f"{key}=", item) for key, item in keywords.items())), ")"
        )

    def shape(self, shape: Shape) -> Doc:
        if isinstance(shape, Class):
            return self.source.runtime(shape.type)
        if isinstance(shape, Items):
            return self.call("Items", self.shape(shape.item), *((repr(shape.kind),) if shape.kind != "list" else ()))
        if isinstance(shape, Fixed):
            return self.call("Fixed", Group("(", tuple(("", self.shape(item)) for item in shape.items), ")", ","))
        if isinstance(shape, Values):
            return self.call("Values", self.shape(shape.value))
        if isinstance(shape, Choice):
            kinds = Group(
                "(",
                tuple(("", Group("(", (("", repr(kind)), ("", self.shape(item))), ")")) for kind, item in shape.kinds),
                ")",
                ",",
            )
            tags = Group(
                "(",
                tuple(
                    ("", Group("(", (("", repr(tag)), ("", self.source.runtime(GeneratedSymbolType(symbol)))), ")"))
                    for tag, symbol in shape.tags
                ),
                ")",
                ",",
            )
            return self.call("Choice", kinds, *((repr(shape.tag), tags) if shape.tag is not None else ()))
        return "None"

    def render(self) -> RenderedBindings:
        sections = []
        codecs = sorted({_CODECS[use.kind] for use in self.plan.uses if use.kind != "stdlib"})
        stdlib = any(use.kind == "stdlib" for use in self.plan.uses)
        if stdlib:
            self.stdlib.add("StdlibCodec")
            self.stdlib.add("Model")
            entries = []
            for model in self.plan.models:
                name = self.source.runtime(GeneratedSymbolType(model.symbol))
                fields = Group(
                    "(",
                    tuple(
                        (
                            "",
                            self.call(
                                "Field",
                                repr(field.wire),
                                repr(field.name),
                                *((self.shape(field.shape),) if field.shape is not None else ()),
                                **({"omit_none": "True"} if field.omit_none else {}),
                            ),
                        )
                        for field in model.fields
                    ),
                    ")",
                    ",",
                )
                entries.append((f"{name}: ", self.call("Model", fields, *(("True",) if model.keyed else ()))))
            prefix = "MODELS: Final[dict[type, Model]] = "
            sections.append(prefix + layout(Group("{", tuple(entries), "}"), 0, len(prefix), _WIDTH))
        accessors = []
        for index, use in enumerate(self.plan.uses):
            codec = _CODECS[use.kind]
            static = self.source.static(use.type)
            arguments: tuple[tuple[str, Doc], ...] = (
                (("", self.shape(use.shape)), ("", "MODELS"))
                if use.kind == "stdlib"
                else (("", self.source.runtime(use.type)),)
            )
            call = Group(f"{codec}(", arguments, ")")
            prefix = f"codec_{index}: Final[{codec}[{static}]] = "
            sections.append(prefix + layout(call, 0, len(prefix), _WIDTH) + f'\n"""{_description(use.use)}"""')
            accessors.append(UseAccessors(use.use, f"codec_{index}"))
        imports = []
        for module, names in (("native", codecs), ("stdlib", sorted(self.stdlib))):
            if names:
                imports.append(f"from .._runtime.model_codecs.{module} import {', '.join(names)}")
        header = [
            '"""Native model codecs of this generated package; regenerate them instead of editing."""',
            "",
            "from __future__ import annotations",
            "",
            *(("from typing import Final",) if self.plan.uses else ()),
            "",
            *self.namespace.imports(),
            "",
            *imports,
        ]
        return RenderedBindings("\n".join(header) + "\n\n\n" + "\n\n".join(sections) + "\n", tuple(accessors))


def render_model_bindings(plan: ClientCodecs) -> RenderedBindings:
    """Render one native codec per selected use and the stdlib models its conversions reach."""
    return _Renderer(plan).render()


def render_model_codecs() -> str:
    """Expose the JSON value type and the sentinel for an omitted argument."""
    return '''"""JSON values and omitted arguments used by the generated client."""

from ._runtime.model_codecs.media import JSONValue
from ._runtime.model_codecs.unset import UNSET, Unset

__all__ = ["JSONValue", "UNSET", "Unset"]
'''
