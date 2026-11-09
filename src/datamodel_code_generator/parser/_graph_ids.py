"""Identities of the models of one parse, independent of the names and paths they are written under.

Every `Reference` takes the next value of a process-wide counter when it is built (`Reference.model_post_init`) and
keeps it in its `__dict__`, beside its schema location. Copies of a Reference (`deepcopy`, `model_copy`) keep the key of
their original; an unpickled one takes a new key, since the counter of the process that pickled it is not this one's.
Keys never reach output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, final

from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.reference import Reference

UNRESOLVED_MODEL_KEY: Final = ModelKey(-1)
"""The key a lookup returns for a reference or a path naming none of the models of a site; no model takes it."""


@final
@record(eq=False)
class KeyedModels:
    """The models that an order or a graph is built over, by model key, and the keys of the models at each path."""

    site: str
    """What the models are keyed for."""
    models: Mapping[ModelKey, DataModel]
    keys_by_path: Mapping[str, tuple[ModelKey, ...]]
    """Read for the references whose key names no model of the site and for the paths a model or a parser lists."""

    def key_of(self, reference: Reference) -> ModelKey:
        """Return the key of the model of the site that ``reference`` names, or `UNRESOLVED_MODEL_KEY`.

        Row ``legacy-keyless-reference-by-path``: a reference whose key names no model of the site (one built outside
        the resolver, such as by a parser extension) names the model at its path (`key_at`), as every reference did
        when the sites keyed models by path.
        """
        if (key := model_key_of(reference)) not in self.models:
            return self.key_at(reference.path)
        return key

    def key_at(self, path: str) -> ModelKey:
        """Return the key of the model of the site at ``path``, or `UNRESOLVED_MODEL_KEY` when none is there."""
        return keys[-1] if (keys := self.keys_by_path.get(path)) else UNRESOLVED_MODEL_KEY

    def keys_of(self, references: Iterable[Reference]) -> frozenset[ModelKey]:
        """Return the keys that `key_of` returns for ``references``."""
        return frozenset(map(self.key_of, references))

    def resolve(self, paths: Iterable[str]) -> frozenset[ModelKey]:
        """Return the keys that `key_at` returns for ``paths``."""
        return frozenset(map(self.key_at, paths))


def keyed_models(models: Iterable[DataModel], site: str) -> KeyedModels:
    """Key the models that ``site`` orders or builds a graph over.

    Two models that hold one Reference share its path and key, and the site takes them for one model, as it did by
    path.
    """
    by_key: dict[ModelKey, DataModel] = {}
    keys_by_path: dict[str, tuple[ModelKey, ...]] = {}
    for model in models:
        by_key[key := model_key_of(model.reference)] = model
        if key not in (keys := keys_by_path.get(path := model.path, ())):
            keys_by_path[path] = (*keys, key)
    return KeyedModels(site, by_key, keys_by_path)
