"""A module-level import closed by an import back inside a function: lazy keys only.

The function runs after both modules are imported, so no import fails; code scanning reported this
shape on #7078 (scripts/behaviour_diff.py:75) but not on _python_type_annotation and its codec.
"""
