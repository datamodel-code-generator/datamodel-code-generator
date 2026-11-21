"""The package root: it re-exports the names of its modules and imports no name of __main__."""

from datamodel_code_generator import errors
from datamodel_code_generator.errors import Error as Error
from datamodel_code_generator.__main__ import run

shadowed = "the root binds the name of a submodule"


def __getattr__(name):
    return getattr(errors, name)
