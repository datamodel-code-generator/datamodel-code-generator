"""Target-aware import metadata for structured Python type annotations."""

from __future__ import annotations

from functools import lru_cache


class PythonTypeUnavailableError(ValueError):
    """A known Python type is unavailable for the configured target."""

    def __init__(self, type_name: str, target_version: tuple[int, int]) -> None:
        self.type_name = type_name
        self.target_version = target_version
        super().__init__(type_name, target_version)


_PYTHON_BUILTIN_TYPE_NAMES = frozenset({
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
    "bool",
    "bytearray",
    "bytes",
    "classmethod",
    "complex",
    "dict",
    "enumerate",
    "filter",
    "float",
    "frozenset",
    "int",
    "list",
    "map",
    "memoryview",
    "object",
    "property",
    "range",
    "reversed",
    "set",
    "slice",
    "staticmethod",
    "str",
    "super",
    "tuple",
    "type",
    "zip",
})
_PYTHON_BUILTIN_TYPE_INTRODUCED_IN: dict[str, tuple[int, int]] = {
    "BaseExceptionGroup": (3, 11),
    "ExceptionGroup": (3, 11),
    "PythonFinalizationError": (3, 13),
}


_PYTHON_TYPE_IMPORT_PATHS: dict[str, str] = {
    # collections.abc
    "Callable": "collections.abc.Callable",
    "Iterable": "collections.abc.Iterable",
    "Iterator": "collections.abc.Iterator",
    "Generator": "collections.abc.Generator",
    "Awaitable": "collections.abc.Awaitable",
    "Coroutine": "collections.abc.Coroutine",
    "AsyncIterable": "collections.abc.AsyncIterable",
    "AsyncIterator": "collections.abc.AsyncIterator",
    "AsyncGenerator": "collections.abc.AsyncGenerator",
    "Mapping": "collections.abc.Mapping",
    "MutableMapping": "collections.abc.MutableMapping",
    "Sequence": "collections.abc.Sequence",
    "MutableSequence": "collections.abc.MutableSequence",
    "Set": "collections.abc.Set",
    "MutableSet": "collections.abc.MutableSet",
    "Collection": "collections.abc.Collection",
    "Reversible": "collections.abc.Reversible",
    "ByteString": "collections.abc.ByteString",
    "Container": "collections.abc.Container",
    "Hashable": "collections.abc.Hashable",
    "ItemsView": "collections.abc.ItemsView",
    "KeysView": "collections.abc.KeysView",
    "MappingView": "collections.abc.MappingView",
    "Sized": "collections.abc.Sized",
    "ValuesView": "collections.abc.ValuesView",
    # collections
    "defaultdict": "collections.defaultdict",
    "OrderedDict": "collections.OrderedDict",
    "Counter": "collections.Counter",
    "deque": "collections.deque",
    "ChainMap": "collections.ChainMap",
    # re
    "Pattern": "re.Pattern",
    "Match": "re.Match",
    # typing
    "Any": "typing.Any",
    "Type": "typing.Type",
    "Union": "typing.Union",
    "Optional": "typing.Optional",
    "Literal": "typing.Literal",
    "Final": "typing.Final",
    "ClassVar": "typing.ClassVar",
    "Annotated": "typing.Annotated",
    "TypeVar": "typing.TypeVar",
    "TypeAlias": "typing.TypeAlias",
    "Never": "typing.Never",
    "NoReturn": "typing.NoReturn",
    "Self": "typing.Self",
    "LiteralString": "typing.LiteralString",
    "TypeGuard": "typing.TypeGuard",
    "NamedTuple": "typing.NamedTuple",
    "TypedDict": "typing.TypedDict",
    "Protocol": "typing.Protocol",
    "Generic": "typing.Generic",
    "Concatenate": "typing.Concatenate",
    "ParamSpec": "typing.ParamSpec",
    # pathlib
    "Path": "pathlib.Path",
    "PurePath": "pathlib.PurePath",
    "PathInfo": "pathlib.types.PathInfo",
    # decimal
    "Decimal": "decimal.Decimal",
    # uuid
    "UUID": "uuid.UUID",
    # datetime
    "datetime": "datetime.datetime",
    "date": "datetime.date",
    "time": "datetime.time",
    "timedelta": "datetime.timedelta",
    # enum
    "Enum": "enum.Enum",
    "IntEnum": "enum.IntEnum",
    "StrEnum": "enum.StrEnum",
    "Flag": "enum.Flag",
    "IntFlag": "enum.IntFlag",
    # pydantic
    "BaseModel": "pydantic.BaseModel",
}

