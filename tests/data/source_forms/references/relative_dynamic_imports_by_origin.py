"""#3869 architecture boundaries: a relative name of ``import_module`` is counted from every package it may have.

``load`` is ``import_module`` by origin, not by spelling, and ``package`` holds a neutral name and a backend's.
layers: parser, shared-model
"""
import importlib
from importlib import import_module as load

package = "neutral"
package = "datamodel_code_generator.model"


def aliased():
    return load(".pydantic_v2", "datamodel_code_generator.model")


def two_packages():
    return importlib.import_module(".pydantic_v2", package)
