"""User class keys: which model or field an option keyed by a class name, ``Class.field`` or a field name names.

Every option the user keys by a class name resolves its keys through the functions here, at the pass its row names
and against the names models have at that pass (keys most specific first):

* ``--model-name-map``: schema path, ``#fragment``, generated class name, original name; at L1 ``ModelResolver.add``
  under the name a model is created under, and at L2 ``settle_yielding_names`` under the final name of a yielding
  model. A generation run translates the class-name keys first (``translate_class_keys``): they name the models of
  the run without them, which they rename by schema path, and the names those models had there stay theirs.
* ``--base-class-map``: class name (``mapped_base_class``); at L1 model construction (``Parser._resolve_base_class``),
  under row ``predicted`` (the root models and the OpenAPI parameter and GraphQL models) or ``reference-name`` (the
  nullable enum root, the root models of a union's variants and the read-only/write-only models).
* ``--extra-template-data``: schema path, then class name (``template_data_keys``, row ``reference-name``); at L1
  model construction (``DataModel.__init__``); a GraphQL scalar reads its ``py_type`` under row ``original-name``.
* ``--validators``: class name, as the template data of that name; at L1 model construction
  (``model/output.py:prepare_output_model_config``).
* ``--aliases``: ``Class.field``, then ``field`` (``field_keys``, ``first_given``); at L1 field naming, under the name
  a model is created under or its original name, and again under the allocated name
  (``Parser._apply_final_class_field_aliases``).
* ``--serialization-aliases``: ``Class.original``, ``Class.name``, ``original``, ``name`` (``field_keys``); at L1
  field naming.
* ``--default-value-overrides``: ``Class.field``, then ``field`` (``field_keys``); at L1 field defaults and inherited
  defaults in the derived class.
* ``--enum-field-as-literal-map``: field name only, no class part; at L1 enum parsing.
* ``--type-overrides``: class name (``model_level_type_override``) or ``Class.field`` (``class_field_key``); at L3
  reuse (``type_override_classes``), and at L5 ``remove_overridden_models``, ``apply_field_type_overrides`` and
  ``apply_base_class_type_overrides`` under the name a model has at L5; a model-level key names a model by the row
  of ``model_type_override``.

Which of a model's names the class part of a key is compared with is the row of ``class_key`` that the place looking
the model up names. The field-keyed options read row ``predicted`` while allOf, the OpenAPI parameters, GraphQL and
Avro name fields before the class name is allocated, ``reference-name`` once it is (``parse_object``, the parameter
defaults and serialization aliases, the forward schemas of allOf, ``Parser._apply_final_class_field_aliases``),
``original-name`` for the explicit aliases of a field that recorded no class (``prepare_explicit_field_aliases``) and
``class-name`` after construction (the aliases of a reused tag field, whose serialization alias reads
``reference-name``, the tag field names and the Avro default overrides).

Rows that keep today's output where the options disagree (each one waits for the option that resolves every key once,
after names are final):

* ``legacy-reparse-for-final-names`` (``ConstructionKeys``): the L1 rows read the name a model is created under, but a
  yielding name settles at L2. When a yielding name changed and a key of an L1 row names the created or the final
  name, ``__init__.py:_parse_generation`` parses the whole input again with the final names assigned. Its key list
  leaves out ``--type-overrides`` and ``--enum-field-as-literal-map``.
* ``legacy-reparse-yielding-only`` (``parser/_passes/settle.py:settle_yielding_names``): the renames that decide that
  parse are those of the yielding names at L2; the renames after L2, which ``GenerationStore.rename_model`` records,
  decide nothing, so a key that names a name one of them gave matches no model.
* ``type-overrides-first-dot``: the class part of a ``--type-overrides`` key is what precedes its first dot
  (``type_override_classes``), while a ``Class.field`` key of the other options names a class at each of its dots
  (``ConstructionKeys``, the lookups); ``scoped_key`` splits every key, by these rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from collections.abc import Set as AbstractSet

__all__ = [
    "ClassKeyRow",
    "ClassScope",
    "ConstructionKeys",
    "ModelNameKeys",
    "ModelNameRecord",
    "ModelNames",
    "ScopedKeyRow",
    "TypeOverrideRow",
    "class_field_key",
    "class_key",
    "class_name_keys",
    "class_scope",
    "field_keys",
    "first_given",
    "first_present",
    "mapped_base_class",
    "model_level_type_override",
    "model_name_path_keys",
    "model_type_override",
    "scoped_key",
    "template_data_keys",
    "translate_class_keys",
    "type_override_classes",
]

_ValueT = TypeVar("_ValueT")

_SCOPE_SEPARATOR: Final = "."
_PATH_KEY_MARKER: Final = "#"
ScopedKeyRow: TypeAlias = Literal["first-dot", "every-dot"]
ClassKeyRow: TypeAlias = Literal["predicted", "reference-name", "original-name", "class-name"]
TypeOverrideRow: TypeAlias = Literal["class-name", "predicted-class-name"]
_TYPE_OVERRIDE_CLASS_KEYS: Final[dict[TypeOverrideRow, ClassKeyRow]] = {
    "class-name": "class-name",
    "predicted-class-name": "predicted",
}


class ModelNames(Protocol):
    """The names of a model's reference (``reference.Reference``) that a class key reads."""

    path: str
    name: str
    original_name: str

    @property
    def short_name(self) -> str:
        """The class name: the last part of the dotted ``name``."""


