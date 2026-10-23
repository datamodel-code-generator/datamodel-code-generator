"""Warnings of the pass modules, attributed to ``parser/base.py`` as before the passes moved out of it.

A warning names a frame by its ``stacklevel``; where that frame is now in a pass module, ``warn()`` would attribute the
warning to that module, and a filter by module (``-W "ignore:::datamodel_code_generator.parser.base"``,
``warnings.filterwarnings(module=...)``) would stop matching it.
"""

from __future__ import annotations

import sys
from typing import Final
from warnings import warn_explicit

__all__ = ["warn_as_parser_base"]

_PARSER_BASE_MODULE: Final = "datamodel_code_generator.parser.base"
_TRACER_MODULE_PREFIX: Final = "pysnooper"


def warn_as_parser_base(message: str, category: type[Warning], *, stacklevel: int) -> None:
    """Warn as ``warn(message, category, stacklevel=stacklevel)`` does, with the module of ``parser/base.py``.

    The file, the line and the registry of the warnings shown once per location are those ``warn()`` takes from the
    frame ``stacklevel`` names; only the module the filters match is ``parser/base.py``. The frames of the wrappers that
    ``--debug`` puts around the functions of the pass modules (``snooper_to_methods``) are not counted, as
    ``parser/_pipeline.py:anchor_parse_caller`` does not count them: the wrappers stay once a parse traced them.
    """
    depth = 1
    for _ in range(stacklevel - 1):
        depth += 1
        while sys._getframe(depth).f_globals.get("__name__", "").startswith(_TRACER_MODULE_PREFIX):  # noqa: SLF001
            depth += 1
    frame = sys._getframe(depth)  # noqa: SLF001
    warn_explicit(
        message,
        category,
        frame.f_code.co_filename,
        frame.f_lineno,
        module=_PARSER_BASE_MODULE,
        registry=frame.f_globals.setdefault("__warningregistry__", {}),
        module_globals=frame.f_globals,
    )
