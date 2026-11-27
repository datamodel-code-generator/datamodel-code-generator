"""Three pairs of modules that import each other, each a cycle that fails the check, and a pair that imports one way.

The import that closes a cycle runs while its module loads (``body_*``), in a function (``function_*``) or never
(``typing_*``, under ``TYPE_CHECKING``); each report names the modules of its cycle, which a baseline entry keeps.
"""
