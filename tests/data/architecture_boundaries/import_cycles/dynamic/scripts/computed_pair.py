"""Import computed back by a computed name."""

import importlib

NAME = "scripts." + "computed"
BACK = importlib.import_module(NAME)
