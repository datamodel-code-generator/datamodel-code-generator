"""Static target availability of Python names, never read from the host running the generator.

The builtins namespace lives here rather than in the stdlib type registry so that ordinary generation, which checks
field names against it, does not load the registry.
"""

from __future__ import annotations

from functools import lru_cache

PYTHON_BUILTIN_TYPE_NAMES = frozenset({
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "BaseException",
    "BaseExceptionGroup",
    "BlockingIOError",
    "BrokenPipeError",
    "BufferError",
    "BytesWarning",
    "ChildProcessError",
    "ConnectionAbortedError",
    "ConnectionError",
    "ConnectionRefusedError",
    "ConnectionResetError",
    "DeprecationWarning",
    "EOFError",
    "EncodingWarning",
    "EnvironmentError",
    "Exception",
    "ExceptionGroup",
    "FileExistsError",
    "FileNotFoundError",
    "FloatingPointError",
    "FutureWarning",
    "GeneratorExit",
    "IOError",
    "ImportCycleError",
    "ImportError",
    "ImportWarning",
    "IndentationError",
    "IndexError",
    "InterruptedError",
    "IsADirectoryError",
    "KeyError",
    "KeyboardInterrupt",
    "LookupError",
    "MemoryError",
    "ModuleNotFoundError",
    "NameError",
    "None",
    "NotADirectoryError",
    "NotImplementedError",
    "OSError",
    "OverflowError",
    "PendingDeprecationWarning",
    "PermissionError",
    "ProcessLookupError",
    "PythonFinalizationError",
    "RecursionError",
    "ReferenceError",
    "ResourceWarning",
    "RuntimeError",
    "RuntimeWarning",
    "StopAsyncIteration",
    "StopIteration",
    "SyntaxError",
    "SyntaxWarning",
    "SystemError",
    "SystemExit",
    "TabError",
    "TimeoutError",
    "TypeError",
    "UnboundLocalError",
    "UnicodeDecodeError",
    "UnicodeEncodeError",
    "UnicodeError",
    "UnicodeTranslateError",
    "UnicodeWarning",
    "UserWarning",
    "ValueError",
    "Warning",
    "ZeroDivisionError",
    "bool",
    "bytearray",
    "bytes",
    "classmethod",
    "complex",
    "dict",
    "enumerate",
    "filter",
    "float",
    "frozendict",
    "frozenset",
    "int",
    "list",
    "map",
    "memoryview",
    "object",
    "property",
    "range",
    "reversed",
    "sentinel",
    "set",
    "slice",
    "staticmethod",
    "str",
    "super",
    "tuple",
    "type",
    "zip",
})
_PYTHON_BUILTIN_VALUE_NAMES = frozenset({
    "Ellipsis",
    "False",
    "NotImplemented",
    "True",
    "abs",
    "aiter",
    "all",
    "anext",
    "any",
    "ascii",
    "bin",
    "breakpoint",
    "callable",
    "chr",
    "compile",
    "copyright",
    "credits",
    "delattr",
    "dir",
    "divmod",
    "eval",
    "exec",
    "exit",
    "format",
    "getattr",
    "globals",
    "hasattr",
    "hash",
    "help",
    "hex",
    "id",
    "input",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "license",
    "locals",
    "max",
    "min",
    "next",
    "oct",
    "open",
    "ord",
    "pow",
    "print",
    "quit",
    "repr",
    "round",
    "setattr",
    "sorted",
    "sum",
    "vars",
})
_PYTHON_BUILTIN_INTRODUCED_IN: dict[str, tuple[int, int]] = {
    "BaseExceptionGroup": (3, 11),
    "ExceptionGroup": (3, 11),
    "ImportCycleError": (3, 15),
    "PythonFinalizationError": (3, 13),
    "frozendict": (3, 15),
    "sentinel": (3, 15),
}
_PYTHON_NAME_REMOVED_IN: dict[tuple[str, str], tuple[int, int]] = {
    ("pathlib", "DirEntryInfo"): (3, 15),
}


def is_target_name(module: str, name: str, introduced: tuple[int, int], target_version: tuple[int, int]) -> bool:
    """Return whether the target has a listed name: introduced at or before it and not removed by it."""
    removed = _PYTHON_NAME_REMOVED_IN.get((module, name))
    return introduced <= target_version and (removed is None or target_version < removed)


def is_target_builtin_name(name: str, target_version: tuple[int, int]) -> bool:
    """Return whether the target's builtins namespace has a listed name."""
    return is_target_name("builtins", name, _PYTHON_BUILTIN_INTRODUCED_IN.get(name, (3, 10)), target_version)


@lru_cache(maxsize=16)
def target_builtin_names(target_version: tuple[int, int]) -> frozenset[str]:
    """Return the public names of the target's builtins namespace: classes, functions, constants and site helpers."""
    return frozenset(
        name
        for name in PYTHON_BUILTIN_TYPE_NAMES | _PYTHON_BUILTIN_VALUE_NAMES
        if is_target_builtin_name(name, target_version)
    )


__all__ = [
    "PYTHON_BUILTIN_TYPE_NAMES",
    "is_target_builtin_name",
    "is_target_name",
    "target_builtin_names",
]
