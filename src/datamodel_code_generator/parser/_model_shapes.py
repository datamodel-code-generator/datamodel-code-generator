"""The keys that deduplication and reuse compare models by, and the rows that keep where today's users disagree.

:class:`ModelShapes` computes every key from what an adapter reads off the models (:class:`ShapeSource`): the
rendered dedup key, the masked rendering, the references in render order and the union tags taken before any model is
merged. The rendering writes nothing to the models: before a model renders for a key, the explicit step
``model/base.py:DataModel._prepare_dedup_key`` decides the sequence interface of a root model and writes the
nullability that rendering the types of its fields decides. The candidate key of msgspec layout flattening
(:func:`reuse_candidate_key`) and the stdout fingerprint (:func:`stdout_fingerprint`) are computed here too.

The users disagree today on whether union tags count, which root models they compare and how they tell apart the
bases of a merged model's children. The rows named ``legacy-*`` (:data:`SAME_NAME`, :data:`COLLAPSE_REUSE`,
:data:`TREE_SCOPE`, :data:`REUSE_MODEL`) keep that output; keys over model facts (G06 for #4834, G07 for #4816) replace
them. Every user but collapse-reuse compares the keys the parse's memo kept for a model in an earlier pass
(:data:`STALE_DEDUP_KEYS`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from operator import is_
from typing import TYPE_CHECKING, Generic, Literal, TypeAlias, final

from typing_extensions import Protocol, TypeVar

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    import re
    from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "COLLAPSE_REUSE",
    "REUSE_MODEL",
    "SAME_NAME",
    "STALE_DEDUP_KEYS",
    "TREE_SCOPE",
    "BaseDedup",
    "KeyCache",
    "KeySlot",
    "ModelShapes",
    "RootModels",
    "ShapeRow",
    "ShapeSource",
    "reuse_candidate_key",
    "skips_root_model",
    "stdout_fingerprint",
]

Key: TypeAlias = "tuple[Hashable, ...]"
RootModels: TypeAlias = Literal["redirected", "skipped", "compared", "skipped-when-collapsed"]
"""Which root models a user compares.

* ``redirected``: a root model that only names the model of its own name is removed for that model; others compare.
* ``skipped``: no root model is compared.
* ``compared``: every root model is compared.
* ``skipped-when-collapsed``: root models are compared unless ``--collapse-root-models`` inlines them.
"""
KeySlot: TypeAlias = "tuple[str | None, bool]"
"""Where the parse's memo keeps a dedup key of a model: the class name it renders under, or None and whether ``M``
stands in for it."""
KeyCache: TypeAlias = Literal["legacy-stale-dedup-keys", "rendered"]
"""Whether a user compares the dedup keys the parse's memo kept for a model in an earlier pass or renders them anew."""
STALE_DEDUP_KEYS: KeyCache = "legacy-stale-dedup-keys"
"""Compare the dedup key the parse's memo kept for a model in an earlier pass, which only invalidation drops.

The memo keeps a model's key the first time a user compares it, and a later pass compares that key again unless a
mutation through the generation store invalidated the model's render caches. Rendering anew is not proven to change
nothing; keys over model facts (G06, G07) replace the memo. The ``RENDER_MEMOS`` check records the keys that differ.
"""
BaseDedup: TypeAlias = Literal["by-type-hint", "by-reference"]
"""How a user that removes merged models tells apart the bases of their children: by module and type hint, or by
reference."""


@final
@record
class ShapeRow:
    """How one deduplication or reuse user compares models."""

    name: str
    union_tags: bool
    """Whether the key holds the tags of the model's discriminated unions taken before any merge."""
    own_class_name: bool
    """Whether a model renders under its own class name (the name it shares with its duplicates) instead of ``M``."""
    root_models: RootModels
    key_cache: KeyCache
    base_dedup: BaseDedup | None


SAME_NAME: ShapeRow = ShapeRow(
    "legacy-same-name-dedup",
    union_tags=False,
    own_class_name=True,
    root_models="redirected",
    key_cache=STALE_DEDUP_KEYS,
    base_dedup="by-type-hint",
)
"""L3 ``__delete_duplicate_models``: models of one class name in a module that render alike, union tags left out."""
COLLAPSE_REUSE: ShapeRow = ShapeRow(
    "legacy-collapse-reuse",
    union_tags=True,
    own_class_name=False,
    root_models="skipped",
    key_cache="rendered",
    base_dedup="by-reference",
)
"""L3 ``--collapse-reuse-models``: rounds of merges until none is left, rendering each round anew, roots left out."""
TREE_SCOPE: ShapeRow = ShapeRow(
    "legacy-tree-scope",
    union_tags=True,
    own_class_name=False,
    root_models="compared",
    key_cache=STALE_DEDUP_KEYS,
    base_dedup=None,
)
"""L3 ``--reuse-scope tree``: models duplicated across modules, root models included."""
REUSE_MODEL: ShapeRow = ShapeRow(
    "legacy-reuse-model",
    union_tags=True,
    own_class_name=False,
    root_models="skipped-when-collapsed",
    key_cache=STALE_DEDUP_KEYS,
    base_dedup=None,
)
"""L5 ``--reuse-model``: models of a module, root models left out only under ``--collapse-root-models``."""