def class_key(row: ClassKeyRow, model: ModelNames | str) -> str:
    """Return the name of a model that the class part of the keys of a class-keyed option is compared with, by row.

    Row ``predicted`` is the name a parser has for a model before the resolver allocates its class name: the name it
    adds the model under (the GraphQL type name, before its affixes), the allOf name before ``--use-title-as-name``,
    or a class name ``ModelResolver.get_class_name`` gives without allocating it; it is passed as ``model`` itself.
    The other rows read the reference ``model``: ``reference-name`` its name (the one it was created under while L1
    builds it), ``original-name`` the name it was added under, and ``class-name`` its class name without the module
    (``DataModel.class_name``). The rows differ where the resolver numbered, mapped or affixed a name, where a module
    prefixes it, and after a model was renamed.
    """
    if isinstance(model, str):
        return model
    match row:
        case "original-name":
            return model.original_name
        case "class-name":
            return model.short_name
    return model.name


@dataclass(frozen=True, slots=True)
class ClassScope:
    """The names of one model that the class part of a key is compared with, in order.

    ``name`` is the name of the row the lookup reads (``class_key``); ``output_names`` are the names the model has in
    the output that a key names where they are none of its L1 names, compared after it.
    """

    name: str
    output_names: tuple[str, ...] = ()

    @property
    def keys(self) -> tuple[str, ...]:
        """The names a class part is compared with: ``name``, then ``output_names``."""
        return (self.name, *self.output_names)


def class_scope(class_name: str | None, output_names: tuple[str, ...] = ()) -> ClassScope | None:
    """Return the scope of the class ``class_name`` (none for no or an empty name) with its ``output_names``."""
    return ClassScope(class_name, output_names) if class_name else None


def class_field_key(class_name: str, field_name: str | None) -> str:
    """Return the key of the field ``field_name`` of the class ``class_name`` (``Class.field``)."""
    return f"{class_name}{_SCOPE_SEPARATOR}{field_name}"


def field_keys(scope: ClassScope | None, *field_names: str) -> tuple[str, ...]:
    """Return the keys of a field option, most specific first: each name under each class name, then each name alone."""
    if scope is None:
        return field_names
    return (
        *(class_field_key(class_name, field_name) for class_name in scope.keys for field_name in field_names),
        *field_names,
    )


def scoped_key(key: str, *, row: ScopedKeyRow) -> tuple[tuple[str, str], ...]:
    """Return the (class part, field part) pairs that a ``Class.field`` key splits into at the dots the row reads.

    ``class_field_key`` joins names that may hold a dot, so a key names one pair at each of its dots. Row ``every-dot``
    gives them all, none for a key without a dot (``model_level_type_override``); the class names the L1 rows key
    (``ConstructionKeys``), the ``--aliases`` keys of a class after name allocation
    (``Parser._apply_final_class_field_aliases``) and those of a wire name in the targets
    (``_target_naming.py:TargetNames.aliased``) read it too. Row ``first-dot`` (``type_override_classes``) gives the
    pair at the first dot, ``(key, "")`` for a key without one.
    """
    if row == "every-dot":
        return tuple(
            (key[:index], key[index + 1 :]) for index, character in enumerate(key) if character == _SCOPE_SEPARATOR
        )
    class_part, _, field_part = key.partition(_SCOPE_SEPARATOR)
    return ((class_part, field_part),)


