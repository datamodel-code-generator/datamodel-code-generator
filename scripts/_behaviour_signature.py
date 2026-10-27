"""Behaviour signature of a generated module or package, for scripts/behaviour_diff.py.

Usage:
  python -I scripts/_behaviour_signature.py <model.py | package dir> <output model type> [payloads.json]

Prints one JSON object: for each public name its kind, bases, config, fields (canonical type, constraints, required,
aliases, default), enum members, TypedDict read-only keys and positional constructor order; the import path of every
re-exported name in packages; whether pydantic (TypeAdapter) or msgspec (Decoder) can build a validator for each
name. With a payload file, also the verdict (accept / reject / error:<Type>) of every payload and a fingerprint of
the parsed value of each accepted one.

Types are canonical: string annotations are parsed and resolved in the module namespace, module-level aliases are
transparent, NotRequired / Required / ReadOnly are dropped (the required flag carries them) and unions are sets.
Run it in a fresh process per tree: it imports the generated modules.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import dataclasses
import enum
import hashlib
import importlib
import importlib.util
import inspect
import json
import sys
import types
import typing
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

PYDANTIC_CONFIG_KEYS = (
    "extra",
    "frozen",
    "populate_by_name",
    "validate_by_name",
    "validate_by_alias",
    "serialize_by_alias",
    "use_enum_values",
    "arbitrary_types_allowed",
    "strict",
    "regex_engine",
    "coerce_numbers_to_str",
    "alias_generator",
)
MSGSPEC_CONFIG_KEYS = ("array_like", "tag", "tag_field", "rename", "forbid_unknown_fields", "omit_defaults", "frozen")
DOC_ONLY_KEYS = frozenset({
    "title",
    "description",
    "examples",
    "example",
    "extra_json_schema",
    "json_schema_extra",
    "deprecated",
})
WRAPPERS = frozenset({"NotRequired", "Required", "ReadOnly", "ClassVar", "Final", "InitVar"})
TYPING_ALIASES = {
    "List": "list",
    "Dict": "dict",
    "Set": "set",
    "FrozenSet": "frozenset",
    "Tuple": "tuple",
    "Type": "type",
}
ADAPTED_KINDS = frozenset({"alias", "BaseModel", "RootModel", "pydantic.dataclass", "dataclass", "TypedDict"})
PAYLOAD_CLASS_NAME = "Payload"
MAX_DEPTH = 25


class Context:
    """The namespace annotations of one generated module resolve in."""

    def __init__(self, module: types.ModuleType) -> None:
        """Capture the module namespace and the root package name."""
        self.namespace = dict(vars(module))
        self.root = module.__name__.split(".")[0]

    def own(self, obj: object) -> bool:
        """Return whether an object is defined in the generated module or package."""
        module = getattr(obj, "__module__", "") or ""
        return module == self.root or module.startswith(f"{self.root}.")


def meta_text(meta: object) -> str | None:
    """Return constraint metadata independent of how it was spelled; None for doc-only metadata."""
    kind = type(meta)
    if kind.__name__ == "FieldInfo":
        parts = [text for text in map(meta_text, getattr(meta, "metadata", ())) if text]
        if discriminator := getattr(meta, "discriminator", None):
            parts.append(f"discriminator={discriminator!r}")
        return ",".join(parts) or None
    if kind.__name__ == "Meta" and kind.__module__.startswith("msgspec"):
        values = {key: getattr(meta, key) for key in dir(meta) if not key.startswith("_") and key not in DOC_ONLY_KEYS}
        values = {key: value for key, value in values.items() if value is not None and not callable(value)}
        return "Meta(" + ",".join(f"{k}={v!r}" for k, v in sorted(values.items())) + ")" if values else None
    if dataclasses.is_dataclass(meta) and not isinstance(meta, type):
        values = {field.name: getattr(meta, field.name) for field in dataclasses.fields(meta)}
        values = {key: value for key, value in values.items() if value is not None and key not in DOC_ONLY_KEYS}
        return f"{kind.__name__}(" + ",".join(f"{k}={v!r}" for k, v in sorted(values.items())) + ")" if values else None
    text = repr(meta)
    return None if " at 0x" in text else text


def split_top_level(text: str) -> list[str]:
    """Split a comma-separated type list at bracket depth zero."""
    parts: list[str] = []
    depth = 0
    current = ""
    for char in text:
        depth += (char == "[") - (char == "]")
        if char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        depth += (char == "[") - (char == "]")
        if depth < 0:
            return False
    return depth == 0


def union_text(parts: list[str]) -> str:
    """Return a flattened, ordered union of canonical type texts; a single member stands for itself."""
    flat: set[str] = set()
    for part in parts:
        if part.startswith("Union[") and part.endswith("]") and _balanced(part[6:-1]):
            flat.update(split_top_level(part[6:-1]))
        else:
            flat.add(part)
    return next(iter(flat)) if len(flat) == 1 else "Union[" + ", ".join(sorted(flat)) + "]"


def _annotated(inner: str, metas: list[str | None], *, meta: bool) -> str:
    texts = [text for text in metas if text]
    return f"Annotated[{inner}, {', '.join(texts)}]" if meta and texts else inner


def canonical(tp: object, ctx: Context, *, meta: bool, depth: int = 0, seen: frozenset[int] = frozenset()) -> str:  # ruff: ignore[too-many-return-statements]
    """Return the canonical text of a type; aliases are transparent, classes keep their name."""
    if depth > MAX_DEPTH:
        return "..."
    if tp is None or tp is type(None):
        return "None"
    if isinstance(tp, str):
        try:
            node = ast.parse(tp.strip(), mode="eval").body
        except SyntaxError:
            return tp
        return canonical_node(node, ctx, meta=meta, depth=depth + 1, seen=seen)
    if isinstance(tp, typing.ForwardRef):
        return canonical(tp.__forward_arg__, ctx, meta=meta, depth=depth + 1, seen=seen)
    if type(tp).__name__ == "TypeAliasType":
        if id(tp) in seen:
            return getattr(tp, "__name__", "...")
        return canonical(getattr(tp, "__value__", None), ctx, meta=meta, depth=depth + 1, seen=seen | {id(tp)})
    if isinstance(tp, typing.TypeVar):
        return f"~{tp.__name__}"
    origin, args = typing.get_origin(tp), typing.get_args(tp)

    def sub(arg: object) -> str:
        return canonical(arg, ctx, meta=meta, depth=depth + 1, seen=seen)

    if origin is typing.Annotated:
        return _annotated(sub(args[0]), [meta_text(arg) for arg in args[1:]], meta=meta)
    if origin is not None and getattr(origin, "_name", None) in WRAPPERS:
        return sub(args[0])
    if origin is typing.Union or isinstance(tp, types.UnionType):
        return union_text([sub(arg) for arg in args])
    if origin is typing.Literal:
        return "Literal[" + ", ".join(sorted(map(repr, args))) + "]"
    if origin is not None:
        return sub(origin) + (f"[{', '.join(map(sub, args))}]" if args else "")
    if isinstance(tp, type):
        if ctx.own(tp) or tp.__module__ in {"builtins", "typing"}:
            return tp.__qualname__
        return f"{tp.__module__.split('.')[0]}.{tp.__qualname__}"
    if tp is Ellipsis:
        return "..."
    return repr(tp).replace("typing_extensions.", "").replace("typing.", "")


def _resolve(node: ast.expr, ctx: Context) -> object:
    if isinstance(node, ast.Name):
        for space in (ctx.namespace, vars(builtins), vars(typing)):
            if node.id in space:
                return space[node.id]
        raise NameError(node.id)
    if isinstance(node, ast.Attribute):
        return getattr(_resolve(node.value, ctx), node.attr)
    raise TypeError(type(node).__name__)


def _evaluate(node: ast.expr, ctx: Context) -> object:
    return eval(compile(ast.Expression(node), "<annotation>", "eval"), dict(ctx.namespace))


def _value_text(node: ast.expr, ctx: Context) -> str:
    try:
        return repr(_evaluate(node, ctx))
    except Exception:  # ruff: ignore[blind-except] - an unresolvable value keeps its source text
        return ast.unparse(node)


def _meta_node_text(node: ast.expr, ctx: Context) -> str | None:
    try:
        return meta_text(_evaluate(node, ctx))
    except Exception:  # ruff: ignore[blind-except] - unresolvable metadata keeps its source text
        return ast.unparse(node)


def canonical_node(node: ast.expr, ctx: Context, *, meta: bool, depth: int, seen: frozenset[int]) -> str:  # ruff: ignore[too-many-return-statements]
    """Return the canonical text of a string annotation's expression, resolved in the module namespace."""

    def sub(child: ast.expr) -> str:
        return canonical_node(child, ctx, meta=meta, depth=depth + 1, seen=seen)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return canonical(node.value, ctx, meta=meta, depth=depth + 1, seen=seen)
        return "None" if node.value is None else repr(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return union_text([sub(node.left), sub(node.right)])
    if isinstance(node, ast.List):
        return "[" + ", ".join(map(sub, node.elts)) + "]"
    if not isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        return ast.unparse(node)
    try:
        resolved = _resolve(node.value if isinstance(node, ast.Subscript) else node, ctx)
    except Exception:  # ruff: ignore[blind-except] - a name the module does not define keeps its source text
        return ast.unparse(node)
    if not isinstance(node, ast.Subscript):
        return canonical(resolved, ctx, meta=meta, depth=depth + 1, seen=seen)
    elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
    name = getattr(resolved, "_name", None) or getattr(resolved, "__name__", "")
    if name in WRAPPERS:
        return sub(elements[0])
    if name == "Annotated":
        return _annotated(sub(elements[0]), [_meta_node_text(e, ctx) for e in elements[1:]], meta=meta)
    if name in {"Union", "Optional"}:
        return union_text([*map(sub, elements), *(["None"] if name == "Optional" else [])])
    if name == "Literal":
        return "Literal[" + ", ".join(sorted(_value_text(e, ctx) for e in elements)) + "]"
    head = canonical(resolved, ctx, meta=meta, depth=depth + 1, seen=seen)
    return TYPING_ALIASES.get(head, head) + "[" + ", ".join(map(sub, elements)) + "]"


def _default_text(value: object, factory: object = None) -> str:
    if factory is not None:
        return "factory:" + getattr(factory, "__qualname__", type(factory).__name__)
    text = repr(value)
    return "<obj>" if " at 0x" in text else text


def _field(  # ruff: ignore[too-many-arguments]
    ctx: Context,
    annotation: object,
    *,
    required: bool,
    alias: str | None = None,
    validation_alias: str | None = None,
    serialization_alias: str | None = None,
    default: str | None = None,
    metadata: tuple[object, ...] = (),
) -> dict[str, Any]:
    constrained = canonical(annotation, ctx, meta=True)
    if extra := [text for text in map(meta_text, metadata) if text]:
        constrained = f"Annotated[{constrained}, {', '.join(extra)}]"
    return {
        "type": canonical(annotation, ctx, meta=False),
        "ctype": constrained,
        "required": required,
        "alias": alias,
        "validation_alias": validation_alias,
        "serialization_alias": serialization_alias,
        "default": default,
    }


def _positional(cls: type) -> list[str] | None:
    try:
        parameters = inspect.signature(cls).parameters.values()
    except (TypeError, ValueError):
        return None
    return [p.name for p in parameters if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]


def _bases(cls: type) -> list[str]:
    names = []
    for base in cls.__bases__:
        if base is object:
            continue
        generic = getattr(base, "__pydantic_generic_metadata__", None)
        origin = generic.get("origin") if generic else None
        names.append((origin or base).__qualname__.split("[", 1)[0])
    return names


def _annotations(cls: type) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        merged.update(getattr(klass, "__annotations__", {}) or {})
    return merged


def _pydantic_item(cls: type, ctx: Context) -> dict[str, Any] | None:
    try:
        import pydantic  # ruff: ignore[import-outside-top-level]
        from pydantic_core import PydanticUndefined  # ruff: ignore[import-outside-top-level]
    except ImportError:
        return None
    if not issubclass(cls, pydantic.BaseModel):
        return None
    with contextlib.suppress(Exception):
        cls.model_rebuild(raise_errors=False, _types_namespace=ctx.namespace)
    config = {
        key: repr(cls.model_config.get(key)) for key in PYDANTIC_CONFIG_KEYS if cls.model_config.get(key) is not None
    }
    fields = cls.model_fields
    if issubclass(cls, pydantic.RootModel):
        info = fields.get("root")
        return {
            "kind": "RootModel",
            "bases": _bases(cls),
            "config": config,
            "root": canonical(info.annotation, ctx, meta=False) if info else None,
            "croot": _field(ctx, info.annotation, required=True, metadata=tuple(info.metadata))["ctype"]
            if info
            else None,
            "default": None
            if info is None or info.is_required()
            else _default_text(info.default, info.default_factory),
        }
    out = {}
    for name, info in fields.items():
        required = info.is_required()
        default = None if info.default is PydanticUndefined else info.default
        out[name] = _field(
            ctx,
            info.annotation,
            required=required,
            alias=info.alias,
            validation_alias=None if info.validation_alias is None else repr(info.validation_alias),
            serialization_alias=info.serialization_alias,
            default=None if required else _default_text(default, info.default_factory),
            metadata=(*info.metadata, *([f"discriminator={info.discriminator!r}"] if info.discriminator else [])),
        )
    return {"kind": "BaseModel", "bases": _bases(cls), "config": config, "fields": out}


def _enum_item(cls: type[enum.Enum]) -> dict[str, Any]:
    flags = (enum.IntEnum, getattr(enum, "StrEnum", None), enum.IntFlag, enum.Flag)
    base = next((b.__name__ for b in cls.__mro__[1:] if b in flags), None) or next(
        (b.__name__ for b in cls.__mro__[1:] if b in {str, int, float}), None
    )
    return {
        "kind": "Enum",
        "bases": _bases(cls),
        "enum_base": base,
        "members": {name: repr(member.value) for name, member in cls.__members__.items()},
    }


def _no_extra_items() -> object:
    try:
        import typing_extensions  # ruff: ignore[import-outside-top-level]
    except ImportError:
        return object()
    return getattr(typing_extensions, "NoExtraItems", object())


def _is_typeddict(obj: type) -> bool:
    """Return whether a class is a TypedDict of typing or typing_extensions (typing's check misses the latter)."""
    try:
        import typing_extensions  # ruff: ignore[import-outside-top-level]
    except ImportError:
        return typing.is_typeddict(obj)
    return typing_extensions.is_typeddict(obj)


QUALIFIERS = frozenset({"NotRequired", "Required", "ReadOnly", "Annotated"})


def qualifiers(annotation: object) -> set[str]:
    """Return the NotRequired / Required / ReadOnly wrappers of a TypedDict annotation, also of a string one.

    The class's required and read-only keys miss wrappers that `from __future__ import annotations` left as strings.
    """
    found: set[str] = set()
    if isinstance(annotation, typing.ForwardRef):
        annotation = annotation.__forward_arg__
    if isinstance(annotation, str):
        try:
            node: ast.expr | None = ast.parse(annotation.strip(), mode="eval").body
        except SyntaxError:
            return found
        while isinstance(node, ast.Subscript):
            if (name := _dotted_name(node.value).rpartition(".")[2]) not in QUALIFIERS:
                break
            found.add(name)
            node = node.slice.elts[0] if isinstance(node.slice, ast.Tuple) else node.slice
        return found
    while (name := getattr(typing.get_origin(annotation), "_name", None) or "") in QUALIFIERS or (
        typing.get_origin(annotation) is typing.Annotated
    ):
        found.add(name or "Annotated")
        annotation = typing.get_args(annotation)[0]
    return found


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def _typed_dict_item(cls: type, ctx: Context) -> dict[str, Any]:
    required_keys = getattr(cls, "__required_keys__", frozenset())
    annotations = _annotations(cls)
    wrappers = {name: qualifiers(tp) for name, tp in annotations.items()}
    required = {
        name
        for name, found in wrappers.items()
        if "Required" in found or ("NotRequired" not in found and name in required_keys)
    }
    readonly = set(getattr(cls, "__readonly_keys__", frozenset())) | {n for n, f in wrappers.items() if "ReadOnly" in f}
    config = {}
    for key in ("__closed__", "__extra_items__"):
        value = getattr(cls, key, None)
        if value is not None and value is not _no_extra_items():
            config[key.strip("_")] = canonical(value, ctx, meta=True) if key == "__extra_items__" else repr(value)
    return {
        "kind": "TypedDict",
        "bases": [b.__qualname__ for b in getattr(cls, "__orig_bases__", ()) if isinstance(b, type)],
        "config": config,
        "readonly": sorted(readonly),
        "fields": {name: _field(ctx, tp, required=name in required) for name, tp in annotations.items()},
    }


def _struct_item(cls: type, ctx: Context) -> dict[str, Any]:
    annotations = _annotations(cls)
    names = cls.__struct_fields__  # ty: ignore[unresolved-attribute]
    defaults = cls.__struct_defaults__  # ty: ignore[unresolved-attribute]
    required_count = len(names) - len(defaults)
    encoded = getattr(cls, "__struct_encode_fields__", names)
    struct_config = getattr(cls, "__struct_config__", None)
    config = {
        key: repr(getattr(struct_config, key))
        for key in MSGSPEC_CONFIG_KEYS
        if getattr(struct_config, key, None) not in {None, False}
    }
    fields = {}
    for index, name in enumerate(names):
        default = None
        if index >= required_count:
            value = defaults[index - required_count]
            default = _default_text(
                value, getattr(value, "factory", None) if type(value).__name__ == "Factory" else None
            )
        fields[name] = _field(
            ctx,
            annotations.get(name),
            required=index < required_count,
            alias=encoded[index] if encoded[index] != name else None,
            default=default,
        )
    return {
        "kind": "msgspec.Struct",
        "bases": _bases(cls),
        "config": config,
        "fields": fields,
        "positional": _positional(cls),
    }


def _dataclass_item(cls: type, ctx: Context) -> dict[str, Any]:
    annotations = _annotations(cls)
    fields = {}
    for field in dataclasses.fields(cls):
        factory = None if field.default_factory is dataclasses.MISSING else field.default_factory
        has_default = field.default is not dataclasses.MISSING or factory is not None
        fields[field.name] = _field(
            ctx,
            annotations.get(field.name, field.type),
            required=not has_default,
            default=_default_text(field.default, factory) if has_default else None,
        )
    kind = "pydantic.dataclass" if hasattr(cls, "__pydantic_validator__") else "dataclass"
    return {"kind": kind, "bases": _bases(cls), "fields": fields, "positional": _positional(cls)}


def item_signature(name: str, obj: object, ctx: Context) -> dict[str, Any]:  # ruff: ignore[too-many-return-statements]
    """Return the behaviour signature of one public name."""
    if isinstance(obj, type):
        if obj.__name__ != name or not ctx.own(obj):
            return {"kind": "alias", "type": canonical(obj, ctx, meta=True)}
        if (pydantic_item := _pydantic_item(obj, ctx)) is not None:
            return pydantic_item
        if issubclass(obj, enum.Enum):
            return _enum_item(obj)
        if _is_typeddict(obj):
            return _typed_dict_item(obj, ctx)
        if hasattr(obj, "__struct_fields__"):
            return _struct_item(obj, ctx)
        if dataclasses.is_dataclass(obj):
            return _dataclass_item(obj, ctx)
        return {"kind": "class", "bases": _bases(obj)}
    if (
        type(obj).__name__ == "TypeAliasType"
        or typing.get_origin(obj) is not None
        or isinstance(obj, (typing.TypeVar, typing.ForwardRef, str, types.UnionType))
        or obj is None
    ):
        return {"kind": "alias", "type": canonical(obj, ctx, meta=True)}
    if callable(obj):
        return {"kind": "function"}
    return {"kind": "other", "type": type(obj).__name__}


def defined_names(tree: ast.Module) -> list[str]:
    """Return the public names a module defines at top level, in source order."""
    names: dict[str, None] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names[node.name] = None
        elif isinstance(node, ast.Assign):
            names.update(dict.fromkeys(target.id for target in node.targets if isinstance(target, ast.Name)))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = None
        elif type(node).__name__ == "TypeAlias":
            names[node.name.id] = None  # ty: ignore[unresolved-attribute]
    return [name for name in names if not name.startswith("_") and name != "model_config"]


def _short_error(exc: BaseException, limit: int = 80) -> str:
    text = str(exc).splitlines()[0][:limit] if str(exc) else ""
    return f"{type(exc).__name__}: {text}"


def _usability(module: types.ModuleType, items: dict[str, Any], output_model_type: str) -> tuple[str, dict[str, str]]:
    """Return whether a validator can be built for each public name (msgspec Decoder or pydantic TypeAdapter)."""
    results: dict[str, str] = {}
    if output_model_type == "msgspec.Struct":
        import msgspec  # ruff: ignore[import-outside-top-level]

        for name, item in items.items():
            obj = module.__dict__[name]
            if item["kind"] not in {"msgspec.Struct", "alias"} or (
                isinstance(obj, type) and not issubclass(obj, msgspec.Struct)
            ):
                continue
            try:
                msgspec.json.Decoder(obj)
            except Exception as exc:  # ruff: ignore[blind-except] - the failure is the result
                results[name] = _short_error(exc)
            else:
                results[name] = "ok"
        return "decoders", results
    import pydantic  # ruff: ignore[import-outside-top-level]

    for name, item in items.items():
        if item["kind"] not in ADAPTED_KINDS:
            continue
        try:
            pydantic.TypeAdapter(module.__dict__[name])
        except Exception as exc:  # ruff: ignore[blind-except] - the failure is the result
            results[name] = _short_error(exc)
        else:
            results[name] = "ok"
    return "adapters", results


def module_signature(module: types.ModuleType, source: str, output_model_type: str) -> dict[str, Any]:
    """Return the signature of one imported module."""
    ctx = Context(module)
    items: dict[str, Any] = {}
    for name in defined_names(ast.parse(source)):
        if name not in module.__dict__:
            continue
        try:
            items[name] = item_signature(name, module.__dict__[name], ctx)
        except Exception as exc:  # ruff: ignore[blind-except] - an introspection failure makes the name unusable
            items[name] = {"kind": "error", "error": _short_error(exc, 120)}
    signature: dict[str, Any] = {"names": sorted(items), "items": items}
    exports = {}
    for name, obj in vars(module).items():
        if name.startswith("_") or name in items:
            continue
        if isinstance(obj, types.ModuleType):
            if obj.__name__ == ctx.root or obj.__name__.startswith(f"{ctx.root}."):
                exports[name] = "<module " + obj.__name__.partition(".")[2] + ">"
            continue
        defined_in = getattr(obj, "__module__", None)
        if isinstance(defined_in, str) and ctx.own(obj) and defined_in != module.__name__:
            exports[name] = defined_in.partition(".")[2] or "__init__"
    if exports:
        signature["exports"] = exports
    key, results = _usability(module, items, output_model_type)
    signature[key] = results
    return signature


def fingerprint(value: object) -> object:  # ruff: ignore[too-many-return-statements]
    """Reduce a parsed payload to (kind, class name, value) per node, so `None` and `X(root=None)` differ."""
    if isinstance(value, enum.Enum):
        return ["enum", type(value).__name__, repr(value.value)]
    if isinstance(value, dict):
        return ["dict", sorted((json.dumps(fingerprint(k)), fingerprint(v)) for k, v in value.items())]
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [fingerprint(v) for v in value]]
    if isinstance(value, (set, frozenset)):
        return [type(value).__name__, sorted(json.dumps(fingerprint(v)) for v in value)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [
            "object",
            type(value).__name__,
            {f.name: fingerprint(getattr(value, f.name)) for f in dataclasses.fields(value)},
        ]
    if (fields := getattr(value, "__struct_fields__", None)) is not None:
        return ["object", type(value).__name__, {name: fingerprint(getattr(value, name)) for name in fields}]
    if hasattr(value, "__pydantic_fields__") and not isinstance(value, type):
        names = type(value).__pydantic_fields__  # ty: ignore[unresolved-attribute]
        return ["object", type(value).__name__, {name: fingerprint(getattr(value, name, None)) for name in names}]
    return [type(value).__name__, repr(value)]


def _payload_type(module: types.ModuleType) -> object:
    if PAYLOAD_CLASS_NAME in module.__dict__:
        return module.__dict__[PAYLOAD_CLASS_NAME]
    generated = [v for v in module.__dict__.values() if isinstance(v, type) and v.__module__ == module.__name__]
    if len(generated) == 1:
        return generated[0]
    msg = f"{len(generated)} generated types instead of {PAYLOAD_CLASS_NAME}"
    raise LookupError(msg)


def payload_verdicts(
    module: types.ModuleType, payloads: list[dict[str, Any]], output_model_type: str
) -> dict[str, Any]:
    """Validate each payload: msgspec.convert for msgspec Structs, a pydantic TypeAdapter for the other types."""
    try:
        validate, rejection = _validator(_payload_type(module), output_model_type)
    except Exception as exc:  # ruff: ignore[blind-except] - no validator means every payload errors
        return {"verdicts": dict.fromkeys((p["id"] for p in payloads), "error:lookup"), "lookup": _short_error(exc)}
    verdicts: dict[str, str] = {}
    values: dict[str, str] = {}
    for payload in payloads:
        verdict, parsed = _verdict(validate, rejection, payload["payload"])
        verdicts[payload["id"]] = verdict
        if verdict == "accept":
            digest = hashlib.sha256(json.dumps(fingerprint(parsed), sort_keys=True).encode()).hexdigest()
            values[payload["id"]] = digest[:16]
    return {"verdicts": verdicts, "values": values}


def _validator(
    target: object, output_model_type: str
) -> tuple[Callable[[object], object], tuple[type[Exception], ...]]:
    if output_model_type == "msgspec.Struct":
        import msgspec  # ruff: ignore[import-outside-top-level]

        def convert(payload: object) -> object:
            return msgspec.convert(payload, type=target)

        return convert, (msgspec.ValidationError,)
    import pydantic  # ruff: ignore[import-outside-top-level]

    return pydantic.TypeAdapter(target).validate_python, (pydantic.ValidationError,)


def _verdict(
    validate: Callable[[object], object], rejection: tuple[type[Exception], ...], payload: object
) -> tuple[str, object]:
    try:
        return "accept", validate(payload)
    except rejection:
        return "reject", None
    except Exception as exc:  # ruff: ignore[blind-except] - another exception is a verdict of its own
        return f"error:{type(exc).__name__}", None


def _import_file(path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("model", path)
    if spec is None or spec.loader is None:
        msg = f"cannot import {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules["model"] = module
    spec.loader.exec_module(module)
    return module


def signature_of(path: Path, output_model_type: str, payloads: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return the signature of a generated module file or package directory."""
    warnings.simplefilter("ignore")
    path = path.resolve()
    if not path.is_dir():
        try:
            module = _import_file(path)
        except Exception as exc:  # ruff: ignore[blind-except] - a failed import is the result
            return {"import_error": _short_error(exc, 200)}
        signature = module_signature(module, path.read_text(encoding="utf-8"), output_model_type)
        if payloads is not None:
            signature["payloads"] = payload_verdicts(module, payloads, output_model_type)
        return signature
    sys.path.insert(0, str(path.parent))
    package = {}
    for file in sorted(path.rglob("*.py")):
        if "__pycache__" in file.parts:
            continue
        relative = file.relative_to(path).with_suffix("")
        name = ".".join([path.name, *(part for part in relative.parts if part != "__init__")])
        key = ".".join(relative.parts)
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # ruff: ignore[blind-except] - a failed import is the result
            package[key] = {"import_error": _short_error(exc, 200)}
            continue
        package[key] = module_signature(module, file.read_text(encoding="utf-8"), output_model_type)
    return {"package": package}


def main(argv: list[str]) -> int:
    """Print the signature of the tree named on the command line."""
    sys.dont_write_bytecode = True
    sys.set_int_max_str_digits(0)
    payloads = json.loads(Path(argv[2]).read_text(encoding="utf-8")) if len(argv) > 2 else None  # ruff: ignore[magic-value-comparison]
    print(json.dumps(signature_of(Path(argv[0]), argv[1], payloads)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