def skips_root_model(row: ShapeRow, *, collapse_root_models: bool) -> bool:
    """Return whether the user of a row leaves root models out of the comparison."""
    return row.root_models == "skipped" or (row.root_models == "skipped-when-collapsed" and collapse_root_models)


def reuse_candidate_key(model_type: type, bases: Iterable[Hashable], field_names: Iterable[str | None]) -> Key:
    """Return what a reuse duplicate shares with the model it duplicates before rendering: type, bases, field names."""
    return model_type, tuple(bases), tuple(field_names)


class _ImportLike(Protocol):
    """The parts of an import that the stdout fingerprint reads."""

    @property
    def import_(self) -> str:
        """The imported name."""
        raise NotImplementedError

    @property
    def from_(self) -> str | None:
        """The module the name is imported from, if any."""
        raise NotImplementedError

    @property
    def alias(self) -> str | None:
        """The name the import binds instead, if any."""
        raise NotImplementedError


def stdout_fingerprint(rendered: str, imports: Iterable[_ImportLike]) -> Key:
    """Return the final rendered definition of a model and its imports with aliases equal to their names dropped."""
    return rendered, tuple(
        sorted({
            (False, "", import_.import_, "")
            if "." in import_.import_
            else (
                import_.from_ is not None,
                import_.from_ or "",
                import_.import_,
                "" if not import_.alias or import_.alias == import_.import_ else import_.alias,
            )
            for import_ in imports
        })
    )


class _ReferenceLike(Protocol):
    """The parts of a reference that the keys read."""

    @property
    def path(self) -> str:
        """The path that identifies the reference."""
        raise NotImplementedError

    @property
    def short_name(self) -> str:
        """The name type hints render for the reference."""
        raise NotImplementedError


ModelT = TypeVar("ModelT")
ReferenceT = TypeVar("ReferenceT", bound=_ReferenceLike)


class ShapeSource(Protocol[ModelT, ReferenceT]):
    """What the keys read off the live models: the adapter (``parser/base.py:_ModelShapeSource``)."""

    def own_class_name(self, model: ModelT) -> str | None:
        """Return the class name the model shares with its duplicates, or None for the name it renders under."""
        raise NotImplementedError

    def render(self, model: ModelT, class_name: str | None) -> tuple[str, Hashable, tuple[Hashable, ...]]:
        """Return the model rendered under a class name, its imports and its values as written, uncached."""
        raise NotImplementedError

    def dedup_key(self, model: ModelT, slot: KeySlot) -> Key:
        """Return the dedup key the memo keeps for the model in a slot, rendering and keeping it for an empty slot."""
        raise NotImplementedError

    def forget_dedup_keys(self, model: ModelT) -> None:
        """Drop the dedup keys the memo keeps for the model."""
        raise NotImplementedError

    def mask_pattern(self, text: str) -> re.Pattern[str]:
        """Return the pattern that finds string literals, field declarations and names in the text."""
        raise NotImplementedError

    def references(self, model: ModelT) -> Sequence[ReferenceT]:
        """Return the references of the model's data types in render order."""
        raise NotImplementedError

    def rule_references(self, model: ModelT) -> Sequence[ReferenceT]:
        """Return the references of the types that the model's schema validator rules name, in order."""
        raise NotImplementedError

    def model_of(self, reference: ReferenceT) -> ModelT | None:
        """Return the model a reference names, or None when it names no model."""
        raise NotImplementedError

    def identity(self, reference: ReferenceT) -> Hashable:
        """Return what tells apart the models that references name: the model key."""
        raise NotImplementedError

    def agreed(self, site: str, *, by_key: bool, by_path: Callable[[], bool]) -> bool:
        """Return ``by_key``, what a site found by `identity`.

        Under the adapter's check, a different ``by_path()``, what the site found by path, is a violation.
        """
        raise NotImplementedError


