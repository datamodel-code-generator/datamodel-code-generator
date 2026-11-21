"""Import computed back by a computed name."""

import importlib

NAME = "datamodel_code_generator." + "computed"
BACK = importlib.import_module(NAME)