def first_present(mapping: Mapping[str, _ValueT], keys: Iterable[str]) -> tuple[_ValueT | None, bool]:
    """Return the value of the first of ``keys`` that ``mapping`` holds and True, or None and False."""
    for key in keys:
        if key in mapping:
            return mapping[key], True
    return None, False


def first_given(mapping: Mapping[str, _ValueT], keys: Iterable[str]) -> _ValueT | None:
    """Return the value of the first of ``keys`` that ``mapping`` gives a string or a non-empty value."""
    return next((value for key in keys if isinstance(value := mapping.get(key), str) or value), None)


def mapped_base_class(base_class_map: Mapping[str, _ValueT] | None, scope: ClassScope) -> tuple[_ValueT | None, bool]:
    """Return the ``--base-class-map`` value of the first name of ``scope`` it maps and True, or None and False."""
    return first_present(base_class_map or {}, scope.keys)


def template_data_keys(reference: ModelNames) -> tuple[str, str]:
    """Return the keys of the ``--extra-template-data`` entries of a model: its schema path, then its name.

    The name is row ``reference-name`` of ``class_key``; ``--validators`` entries join the template data of their key.
    """
    return reference.path, class_key("reference-name", reference)


def model_level_type_override(key: str) -> bool:
    """Return whether a ``--type-overrides`` key names a model (no class part) rather than one field of a class."""
    return not scoped_key(key, row="every-dot")


def model_type_override(
    overrides: Mapping[str, _ValueT], model: ModelNames | str, *, row: TypeOverrideRow
) -> _ValueT | None:
    """Return the value of the model-level ``--type-overrides`` key that names a model, or None, by row.

    Row ``class-name`` compares the keys with the class name of the model, without the module a dotted name gives
    it: the model L5 ``remove_overridden_models`` drops, the references and bases the override replaces, the site
    models built from it and the base layouts read one name. Row ``predicted-class-name`` compares them with the class
    name a parser gives before the resolver allocates it, passed as ``model`` (the Avro fixed types).
    """
    return overrides.get(class_key(_TYPE_OVERRIDE_CLASS_KEYS[row], model))


def type_override_classes(type_overrides: Iterable[str]) -> frozenset[str]:
    """Return the class names ``--type-overrides`` keys name, by the part before the first dot (row ``first-dot``)."""
    return frozenset(class_name for key in type_overrides for class_name, _ in scoped_key(key, row="first-dot"))


@dataclass(frozen=True, slots=True)
class ConstructionKeys:
    """The class names the L1 rows key, for row ``legacy-reparse-for-final-names``."""

    class_names: frozenset[str] = frozenset()
    """The keys of ``--base-class-map``, ``--extra-template-data`` and ``--validators``."""
    field_scopes: frozenset[str] = frozenset()
    """The class parts of the ``--aliases``, ``--serialization-aliases`` and ``--default-value-overrides`` keys, at
    each of their dots, as the lookups join a class name and a field name that may hold a dot."""

    @classmethod
    def of(
        cls,
        class_keyed: Iterable[Iterable[str] | None],
        field_keyed: Iterable[Iterable[str] | None],
    ) -> ConstructionKeys:
        """Collect the class names that the class-keyed and the field-keyed options of L1 name."""
        return cls(
            frozenset(key for keys in class_keyed for key in keys or ()),
            frozenset(
                class_name
                for keys in field_keyed
                for key in keys or ()
                for class_name, _ in scoped_key(key, row="every-dot")
            ),
        )

    def name_any(self, names: AbstractSet[str]) -> bool:
        """Return whether a key names one of ``names``, so the models of these names are built again."""
        return not names.isdisjoint(self.class_names) or not names.isdisjoint(self.field_scopes)


def class_name_keys(model_name_map: Mapping[str, str] | None) -> dict[str, str]:
    """Return the ``--model-name-map`` entries keyed by a class name (generated or original), not by a schema path.

    Every schema path holds a ``#`` (``ModelResolver.join_path``), so a key without one names a model by a name.
    """
    return {key: value for key, value in (model_name_map or {}).items() if _PATH_KEY_MARKER not in key}


@dataclass(slots=True)
class ModelNameRecord:
    """What a run without the class-name keys of ``--model-name-map`` named, by schema path, for the translation."""

    allocated: list[tuple[str, str, str]] = field(default_factory=list)
    """The path, generated name and original name of each L1 allocation a class-name key is matched at, in order."""
    at_settle: dict[str, str] = field(default_factory=dict)
    """The name of every reference at the L2 settle."""
    settled: dict[str, str] = field(default_factory=dict)
    """The names the yielding references (typed map values) settled under."""
    final: dict[str, str] = field(default_factory=dict)
    """The name of every generated model at the end of the run."""


