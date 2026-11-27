"""Writes ``DataModel.fields`` and the run-state list ``Parser.results`` outside the store operations their rows
name, calls ``DataModel.set_base_class``, which only the store runs, and reads and writes the bookkeeping of a
``Reference``.

``GenerationStore.set_fields``, one of those operations, writes ``DataModel.fields`` too, ``Parser.__init__`` builds
``results``, the store and the model's own initialization call ``set_base_class``, which writes ``fields`` for the
store, and ``Reference.__init__`` builds its bookkeeping: heki allows them all.
"""

from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.parser.base import Parser
from datamodel_code_generator.reference import Reference


def rename_fields(model: DataModel) -> None:
    model.fields = ["renamed"]


def register(parser: Parser) -> None:
    parser.results.append("model")


def rebase(model: DataModel) -> None:
    model.set_base_class()


def users(reference: Reference) -> list[object]:
    return reference.children


def unload(reference: Reference) -> None:
    reference.loaded = False
