"""Warnings of code moved out of a public parser module, attributed to that module as before the move.

A warning names a frame by its ``stacklevel``; where that frame is now in a pass module or a concern module of a parser
(``parser/_<concern>.py``), ``warn()`` would attribute the warning to that module, and a filter by module
(``-W "ignore:::datamodel_code_generator.parser.base"``, ``warnings.filterwarnings(module=...)``) would stop matching
it. The pass modules warn as ``parser/base.py`` (``warn_as_parser_base``); a concern module calls ``warn_as_module``
with the public module its code came from, for example ``warn_as_module("datamodel_code_generator.parser.jsonschema",
message, category, stacklevel=2)`` where the moved method called ``warn(message, category, stacklevel=2)``, one more
for each frame the move put between the warning and the frame it names (a delegator method).
"""

from __future__ import annotations

from typing import Final

from datamodel_code_generator._warning_control import frame_at, warn_at_frame

__all__ = ["warn_as_module", "warn_as_parser_base"]

_PARSER_BASE_MODULE: Final = "datamodel_code_generator.parser.base"


def warn_as_module(module: str, message: str, category: type[Warning], *, stacklevel: int) -> None:
    """Warn as ``warn(message, category, stacklevel=stacklevel)`` does, with ``module`` as the module filters match.

    The file, the line and the registry of the warnings shown once per location are those ``warn()`` takes from the
    frame ``stacklevel`` names; only the module the filters match is ``module``. The frames of the wrappers that
    ``--debug`` puts around the functions of the pass and concern modules (``snooper_to_methods``) are not counted, as
    ``parser/_pipeline.py:parse_caller`` does not count them: the wrappers stay once a parse traced them.
    """
    warn_at_frame(frame_at(stacklevel), message, category, module=module)


def warn_as_parser_base(message: str, category: type[Warning], *, stacklevel: int) -> None:
    """Warn as ``warn_as_module`` does, with the module of ``parser/base.py``, for the pass modules."""
    warn_as_module(_PARSER_BASE_MODULE, message, category, stacklevel=stacklevel + 1)