_TARGET_STABLE_PYTHON_TYPE_MODULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "typing",
        (
            "AbstractSet",
            "Any",
            "AnyStr",
            "AsyncContextManager",
            "AsyncGenerator",
            "AsyncIterable",
            "AsyncIterator",
            "Awaitable",
            "BinaryIO",
            "ByteString",
            "Callable",
            "ChainMap",
            "ClassVar",
            "Collection",
            "Concatenate",
            "Container",
            "ContextManager",
            "Coroutine",
            "Counter",
            "DefaultDict",
            "Deque",
            "Dict",
            "Final",
            "ForwardRef",
            "FrozenSet",
            "Generator",
            "Generic",
            "GenericAlias",
            "Hashable",
            "IO",
            "ItemsView",
            "Iterable",
            "Iterator",
            "KeysView",
            "List",
            "Literal",
            "LiteralString",
            "Mapping",
            "MappingView",
            "Match",
            "MutableMapping",
            "MutableSequence",
            "MutableSet",
            "NamedTuple",
            "Never",
            "NewType",
            "NoDefault",
            "NoReturn",
            "NotRequired",
            "Optional",
            "OrderedDict",
            "ParamSpec",
            "ParamSpecArgs",
            "ParamSpecKwargs",
            "Pattern",
            "Protocol",
            "ReadOnly",
            "Required",
            "Reversible",
            "Self",
            "Sequence",
            "Set",
            "Sized",
            "SupportsAbs",
            "SupportsBytes",
            "SupportsComplex",
            "SupportsFloat",
            "SupportsIndex",
            "SupportsInt",
            "SupportsRound",
            "Text",
            "TextIO",
            "Tuple",
            "Type",
            "TypeAlias",
            "TypeAliasType",
            "TypeGuard",
            "TypeIs",
            "TypeForm",
            "TypeVar",
            "TypeVarTuple",
            "TypedDict",
            "Union",
            "Unpack",
            "ValuesView",
            "override",
        ),
    ),
    (
        "collections.abc",
        (
            "AsyncGenerator",
            "AsyncIterable",
            "AsyncIterator",
            "Awaitable",
            "Buffer",
            "ByteString",
            "Callable",
            "Collection",
            "Container",
            "Coroutine",
            "Generator",
            "Hashable",
            "ItemsView",
            "Iterable",
            "Iterator",
            "KeysView",
            "Mapping",
            "MappingView",
            "MutableMapping",
            "MutableSequence",
            "MutableSet",
            "Reversible",
            "Sequence",
            "Set",
            "Sized",
            "ValuesView",
        ),
    ),
    (
        "collections",
        ("ChainMap", "Counter", "OrderedDict", "UserDict", "UserList", "UserString", "defaultdict", "deque"),
    ),
    (
        "pathlib",
        (
            "DirEntryInfo",
            "Path",
            "PosixPath",
            "PurePath",
            "PurePosixPath",
            "PureWindowsPath",
            "UnsupportedOperation",
            "WindowsPath",
        ),
    ),
    ("pathlib.types", ("PathInfo",)),
    (
        "decimal",
        (
            "Clamped",
            "Context",
            "ConversionSyntax",
            "Decimal",
            "DecimalException",
            "DecimalTuple",
            "DivisionByZero",
            "DivisionImpossible",
            "DivisionUndefined",
            "FloatOperation",
            "Inexact",
            "InvalidContext",
            "InvalidOperation",
            "Overflow",
            "Rounded",
            "Subnormal",
            "Underflow",
        ),
    ),
    ("uuid", ("SafeUUID", "UUID")),
    ("datetime", ("date", "datetime", "time", "timedelta", "timezone", "tzinfo")),
    (
        "enum",
        (
            "DynamicClassAttribute",
            "Enum",
            "EnumCheck",
            "EnumDict",
            "EnumMeta",
            "EnumType",
            "Flag",
            "FlagBoundary",
            "IntEnum",
            "IntFlag",
            "ReprEnum",
            "StrEnum",
            "auto",
            "member",
            "nonmember",
            "property",
            "verify",
        ),
    ),
    ("re", ("Match", "Pattern", "PatternError", "RegexFlag", "Scanner", "error")),
)

_TARGET_STABLE_PYTHON_TYPE_INTRODUCED_IN: dict[tuple[str, str], tuple[int, int]] = {
    ("collections.abc", "Buffer"): (3, 12),
    ("enum", "EnumCheck"): (3, 11),
    ("enum", "EnumDict"): (3, 13),
    ("enum", "EnumType"): (3, 11),
    ("enum", "FlagBoundary"): (3, 11),
    ("enum", "ReprEnum"): (3, 11),
    ("enum", "StrEnum"): (3, 11),
    ("enum", "member"): (3, 11),
    ("enum", "nonmember"): (3, 11),
    ("enum", "property"): (3, 11),
    ("enum", "verify"): (3, 11),
    ("pathlib", "DirEntryInfo"): (3, 14),
    ("pathlib.types", "PathInfo"): (3, 14),
    ("pathlib", "UnsupportedOperation"): (3, 13),
    ("re", "PatternError"): (3, 13),
    ("typing", "LiteralString"): (3, 11),
    ("typing", "Never"): (3, 11),
    ("typing", "NoDefault"): (3, 13),
    ("typing", "NotRequired"): (3, 11),
    ("typing", "ReadOnly"): (3, 13),
    ("typing", "Required"): (3, 11),
    ("typing", "Self"): (3, 11),
    ("typing", "TypeAliasType"): (3, 12),
    ("typing", "TypeForm"): (3, 15),
    ("typing", "TypeIs"): (3, 13),
    ("typing", "TypeVarTuple"): (3, 11),
    ("typing", "Unpack"): (3, 11),
    ("typing", "override"): (3, 12),
}

