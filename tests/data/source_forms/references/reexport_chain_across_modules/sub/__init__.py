"""A subpackage imported by name from its parent; it imports its sibling module dynamically."""

import importlib

READER = importlib.import_module(".reader", __name__)
