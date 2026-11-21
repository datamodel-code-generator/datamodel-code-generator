"""#3869 architecture boundaries: a star import may bind ``import_module`` or ``__import__``, and the rules read them as such.

The model leaves such a name open (``star``) and gives it no origin; a rule that looks for a dynamic import counts it,
as it counts a gate that fails closed. A parameter of the name still shadows it.
layers: parser, shared-model
"""
from importlib import *

CONCRETE_BACKEND = "datamodel_code_generator.model.pydantic_v2.base_model"
import_module(CONCRETE_BACKEND)
__import__(CONCRETE_BACKEND)


def shadowed(import_module):
    import_module(CONCRETE_BACKEND)