_PYTHON_TYPE_BACKPORTS: dict[str, tuple[tuple[int, int], str]] = {
    "Buffer": ((3, 12), "typing_extensions"),
    "LiteralString": ((3, 11), "typing_extensions"),
    "Never": ((3, 11), "typing_extensions"),
    "NoDefault": ((3, 13), "typing_extensions"),
    "NotRequired": ((3, 11), "typing_extensions"),
    "ReadOnly": ((3, 13), "typing_extensions"),
    "Required": ((3, 11), "typing_extensions"),
    "Self": ((3, 11), "typing_extensions"),
    "TypeAliasType": ((3, 12), "typing_extensions"),
    "TypeIs": ((3, 13), "typing_extensions"),
    "TypeForm": ((3, 15), "typing_extensions"),
    "TypeVarTuple": ((3, 11), "typing_extensions"),
    "Unpack": ((3, 11), "typing_extensions"),
    "override": ((3, 12), "typing_extensions"),
}


def is_python_builtin_type_name(type_name: str, target_version: tuple[int, int]) -> bool:
    """Recognize target builtins statically without consulting the host runtime.

    A bare target-newer name remains available for a user-defined symbol. Only an
    explicit ``builtins.Name`` path proves stdlib intent and is rejected below.
    """
    if type_name not in _PYTHON_BUILTIN_TYPE_NAMES:
        return False
    return target_version >= _PYTHON_BUILTIN_TYPE_INTRODUCED_IN.get(type_name, (3, 10))


@lru_cache(maxsize=256)
def get_python_type_import_path(type_name: str, target_version: tuple[int, int]) -> str | None:
    """Return a static import path, distinguishing unknown and target-newer names."""
    if (backport := _PYTHON_TYPE_BACKPORTS.get(type_name)) and target_version < backport[0]:
        return f"{backport[1]}.{type_name}"
    if import_path := _PYTHON_TYPE_IMPORT_PATHS.get(type_name):
        module, _, import_name = import_path.rpartition(".")
        if target_version < _TARGET_STABLE_PYTHON_TYPE_INTRODUCED_IN.get((module, import_name), (3, 10)):
            raise PythonTypeUnavailableError(type_name, target_version)
        return import_path
    for module, names in _TARGET_STABLE_PYTHON_TYPE_MODULES:
        if type_name not in names:
            continue
        if target_version < _TARGET_STABLE_PYTHON_TYPE_INTRODUCED_IN.get((module, type_name), (3, 10)):
            raise PythonTypeUnavailableError(type_name, target_version)
        return f"{module}.{type_name}"
    return None


@lru_cache(maxsize=256)
def get_qualified_python_type_import_path(qualified_name: str, target_version: tuple[int, int]) -> str:
    """Resolve a known stdlib path for a target while preserving its module choice."""
    module, separator, type_name = qualified_name.rpartition(".")
    if not separator:
        return qualified_name
    if module == "builtins" and type_name in _PYTHON_BUILTIN_TYPE_NAMES:
        if target_version < _PYTHON_BUILTIN_TYPE_INTRODUCED_IN.get(type_name, (3, 10)):
            raise PythonTypeUnavailableError(qualified_name, target_version)
        return qualified_name
    for known_module, names in _TARGET_STABLE_PYTHON_TYPE_MODULES:
        if module != known_module or type_name not in names:
            continue
        if (backport := _PYTHON_TYPE_BACKPORTS.get(type_name)) and target_version < backport[0]:
            return f"{backport[1]}.{type_name}"
        if target_version < _TARGET_STABLE_PYTHON_TYPE_INTRODUCED_IN.get((module, type_name), (3, 10)):
            raise PythonTypeUnavailableError(qualified_name, target_version)
        return qualified_name
    return qualified_name


@lru_cache(maxsize=256)
def is_known_python_type_path(path: str) -> bool:
    """Return whether a bare builtin name or a dotted path names a type this registry lists for any target."""
    module, separator, type_name = path.rpartition(".")
    if not separator or module == "builtins":
        return type_name in _PYTHON_BUILTIN_TYPE_NAMES
    return (
        any(module == known_module and type_name in names for known_module, names in _TARGET_STABLE_PYTHON_TYPE_MODULES)
        or _PYTHON_TYPE_IMPORT_PATHS.get(type_name) == path
        or ((backport := _PYTHON_TYPE_BACKPORTS.get(type_name)) is not None and backport[1] == module)
    )


__all__ = [
    "PythonTypeUnavailableError",
    "get_python_type_import_path",
    "get_qualified_python_type_import_path",
    "is_known_python_type_path",
    "is_python_builtin_type_name",
]
