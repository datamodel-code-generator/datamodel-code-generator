"""#3869 architecture boundaries: ``__import__`` read from ``importlib`` is a dynamic import as ``builtins``' is.

layers: shared-model
"""
import builtins
import importlib

CONCRETE_BACKEND = "datamodel_code_generator.model.pydantic_v2.base_model"
importlib.__import__(CONCRETE_BACKEND)
builtins.__import__(CONCRETE_BACKEND)