@dataclass(frozen=True, slots=True, eq=False)
class ModelNameKeys:
    """The ``--model-name-map`` a run applies, with the user's key behind each of its keys and the reserved names.

    It compares and hashes by identity, as the run context that carries it does by its fields.
    """

    model_name_map: Mapping[str, str]
    """The path keys, the translated keys (by schema path) and the keys left as written."""
    applied_keys: Mapping[str, str]
    """The key of ``model_name_map`` that each key the user wrote applies as."""
    reserved: Mapping[str, frozenset[str]]
    """The names keyed models held in the run without the class-name keys, with the paths that may take each."""

    def unmatched(self, found: AbstractSet[str]) -> set[str]:
        """Return the keys the user wrote whose key in ``model_name_map`` was not ``found``."""
        return {key for key, applied in self.applied_keys.items() if applied not in found}


_NAME_MATCH: Final = 0
_ORIGINAL_MATCH: Final = 1
_SETTLE_MATCH: Final = 2
_FINAL_MATCH: Final = 3


def model_name_path_keys(path: str) -> tuple[str, ...]:
    """Return the ``--model-name-map`` keys that name the model at schema ``path``: the path, then its ``#fragment``."""
    _, separator, fragment = path.partition(_PATH_KEY_MARKER)
    return (path, f"{_PATH_KEY_MARKER}{fragment}") if separator else (path,)


def _class_key_target(key: str, record: ModelNameRecord, eligible: Mapping[str, None]) -> tuple[str, int] | None:
    """Return the path of the model a class-name key names in the run without such keys, with how it names it.

    The first allocation whose generated or original name is the key wins, as at allocation today; a typed map value's
    settled name stays a key of the settle; then the name a model had at the settle (a late primary-first rename), then
    its final name (a rename after L1). Only the models of ``eligible`` are named so: a path key names the others.
    """
    for path, name, original_name in record.allocated:
        if path in eligible and key in {name, original_name}:
            return path, _NAME_MATCH if key == name else _ORIGINAL_MATCH
    if key in record.settled.values():
        return None
    for kind, names in ((_SETTLE_MATCH, record.at_settle), (_FINAL_MATCH, record.final)):
        if (path := next((path for path, name in names.items() if name == key and path in eligible), None)) is not None:
            return path, kind
    return None


def _holders(names: Mapping[str, str]) -> dict[str, frozenset[str]]:
    """Return the paths that hold each name of ``names`` (path to name)."""
    holders: dict[str, set[str]] = {}
    for path, name in names.items():
        holders.setdefault(name, set()).add(path)
    return {name: frozenset(paths) for name, paths in holders.items()}


def translate_class_keys(model_name_map: Mapping[str, str], record: ModelNameRecord) -> ModelNameKeys:
    """Translate the class-name keys of ``--model-name-map`` to the models they name in the run without them.

    Keys are names of the output without the map: each class-name key names the model that ``_class_key_target``
    finds in that run (``record``) and applies to it by its schema path; two keys of one model leave the one matched by
    the generated name, as the candidate order of today's matching does. A key that names no model stays as written.
    Every name a keyed model held in that run, other than a value of the map, is reserved for the models that end the
    run with it (none when no model does), so no other model takes the name the user saw on the keyed model.
    """
    eligible = dict.fromkeys(
        path
        for path, _, _ in record.allocated
        if not any(path_key in model_name_map for path_key in model_name_path_keys(path))
    )
    targets: dict[str, tuple[int, str]] = {}
    for key in class_name_keys(model_name_map):
        if (target := _class_key_target(key, record, eligible)) is not None and (
            (path := target[0]) not in targets or target[1] < targets[path][0]
        ):
            targets[path] = target[1], key
    keyed = {key: path for path, (_, key) in targets.items()}
    applied = {key: keyed.get(key, key) for key in model_name_map}
    translated = {applied_key: model_name_map[key] for key, applied_key in applied.items()}
    holders = {**_holders(record.at_settle), **_holders(record.final)}
    values = set(model_name_map.values())
    held = {
        name
        for path in targets
        for name in (
            *(name for allocated_path, name, _ in record.allocated if allocated_path == path),
            record.at_settle.get(path),
            record.final.get(path),
        )
        if name is not None and name not in values
    }
    return ModelNameKeys(translated, applied, {name: holders.get(name, frozenset()) for name in held})
