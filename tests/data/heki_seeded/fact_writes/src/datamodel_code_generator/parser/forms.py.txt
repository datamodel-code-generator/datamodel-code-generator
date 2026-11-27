"""Writes of fact rows by a pass, each list mutator a finding once per call.

Through a store operation called by the pass itself, not through the store's builder (a finding), by every list
mutator, to a deep copy (none: no graph object holds it), through a container alias, by a store-only method that a
callback calls on an untyped receiver, by a function that only shares the name of such a method (judged by the
attribute it writes), and through a receiver whose class heki cannot tell (``MUT205``).
"""

from copy import deepcopy

from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
from datamodel_code_generator.parser.generation import GenerationStore


def through_the_store(store: GenerationStore, model: DataModel) -> None:
    store.set_fields(model, list(model.fields))


def reorder(model: DataModel) -> None:
    model.fields.reverse()
    model.fields[:] = model.fields[::-1]
    model.fields[0] = model.fields[0]
    model.fields.insert(0, model.fields.pop())
    model.fields.sort(reverse=True)


def reorder_a_copy(model: DataModel) -> None:
    copied = deepcopy(model.fields)
    copied.reverse()


def mark_through_an_alias(field: DataModelFieldBase) -> None:
    extras = field.extras
    extras["seen"] = True


def rebase_in_a_callback(models: list[DataModel]) -> list[None]:
    return list(map(lambda owner: owner.set_base_class(), models))


def set_base_class(model: DataModel) -> None:
    model.fields = []


def clear_untyped(models):
    for model in models:
        model.fields = []
