"""The tracing of ``--debug``: ``enable_debug_message`` turns pysnooper on, ``snooper_to_methods`` wraps a class.

The parsers decorate their classes with it when they are imported, so it imports no other module of the
package. The package root re-exports both under their historical module.
"""

from __future__ import annotations

import inspect
import sys
from typing import (
    TYPE_CHECKING,
    Any,
    TypeVar,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FunctionType


T = TypeVar("T")


def enable_debug_message() -> None:
    """Enable debug tracing with pysnooper."""
    global _debug_snoop  # noqa: PLW0603
    try:
        import pysnooper  # noqa: PLC0415

        pysnooper.tracer.DISABLED = False
        _debug_snoop = pysnooper.snoop
    except ImportError as err:
        from datamodel_code_generator._message_text import install_command  # noqa: PLC0415

        msg = f"Please run {install_command('debug')} to use debug option"
        raise Exception(msg) from err  # noqa: TRY002


DEFAULT_MAX_VARIABLE_LENGTH: int = 100


_debug_snoop: Callable[..., Any] | None = None
_LAYER_MODULE_PREFIX = "datamodel_code_generator.parser._passes."
_CONCERN_MODULE_PREFIX = "datamodel_code_generator.parser._"
_CONCERN_HOST_PARAMETER = "parser"


def _traces_module_function(module_name: str, function: FunctionType) -> bool:
    """Return whether ``snooper_to_methods`` traces a function of an imported module.

    It traces every function of a pass layer module (``parser/_passes/<layer>.py``) and the functions of the parser's
    concern modules (``parser/_<concern>.py``) that take the parser as their first parameter, named ``parser``: the
    methods those modules took over, while the module helpers they took over stay untraced as before.
    """
    if module_name.startswith(_LAYER_MODULE_PREFIX):
        return not module_name.rpartition(".")[2].startswith("_")
    code = function.__code__
    return (
        module_name.startswith(_CONCERN_MODULE_PREFIX)
        and "." not in module_name.removeprefix(_CONCERN_MODULE_PREFIX)
        and code.co_argcount > 0
        and code.co_varnames[0] == _CONCERN_HOST_PARAMETER
    )


def snooper_to_methods() -> Callable[..., Any]:
    """Class decorator to add pysnooper tracing to all methods.

    It also traces, once each, the module functions ``_traces_module_function`` selects: those of the pass layer
    modules, which the pipeline of ``Parser`` calls through their modules, and the methods a concern module of the
    parser took over, which the parser and the module itself call through the module. A module is covered when it is
    imported before the class is decorated. It wraps nothing for a class decorated before ``enable_debug_message()``
    (``--debug``) imported pysnooper and turned the tracing on.
    """

    def inner(cls: type[T]) -> type[T]:
        if (snoop := _debug_snoop) is None:
            return cls

        methods = inspect.getmembers(cls, predicate=inspect.isfunction)
        for name, method in methods:
            snooper_method = snoop(max_variable_length=DEFAULT_MAX_VARIABLE_LENGTH)(method)
            setattr(cls, name, snooper_method)
        for module_name, module in [*sys.modules.items()]:
            if not module_name.startswith(_CONCERN_MODULE_PREFIX):
                continue
            for name, function in inspect.getmembers(module, predicate=inspect.isfunction):
                if (
                    function.__module__ == module_name
                    and not hasattr(function, "__wrapped__")
                    and _traces_module_function(module_name, function)
                ):
                    setattr(module, name, snoop(max_variable_length=DEFAULT_MAX_VARIABLE_LENGTH)(function))
        return cls

    return inner


_ROOT_MODULE = "datamodel_code_generator"
enable_debug_message.__module__ = _ROOT_MODULE
snooper_to_methods.__module__ = _ROOT_MODULE
