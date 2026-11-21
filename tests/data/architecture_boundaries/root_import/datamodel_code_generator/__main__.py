"""The command line: the one module above the root, so it may read the root's names."""

import datamodel_code_generator
from datamodel_code_generator import Error
from datamodel_code_generator.__main__ import run as again

from . import shadowed


def run():
    return datamodel_code_generator.Error, Error, again, shadowed
