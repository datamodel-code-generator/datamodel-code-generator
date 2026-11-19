"""#7026 redundant-local-import: a function-level import of modules the importer already loads at module level.

An import under a module-level ``if`` (a type-checking, version or platform test) loads nothing for certain, a ``try``
loads unless the import fails, and a module that its target imports back cannot move its import to module level.
"""
