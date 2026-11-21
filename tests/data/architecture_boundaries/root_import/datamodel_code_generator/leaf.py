"""A module below the root: every read of a root name or of __main__ fails, every read of a submodule passes."""

import importlib
from typing import TYPE_CHECKING

import datamodel_code_generator
import datamodel_code_generator as dcg
import datamodel_code_generator.__main__
import datamodel_code_generator.errors
from datamodel_code_generator import Error
from datamodel_code_generator import errors
from datamodel_code_generator import __main__ as cli
from datamodel_code_generator import lazy_name
from datamodel_code_generator import shadowed
from datamodel_code_generator.__main__ import run
from datamodel_code_generator.errors import Error as LeafError

from . import Error as RelativeError
from . import errors as relative_errors
from .__main__ import run as relative_run

if TYPE_CHECKING:
    from datamodel_code_generator import Error as TypingError


def function_local_import():
    from datamodel_code_generator import Error as LocalError

    return LocalError


def dynamic_imports():
    root = importlib.import_module("datamodel_code_generator")
    module = importlib.import_module("datamodel_code_generator.errors")
    relative = __import__("__main__", globals(), fromlist=["run"], level=1)
    return root, module, importlib.import_module("datamodel_code_generator.__main__"), relative


def attribute_reads():
    return (
        datamodel_code_generator.Error,
        datamodel_code_generator.errors.Error,
        dcg.__main__,
        dcg.errors,
        cli.run,
    )