@final
@dataclass(slots=True)
class ModelShapes(Generic[ModelT, ReferenceT]):
    """The keys of models for one comparison pass of one user, with the shapes memoized for that pass."""

    row: ShapeRow
    source: ShapeSource[ModelT, ReferenceT]
    union_tags: Mapping[ModelT, tuple[Hashable, ...]]
    memo: dict[ModelT, tuple[Key, Sequence[ReferenceT]]] = field(default_factory=dict)

    def _tags(self, model: ModelT) -> tuple[Hashable, ...]:
        return self.union_tags.get(model, ()) if self.row.union_tags else ()

    def key(self, model: ModelT) -> Key:
        """Return the rendered model with the tags of its discriminated unions when the row compares them.

        The model renders under its own class name or as ``M``, as the row says, and the parse's memo keeps the key
        (:data:`STALE_DEDUP_KEYS`).
        """
        slot: KeySlot = (self.source.own_class_name(model), False) if self.row.own_class_name else (None, True)
        if self.row.key_cache == "rendered":
            self.source.forget_dedup_keys(model)
        key = self.source.dedup_key(model, slot)
        return (*key, *tags) if (tags := self._tags(model)) else key

    def shape(self, model: ModelT) -> tuple[Key, Sequence[ReferenceT]]:
        """Return a model's dedup key with referenced model names masked, and its references in order.

        The key is rendered afresh, since cached keys keep the names that references had when they were cached, and
        memoized for one comparison pass. Names are masked outside string literals and field declarations only, so
        defaults, descriptions and field names that spell a referenced model name still tell models apart. The union
        tags the row compares belong to the key, as they do to :meth:`key`.
        """
        if (shape := self.memo.get(model)) is not None:
            return shape
        references = self.source.references(model)
        masks: dict[str, str] = {}
        for reference in references:
            masks.setdefault(reference.short_name, f"<{len(masks)}>")
        rendered, imports, written = self.source.render(model, "M")
        masked = self.source.mask_pattern(rendered).sub(
            lambda match: masks.get(name, name) if (name := match[1]) else match[0], rendered
        )
        key = (type(model), masked, imports, len(references), *written)
        compared = (*references, *self.source.rule_references(model))
        self.memo[model] = shape = ((*key, *tags) if (tags := self._tags(model)) else key), compared
        return shape

    def _compared_references(self, model: ModelT) -> tuple[ReferenceT, ...]:
        """Return the references that the models a model matches must match too: its own, then those its rules name.

        Rules name a model by its class name alone until imports are final, so models of other modules that share a
        name tell the rules apart only by their shapes. The masked rendering keeps the names that rules write.
        """
        return (*self.source.references(model), *self.source.rule_references(model))

    def references_match(self, left: ModelT, right: ModelT) -> bool:
        """Return whether equally rendered models also reference models of the same shape.

        Type hints carry only reference names, and references from different sources can share a name until
        module-level renaming, so equal render output does not prove equal field types. Referenced models are compared
        pairwise with the names of their own references masked, so models that differ only in how deeper references
        are named still match. Reference pairs already under comparison, told apart by `ShapeSource.identity`, are
        assumed to match, which keeps recursive models finite, and models that reference the very same models match
        without rendering them again.
        """
        left_references, right_references = self._compared_references(left), self._compared_references(right)
        if len(left_references) == len(right_references) and all(map(is_, left_references, right_references)):
            return True
        pending: list[tuple[ModelT | None, ModelT | None]] = [(left, right)]
        assumed_pairs: set[tuple[Hashable, Hashable]] = set()
        assumed_paths: set[tuple[str, str]] = set()
        while pending:
            left_model, right_model = pending.pop()
            if (
                left_model is None
                or right_model is None
                or (left_shape := self.shape(left_model))[0] != (right_shape := self.shape(right_model))[0]
            ):
                return False
            for left_reference, right_reference in zip(left_shape[1], right_shape[1], strict=True):
                if left_reference is right_reference:
                    continue
                pair = (self.source.identity(left_reference), self.source.identity(right_reference))
                paths = (left_reference.path, right_reference.path)
                if self.source.agreed(
                    "assumed reference pairs",
                    by_key=pair in assumed_pairs,
                    by_path=lambda paths=paths: paths in assumed_paths,
                ):
                    continue
                assumed_pairs.add(pair)
                assumed_paths.add(paths)
                pending.append((self.source.model_of(left_reference), self.source.model_of(right_reference)))
        return True

    def _referenced_shapes(self, model: ModelT) -> tuple[object, ...]:
        """Return the shapes of the models a model references in order, which models that match share.

        A reference to anything other than a model matches only itself, so it stands for itself.
        """
        return tuple(
            self.shape(source)[0] if (source := self.source.model_of(reference)) is not None else id(reference)
            for reference in self._compared_references(model)
        )

    def iter_matching_duplicates(self, models: Iterable[ModelT], *, keyed: bool) -> Iterator[tuple[ModelT, ModelT]]:
        """Pair each model with the earlier model of the same key whose content and references both match.

        Without ``keyed`` every model shares one key, for models the caller already grouped by :meth:`key`. Earlier
        unmatched models of a key never match each other, so at most one matches. A model that references the very
        same models as one of them is paired at once. Otherwise it is compared in full only with those whose
        referenced models render alike, so many equally rendered models with different references are not compared
        pairwise. Referenced models are rendered only for keys with a model that is not paired at once.
        """
        by_references: dict[tuple[object, ...], ModelT] = {}
        by_shapes: defaultdict[tuple[object, ...], list[ModelT]] = defaultdict(list)
        unshaped: dict[Key | None, list[ModelT]] = {}
        for model in models:
            key = self.key(model) if keyed else None
            references = (key, *map(id, self._compared_references(model)))
            if (canonical := by_references.get(references)) is None and (pending := unshaped.get(key)) is not None:
                for candidate in pending:
                    by_shapes[key, self._referenced_shapes(candidate)].append(candidate)
                pending.clear()
                candidates = by_shapes[key, self._referenced_shapes(model)]
                canonical = next((c for c in candidates if self.references_match(c, model)), None)
            if canonical is None:
                unshaped.setdefault(key, []).append(model)
                by_references[references] = model
                continue
            yield canonical, model
