"""Identities of the models of one parse, independent of the names and paths they are written under.

Every `Reference` takes the next value of a process-wide counter when it is built (`Reference.model_post_init`) and
keeps it in its `__dict__`, beside its schema location. Copies of a Reference (`deepcopy`, `model_copy`) keep the key of
their original; an unpickled one takes a new key, since the counter of the process that pickled it is not this one's.
Keys never reach output.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final, final

from datamodel_code_generator import Error
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._records import record
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.reference import SCHEMA_OWNER_FRAMES_KEY, location_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator._checks import CheckMode
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.reference import Reference, SchemaLocation

UNRESOLVED_MODEL_KEY: Final = ModelKey(-1)
"""The key a lookup returns for a reference or a path naming none of the models of a site; no model takes it."""


@final
@record(eq=False)
class BuildFrames:
    """The model builder frames open when a model is recorded, innermost first, and the model they build."""

    model: Reference
    """The model the frames stand for: the recorded model, or the base model of a read or write variant."""
    frames: tuple[list[Reference | None], ...]
    """One cell per frame: None until its builder returns, then the model that builder built, if it built one."""


def owner_of(reference: Reference) -> Reference | None:
    """Return the model whose builder built the model of ``reference``, or None for a root model.

    It is the innermost filled frame cell that holds another model than the one the frames stand for. A model built
    outside every builder (a definition, a document root, a `$ref` target) has no owner; neither has a model the parser
    did not record.
    """
    if (record := reference.__dict__.get(SCHEMA_OWNER_FRAMES_KEY)) is None:
        return None
    key = model_key_of(record.model)
    return next((owner for (owner,) in record.frames if owner is not None and model_key_of(owner) != key), None)


def is_root_or_owned(reference: Reference) -> bool:
    """Return whether the model of ``reference`` was recorded and has an owner or is a root model.

    A root model is recorded with no builder frame open but its own builders'. A model recorded inside a builder that
    built no model, or never recorded, breaks the rule.
    """
    if owner_of(reference) is not None:
        return True
    record = reference.__dict__.get(SCHEMA_OWNER_FRAMES_KEY)
    return record is not None and all(owner is not None for (owner,) in record.frames)


@final
@record
class ModelIdentity:
    """Who a model is, whatever name and path it is written under."""

    key: ModelKey
    location: SchemaLocation | None
    """The schema object the model is built from (`location_of`), or None."""
    owner: ModelKey | None
    """The model whose builder built this one (`owner_of`), or None for a root model."""


def identity_of(reference: Reference) -> ModelIdentity:
    """Return the identity of the model of ``reference``, from what the Reference carries."""
    owner = owner_of(reference)
    return ModelIdentity(
        model_key_of(reference), location_of(reference), None if owner is None else model_key_of(owner)
    )


@final
@record(eq=False)
class KeyedModels:
    """The models that an order or a graph is built over, by model key, and the keys of the models at each path."""

    site: str
    """What the models are keyed for, as a check names it."""
    models: Mapping[ModelKey, DataModel]
    keys_by_path: Mapping[str, tuple[ModelKey, ...]]
    """Read for the references whose key names no model of the site and for the paths a model or a parser lists."""
    mode: CheckMode | None
    """How the ``MODEL_GRAPH`` check handles a violation, or None when it is off."""

    def key_of(self, reference: Reference) -> ModelKey:
        """Return the key of the model of the site that ``reference`` names, or `UNRESOLVED_MODEL_KEY`.

        Row ``legacy-keyless-reference-by-path``: a reference whose key names no model of the site (one built outside
        the resolver, such as by a parser extension) names the model at its path (`key_at`), as every reference did
        when the sites keyed models by path. Under the ``MODEL_GRAPH`` check, a reference whose key names a model of
        the site that is not at its path is a violation.
        """
        if (key := model_key_of(reference)) not in self.models:
            return self.key_at(reference.path)
        if (mode := self.mode) is not None and key not in self.keys_by_path.get(reference.path, ()):
            path = redact_reference(reference.path)
            mode.violation(
                Error,
                f"{self.site}: the path and the model key of the reference {path} name different models",
                site=self.site,
                path=path,
            )
        return key

    def key_at(self, path: str) -> ModelKey:
        """Return the key of the model of the site at ``path``, or `UNRESOLVED_MODEL_KEY` when none is there.

        Under the ``MODEL_GRAPH`` check, a path that two or more models of the site share is a violation: a lookup by
        path cannot tell them apart.
        """
        if not (keys := self.keys_by_path.get(path)):
            return UNRESOLVED_MODEL_KEY
        if len(keys) > 1 and (mode := self.mode) is not None:
            redacted = redact_reference(path)
            mode.violation(
                Error,
                f"{self.site}: a lookup by the path {redacted} names {len(keys)} models",
                site=self.site,
                path=redacted,
            )
        return keys[-1]

    def keys_of(self, references: Iterable[Reference]) -> frozenset[ModelKey]:
        """Return the keys that `key_of` returns for ``references``."""
        return frozenset(map(self.key_of, references))

    def resolve(self, paths: Iterable[str]) -> frozenset[ModelKey]:
        """Return the keys that `key_at` returns for ``paths``."""
        return frozenset(map(self.key_at, paths))


def keyed_models(models: Iterable[DataModel], site: str) -> KeyedModels:
    """Key the models that ``site`` orders or builds a graph over.

    Two models that hold one Reference share its path and key, and the site takes them for one model, as it did by
    path. Under the ``MODEL_GRAPH`` check, two models whose Reference objects share a key (a copy) are a violation.
    """
    by_key: dict[ModelKey, DataModel] = {}
    keys_by_path: dict[str, tuple[ModelKey, ...]] = {}
    mode = check_mode(CheckArea.MODEL_GRAPH)
    for model in models:
        key = model_key_of(model.reference)
        if mode is not None and by_key.get(key, model).reference is not model.reference:
            path = redact_reference(model.path)
            mode.violation(
                Error,
                f"{site}: two Reference objects of live models share the model key of {path}",
                site=site,
                path=path,
            )
        by_key[key] = model
        if key not in (keys := keys_by_path.get(path := model.path, ())):
            keys_by_path[path] = (*keys, key)
    return KeyedModels(site, by_key, keys_by_path, mode)


def unchecked(keyed: KeyedModels) -> KeyedModels:
    """Return ``keyed`` without its check mode, for the computation `graph_answer` compares, so no violation repeats."""
    return replace(keyed, mode=None)
