"""Utility functions for YAML/TOML loading and lazy BaseModel access."""

from __future__ import annotations

import contextlib
import re
import sys
import warnings
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache, partial
from io import BytesIO
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeVar

from typing_extensions import Sentinel, override

from datamodel_code_generator._context_scope import context_values
from datamodel_code_generator._lazy_exports import module_hooks as _module_hooks
from datamodel_code_generator._records import mutable_record, record
from datamodel_code_generator.deprecations import warn_deprecated

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator
    from pathlib import Path
    from types import ModuleType

    from datamodel_code_generator._format_types import PythonVersion

_T = TypeVar("_T")


@lru_cache(maxsize=1)
def _get_toml_loader() -> Callable[[Any], dict[str, Any]]:
    """Get the TOML parser lazily."""
    from tomllib import load as load_toml_data  # noqa: PLC0415

    return load_toml_data


@lru_cache(maxsize=32)
def _parse_toml(data: bytes) -> dict[str, Any]:
    """Parse the bytes of a TOML document; the result depends on the bytes alone, so equal content is parsed once."""
    return _get_toml_loader()(BytesIO(data))


def load_toml(path: Path) -> dict[str, Any]:
    """Load and parse a TOML file, returning a document the caller owns."""
    record_watch_dependency(path)
    return deepcopy(_parse_toml(path.read_bytes()))


def _load_config_toml(path: Path) -> dict[str, Any]:
    """Load a configuration file as ``load_toml`` does, raising an ``Error`` that names it where it is not valid TOML.

    A file that is not valid TOML (``TOMLDecodeError``) or not UTF-8 (``UnicodeDecodeError``), both ``ValueError``,
    is reported as an invalid configuration (#6844). The error is a ``ValueError`` as well, so a caller of
    ``load_pyproject_config`` catches it as before. ``load_toml`` keeps raising them as it did for its callers.
    """
    try:
        return load_toml(path)
    except ValueError as exc:
        from datamodel_code_generator import _InvalidConfigTomlError  # noqa: PLC0415

        msg = f"Invalid TOML in {path}: {exc}"
        raise _InvalidConfigTomlError(msg) from exc


def _find_pyproject_toml(settings_path: Path) -> Path | None:
    """Return the nearest pyproject.toml in ``settings_path`` or one of its parents."""
    for path in (settings_path, *settings_path.parents):
        pyproject_toml = path / "pyproject.toml"
        if pyproject_toml.is_file():
            return pyproject_toml
    return None


def _collecting_watch_dependencies() -> ModuleType | None:
    """Return watch support while it collects dependencies, without importing it on normal paths; else None."""
    if (watch_dependencies := sys.modules.get("datamodel_code_generator.watch_dependencies")) is not None and (
        watch_dependencies.collector_is_active()
    ):
        return watch_dependencies
    return None


def record_watch_dependency(path: Path) -> None:
    """Record an active watch dependency without importing watch support on normal paths."""
    if (watch_dependencies := _collecting_watch_dependencies()) is not None:
        watch_dependencies.record_local_dependency(path)


_YAML_1_2_BOOL_PATTERN = re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$")
_YAML_1_1_RESOLVER_TAGS = frozenset({"tag:yaml.org,2002:bool", "tag:yaml.org,2002:value"})
_YAML_DEPRECATED_BOOL_VALUES = ("True", "False", "TRUE", "FALSE")
_YAML_DEPRECATED_BOOL_LINE_PATTERN = re.compile(r"(?m)(?::|-\s*)\s*(True|False|TRUE|FALSE)(?:\s*(?:#.*)?)$")
_YAML_DEPRECATED_BOOL_WARNING_MESSAGE = "YAML bool "
_YAML_DEPRECATED_BOOL_WARNING_MODULE = "datamodel_code_generator"
_YAML_UNSUPPORTED_TAGS = {"tag:yaml.org,2002:set"}
_YAML_UNSUPPORTED_TAG_MARKERS = ("!!set", "tag:yaml.org,2002:set")
_YAML_TAG_DIRECTIVE_PATTERN = re.compile(r"(?m)^%TAG(?:\s|$)")
_YAML_LINE_BREAKS = "\r\n\x85\u2028\u2029"
_YAML_LINE_ENDS = f"\0{_YAML_LINE_BREAKS}"
_YAML_BLANKS = " \t"
_YAML_TOKEN_ENDS = f"\0{_YAML_BLANKS}{_YAML_LINE_BREAKS}"
# Pattern for scientific notation without decimal point (e.g., 1e-5, 1E+10)
# Standard YAML only matches floats with decimal points, missing patterns like "1e-5"
_YAML_SCIENTIFIC_NOTATION_PATTERN = re.compile(r"^[-+]?[0-9][0-9_]*[eE][-+]?[0-9]+$")
_YAML_1_1_NUMBER_TAGS: Final = MappingProxyType({
    "tag:yaml.org,2002:float": "tag:datamodel-code-generator:yaml-1.1-float",
    "tag:yaml.org,2002:int": "tag:datamodel-code-generator:yaml-1.1-int",
})
"""The tag of a plain scalar that PyYAML reads as a number of the tag it replaces and YAML 1.2 reads as text."""
_YAML_1_1_NUMBER_LOOKAHEAD: Final = r"(?=[^_:]*[_:])"
"""What keeps a number pattern of PyYAML to the texts with ``_`` or ``:``: digits joined by ``_`` and base 60, such as
``18_24`` and ``16:9``, which YAML 1.2 and the ryaml reader read as strings."""


@record
class YamlStringKeywords:
    """The keywords whose values the document models read as strings: a scalar, the items of a list, a map's values."""

    scalars: frozenset[str]
    lists: frozenset[str]
    maps: frozenset[str]


@mutable_record(eq=False, slots=True)
class _YamlNumberReading:
    """How a generation run reads the plain scalars that YAML 1.1 reads as numbers and YAML 1.2 as text.

    By default as the numbers PyYAML reads, counting them (``held``); as their text (``as_text``); or as their text
    where a string is required (``strings``) and as their numbers elsewhere.
    """

    as_text: bool = False
    strings: YamlStringKeywords | None = None
    held: int = 0


@record(eq=False)
class _YamlNumberText:
    """A scalar read where a string may be required, until the document it is in decides (``settle_yaml_numbers``).

    It is a key of its own: two keys of a mapping that spell one text are one key once it becomes its text.
    """

    text: str
    number: int | float


_OUTSIDE_A_RUN: Final = _YamlNumberReading()
"""The reading of the YAML loaded outside a generation run: the numbers PyYAML reads, counted as in a run, so that the
parsed-source cache keeps which of its documents held one."""
_YAML_NUMBER_READING: Final[ContextVar[_YamlNumberReading]] = ContextVar("yaml_number_reading", default=_OUTSIDE_A_RUN)


@contextlib.contextmanager
def yaml_number_reading(*, as_text: bool) -> Generator[None]:
    """Read the YAML of a generation run in the block: the YAML 1.1-only numbers as their text with ``as_text``.

    Without ``as_text`` they are the numbers PyYAML reads, and the run counts the ones its documents held
    (``yaml_numbers_held``). A block inside a run keeps the run's reading, unless it reads them as text and the run does
    not.
    """
    if (reading := _YAML_NUMBER_READING.get()) is not _OUTSIDE_A_RUN and (reading.as_text or not as_text):
        yield
        return
    with context_values({_YAML_NUMBER_READING: _YamlNumberReading(as_text=as_text)}):
        yield


@contextlib.contextmanager
def yaml_numbers_read_where_strings_required(strings: YamlStringKeywords) -> Generator[None]:
    """Read in the block a YAML 1.1-only number as its text where a string is required, else as its number.

    A string is required for a mapping key, and for the values of the keywords in ``strings``.
    """
    with context_values({_YAML_NUMBER_READING: _YamlNumberReading(strings=strings)}):
        yield


def yaml_numbers_held() -> int:
    """Return how many YAML 1.1-only numbers the documents read so far held, read as the numbers PyYAML reads."""
    return _YAML_NUMBER_READING.get().held


def note_yaml_numbers_held(count: int) -> None:
    """Count the YAML 1.1-only numbers that a document read from a cache held when it was read (``count``)."""
    _YAML_NUMBER_READING.get().held += count


def yaml_number_reading_key() -> str:
    """Return the part of a cache key that tells the readings of the YAML 1.1-only numbers apart."""
    reading = _YAML_NUMBER_READING.get()
    return "text" if reading.as_text else "numbers" if reading.strings is None else "strings"


def _construct_yaml_1_1_number(construct: Callable[[Any, Any], Any], loader: Any, node: Any) -> Any:
    """Construct a plain scalar that YAML 1.1 reads as a number and YAML 1.2 as text, as the reading says."""
    reading = _YAML_NUMBER_READING.get()
    if reading.as_text:
        return node.value
    number = construct(loader, node)
    if reading.strings is not None:
        return _YamlNumberText(node.value, number)
    reading.held += 1
    return number


def settle_yaml_numbers(data: _T) -> _T:
    """Return a loaded YAML value read where strings are required (``yaml_numbers_read_where_strings_required``).

    Each scalar that the reading kept for the document to decide becomes its text as a mapping key and as the value of a
    keyword that requires a string (a scalar, an item of a list, a value of a map), and its number anywhere else. An
    object or array that the document holds twice decides by the keyword of its first place. A tuple, a pair of an
    ``!!omap`` or ``!!pairs``, decides as a list without a keyword, and stays a tuple. A document that is one such
    scalar is no schema, and keeps it.
    """
    if (strings := _YAML_NUMBER_READING.get().strings) is None:
        return data
    pending: list[tuple[Any, object]] = [(data, None)] if isinstance(data, (dict, list)) else []
    seen: set[int] = set()
    tuples: list[tuple[Any, Any]] = []
    while pending:
        value, keyword = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, dict):
            if any(isinstance(key, _YamlNumberText) for key in value):
                items = list(value.items())
                value.clear()
                value.update((key.text if isinstance(key, _YamlNumberText) else key, item) for key, item in items)
            entries: Iterable[tuple[Any, Any]] = value.items()
            text_items = keyword in strings.maps
        else:
            entries = enumerate(value)
            text_items = keyword in strings.lists
        for key, item in list(entries):
            if isinstance(item, tuple):
                value[key] = pair = list(item)
                tuples.append((value, key))
                pending.append((pair, None))
            elif isinstance(item, _YamlNumberText):
                value[key] = (
                    item.text if text_items or (isinstance(key, str) and key in strings.scalars) else item.number
                )
            elif isinstance(item, (dict, list)):
                pending.append((item, key if isinstance(value, dict) else None))
    for holder, key in reversed(tuples):
        holder[key] = tuple(holder[key])
    return data


def _warning_filter_matches(pattern: Any, text: str) -> bool:
    if pattern is None:
        return True
    if hasattr(pattern, "match"):
        return bool(pattern.match(text))
    return re.match(str(pattern), text) is not None


def _is_yaml_deprecated_bool_warning_enabled() -> bool:
    for action, message, category, module, _ in warnings.filters:
        if not issubclass(DeprecationWarning, category):
            continue
        if not _warning_filter_matches(message, _YAML_DEPRECATED_BOOL_WARNING_MESSAGE):
            continue
        if not _warning_filter_matches(module, _YAML_DEPRECATED_BOOL_WARNING_MODULE):
            continue
        return action != "ignore"
    return True


def _plain_deprecated_bool_offsets(text: str) -> set[int]:
    """Return the offsets of the unquoted scalars in ``text`` that spell a YAML 1.1 boolean."""
    import yaml  # noqa: PLC0415

    offsets: set[int] = set()
    with contextlib.suppress(yaml.YAMLError):
        offsets.update(
            token.start_mark.index
            for token in yaml.scan(text, Loader=yaml.SafeLoader)
            if isinstance(token, yaml.ScalarToken) and token.plain and token.value in _YAML_DEPRECATED_BOOL_VALUES
        )
    return offsets


def warn_yaml_deprecated_bool_values(text: str) -> None:
    """Warn for YAML 1.1-style boolean scalars when ryaml is used.

    Only unquoted scalars warn: a quoted string or a block scalar line such as ``enabled: True`` holds text.
    """
    if not _is_yaml_deprecated_bool_warning_enabled() or not any(
        value in text for value in _YAML_DEPRECATED_BOOL_VALUES
    ):
        return

    matches = list(_YAML_DEPRECATED_BOOL_LINE_PATTERN.finditer(text))
    if not matches:
        return
    offsets = _plain_deprecated_bool_offsets(text)
    for match in matches:
        if match.start(1) not in offsets:
            continue
        warnings.warn(
            f"YAML bool '{match.group(1)}' is deprecated. Use lowercase 'true' or 'false' instead. "
            f"In a future version, only lowercase booleans will be recognized.",
            DeprecationWarning,
            stacklevel=3,
        )


def _iter_yaml_nodes(node: Any) -> Iterator[Any]:
    import yaml  # noqa: PLC0415

    yield node
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            yield from _iter_yaml_nodes(key_node)
            yield from _iter_yaml_nodes(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item_node in node.value:
            yield from _iter_yaml_nodes(item_node)


def reject_unsupported_yaml_tags(text: str) -> None:
    """Reject YAML tags that cannot be represented by JSON-compatible sample data."""
    if not any(marker in text for marker in _YAML_UNSUPPORTED_TAG_MARKERS) and not _YAML_TAG_DIRECTIVE_PATTERN.search(
        text
    ):
        return

    import yaml  # noqa: PLC0415

    node = read_yaml_text(yaml.compose, text)
    if node is None:
        return
    for yaml_node in _iter_yaml_nodes(node):
        if yaml_node.tag in _YAML_UNSUPPORTED_TAGS:
            msg = f"Unsupported YAML tag: {yaml_node.tag}"
            raise yaml.YAMLError(msg)


_YAML_DEPRECATED_BOOLS_READ: Final[ContextVar[list[str] | None]] = ContextVar(
    "yaml_deprecated_bools_read", default=None
)


@contextlib.contextmanager
def yaml_deprecated_bools_recorded() -> Generator[list[str]]:
    """Record the details of each deprecated YAML bool that PyYAML reads in the block, as it warns for them."""
    recorded: list[str] = []
    with context_values({_YAML_DEPRECATED_BOOLS_READ: recorded}):
        yield recorded


def warn_deprecated_yaml_bools(details: Iterable[str]) -> None:
    """Warn for deprecated YAML bools that PyYAML read, by their details (``yaml_deprecated_bools_recorded``)."""
    for detail in details:
        warn_deprecated("config.yaml-non-lowercase-bool", details=detail)


def _construct_yaml_bool_with_warning(loader: Any, node: Any) -> bool:
    value = loader.construct_scalar(node)
    if value in _YAML_DEPRECATED_BOOL_VALUES:
        from datamodel_code_generator.python_literal import stable_repr  # noqa: PLC0415

        details = (
            f"YAML bool {stable_repr(value)} is deprecated. In a future version, only lowercase booleans will be "
            "recognized."
        )
        if (recorded := _YAML_DEPRECATED_BOOLS_READ.get()) is not None:
            recorded.append(details)
        warn_deprecated_yaml_bools((details,))
    return value in {"true", "True", "TRUE"}


def _fast_construct_document(loader: Any, root: Any) -> Any:  # noqa: PLR0914, PLR0915
    """Construct a composed node tree in PyYAML's breadth-first order without per-node dispatch.

    Behaviourally identical to ``BaseConstructor.construct_document`` for SafeLoader-family
    loaders: PyYAML's ``constructed_objects`` memo and ``state_generators`` queue are shared,
    so aliases, recursive structures, merge keys, explicit tags and error cases take the
    standard path in the same order. Only plain map / seq / str handling is inlined.
    """
    from collections import deque  # noqa: PLC0415
    from collections.abc import Hashable  # noqa: PLC0415
    from types import GeneratorType  # noqa: PLC0415

    from yaml.constructor import ConstructorError, SafeConstructor  # noqa: PLC0415
    from yaml.nodes import MappingNode, ScalarNode, SequenceNode  # noqa: PLC0415

    map_tag = "tag:yaml.org,2002:map"
    seq_tag = "tag:yaml.org,2002:seq"
    str_tag = "tag:yaml.org,2002:str"
    generator_marker = Sentinel("generator_marker")

    constructors = loader.yaml_constructors
    plain_map = constructors.get(map_tag) is SafeConstructor.construct_yaml_map
    plain_seq = constructors.get(seq_tag) is SafeConstructor.construct_yaml_seq
    plain_str = constructors.get(str_tag) is SafeConstructor.construct_yaml_str
    flatten_mapping = loader.flatten_mapping
    construct_object = loader.construct_object
    memo = loader.constructed_objects
    state_generators = loader.state_generators
    queue: deque[tuple[Any, Any]] = deque()

    def delegate(node: Any) -> Any:
        """Use standard PyYAML construction and retain its breadth-first deferred work."""
        value = construct_object(node)
        while state_generators:
            queue.append((generator_marker, state_generators.pop(0)))
        return value

    def container_for(node: Any) -> Any:
        if (existing := memo.get(node)) is not None:
            return existing

        node_class = node.__class__
        match node_class:
            case _ if node_class is MappingNode and node.tag == map_tag and plain_map:
                value: Any = {}
            case _ if node_class is SequenceNode and node.tag == seq_tag and plain_seq:
                value = []
            case _:
                return delegate(node)

        memo[node] = value
        queue.append((node, value))
        return value

    def scalar(node: Any) -> Any:
        tag = node.tag
        if tag == str_tag and plain_str:
            return node.value
        constructor = constructors.get(tag)
        if constructor is None:
            return delegate(node)
        value = constructor(loader, node)
        if value.__class__ is GeneratorType:
            value.close()
            return delegate(node)
        return value

    result = scalar(root) if root.__class__ is ScalarNode else container_for(root)
    while queue:
        node, container = queue.popleft()
        if node is generator_marker:
            for _ in container:
                pass
            while state_generators:
                queue.append((generator_marker, state_generators.pop(0)))
            continue
        if node.__class__ is MappingNode:
            flatten_mapping(node)
            for key_node, value_node in node.value:
                if key_node.__class__ is ScalarNode and key_node.tag == str_tag and plain_str:
                    key = key_node.value
                else:
                    key = scalar(key_node) if key_node.__class__ is ScalarNode else delegate(key_node)
                    if not isinstance(key, Hashable):
                        raise ConstructorError(  # noqa: TRY003
                            "while constructing a mapping",  # noqa: EM101
                            node.start_mark,
                            "found unhashable key",
                            key_node.start_mark,
                        )
                container[key] = scalar(value_node) if value_node.__class__ is ScalarNode else container_for(value_node)
        else:
            append = container.append
            for item in node.value:
                append(scalar(item) if item.__class__ is ScalarNode else container_for(item))
    return result


def _custom_safe_loader(base: Any) -> type:
    """Return ``base`` with YAML 1.2 bool handling, timestamps as strings and exponents without a point.

    A plain ``=`` is a string, as in YAML 1.2: the YAML 1.1 ``value`` type it would resolve to has no constructor. A
    number with ``_`` or ``:`` reads as the generation run reads it (``yaml_number_reading``): the number PyYAML reads,
    by default.
    """

    class CustomSafeLoader(base):
        """SafeLoader with YAML 1.2 bool handling and timestamp-as-string."""

        yaml_constructors = base.yaml_constructors.copy()
        yaml_implicit_resolvers = {  # noqa: RUF012
            k: v
            for k, v in (
                (k, [(tag, pat) for tag, pat in v if tag not in _YAML_1_1_RESOLVER_TAGS])
                for k, v in base.yaml_implicit_resolvers.items()
            )
            if v
        }

        def construct_document(self, node: Any) -> Any:
            if not hasattr(self, "state_generators"):  # pragma: no cover  # PyYAML internals changed
                return super().construct_document(node)
            data = _fast_construct_document(self, node)
            while self.state_generators:  # pragma: no cover  # normally empty; mirrors BaseConstructor
                generators = self.state_generators
                self.state_generators = []
                for generator in generators:
                    for _ in generator:
                        pass
            self.constructed_objects = {}
            self.recursive_objects = {}
            self.deep_construct = False
            return data

    CustomSafeLoader.yaml_constructors["tag:yaml.org,2002:timestamp"] = CustomSafeLoader.yaml_constructors[
        "tag:yaml.org,2002:str"
    ]
    for key in ["t", "f", "T", "F"]:
        CustomSafeLoader.yaml_implicit_resolvers.setdefault(key, []).append((
            "tag:yaml.org,2002:bool",
            _YAML_1_2_BOOL_PATTERN,
        ))
    CustomSafeLoader.yaml_constructors["tag:yaml.org,2002:bool"] = _construct_yaml_bool_with_warning

    # Add scientific notation without decimal point (e.g., 1e-5) as float
    for key in ["-", "+", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
        CustomSafeLoader.yaml_implicit_resolvers.setdefault(key, []).append((
            "tag:yaml.org,2002:float",
            _YAML_SCIENTIFIC_NOTATION_PATTERN,
        ))

    # A number that YAML 1.2 reads as text takes the tag of its kind first, in PyYAML's order; it reads as the run says.
    for resolvers in CustomSafeLoader.yaml_implicit_resolvers.values():
        resolvers[:0] = [
            (_YAML_1_1_NUMBER_TAGS[tag], re.compile(_YAML_1_1_NUMBER_LOOKAHEAD + pattern.pattern, pattern.flags))
            for tag, pattern in resolvers
            if tag in _YAML_1_1_NUMBER_TAGS
        ]
    for tag, number_tag in _YAML_1_1_NUMBER_TAGS.items():
        CustomSafeLoader.yaml_constructors[number_tag] = partial(
            _construct_yaml_1_1_number, CustomSafeLoader.yaml_constructors[tag]
        )

    return CustomSafeLoader


@lru_cache(maxsize=1)
def get_pure_safe_loader() -> type:
    """Get the customized SafeLoader on PyYAML's own scanner, which reads tabs as libyaml does, lazily.

    Tabs separate tokens on a line except before a simple key in block context, and stand between the words of a
    plain scalar, where a tab left of the scalar's indentation on a continuation line is an error. PyYAML's scanner
    reads spaces alone there, so a document that libyaml reads would not be read without it.
    """
    import yaml  # noqa: PLC0415
    from yaml.scanner import ScannerError  # noqa: PLC0415

    def at_document_marker(scanner: Any) -> bool:
        return scanner.prefix(3) in {"---", "..."} and scanner.peek(3) in _YAML_TOKEN_ENDS

    class TabReadingSafeLoader(yaml.SafeLoader):
        @override
        def scan_to_next_token(self) -> None:
            if self.index == 0 and self.peek() == "\ufeff":
                self.forward()
            while True:
                while (character := self.peek()) == " " or (
                    character == "\t" and (self.flow_level or not self.allow_simple_key)
                ):
                    self.forward()
                if self.peek() == "#":
                    while self.peek() not in _YAML_LINE_ENDS:
                        self.forward()
                if not self.scan_line_break():
                    return
                if not self.flow_level:
                    self.allow_simple_key = True

        @override
        def scan_plain_spaces(self, indent: int, start_mark: Any) -> list[str] | None:
            length = 0
            while self.peek(length) in _YAML_BLANKS:
                length += 1
            whitespaces = self.prefix(length)
            self.forward(length)
            if self.peek() not in _YAML_LINE_BREAKS:
                return [whitespaces] if whitespaces else []
            line_break = self.scan_line_break()
            self.allow_simple_key = True
            if at_document_marker(self):
                return None
            breaks: list[str] = []
            while (character := self.peek()) in _YAML_BLANKS or character in _YAML_LINE_BREAKS:
                if character in _YAML_LINE_BREAKS:
                    breaks.append(self.scan_line_break())
                    if at_document_marker(self):
                        return None
                    continue
                if character == "\t" and self.column < indent:
                    msg = "while scanning a plain scalar"
                    raise ScannerError(
                        msg,
                        start_mark,
                        "found a tab character that violates indentation",
                        self.get_mark(),
                    )
                self.forward()
            if line_break != "\n":
                return [line_break, *breaks]
            return breaks or [" "]

    return _custom_safe_loader(TabReadingSafeLoader)


@lru_cache(maxsize=1)
def get_safe_loader() -> type:
    """Get customized SafeLoader lazily: on libyaml where PyYAML has it, else on PyYAML's own scanner."""
    try:
        from yaml import CSafeLoader  # noqa: PLC0415
    except ImportError:
        return get_pure_safe_loader()
    return _custom_safe_loader(CSafeLoader)


def read_yaml_text(read: Callable[..., _T], text: str) -> _T:
    """Read YAML text with ``read`` (``yaml.load`` or ``yaml.compose``), so that every install reads it alike.

    libyaml, where PyYAML has it, reads first; a document it cannot read, such as one with a tab inside a block
    scalar, is read on PyYAML's own scanner (``get_pure_safe_loader``), whose error is raised as on an install without
    libyaml. Both read every document they both accept to the same data.
    """
    import yaml  # noqa: PLC0415

    loader = get_safe_loader()
    try:
        return read(text, Loader=loader)
    except yaml.YAMLError as error:
        if isinstance(error, yaml.constructor.ConstructorError) or (pure := get_pure_safe_loader()) is loader:
            raise
    return read(text, Loader=pure)


YamlBackend = Literal["ryaml", "pyyaml"]


@lru_cache(maxsize=1)
def get_yaml_backend() -> YamlBackend:
    """Detect the available YAML backend ('ryaml' or 'pyyaml')."""
    try:
        import ryaml  # noqa: PLC0415, F401  # ty: ignore[import-not-found]
    except ImportError:
        return "pyyaml"
    else:
        return "ryaml"


@lru_cache(maxsize=1)
def get_yaml_parse_errors() -> tuple[type[Exception], ...]:
    """Return YAML parse error types for both backends."""
    import yaml  # noqa: PLC0415

    errors: list[type[Exception]] = [yaml.YAMLError]
    try:
        import ryaml  # noqa: PLC0415  # ty: ignore[import-not-found]

        errors.append(ryaml.InvalidYamlError)
    except ImportError:
        pass
    return tuple(errors)


@lru_cache(maxsize=1)
def _get_base_model_class() -> type:
    """Get BaseModel class with strict=False config lazily."""
    from pydantic import BaseModel as _PydanticBaseModel  # noqa: PLC0415
    from pydantic import ConfigDict as _ConfigDict  # noqa: PLC0415

    class _BaseModelV2(_PydanticBaseModel):
        model_config = _ConfigDict(strict=False, defer_build=True)

    return _BaseModelV2


def create_module_getattr(
    module_name: str,
    lazy_imports: dict[str, tuple[str, str]],
) -> Callable[[str], Any]:
    """Create a __getattr__ function for lazy module imports.

    Args:
        module_name: The name of the module (typically __name__).
        lazy_imports: Mapping of attribute name to (module_path, attribute_name).

    Returns:
        A __getattr__ function that lazily imports the specified attributes.

    Example:
        __getattr__ = create_module_getattr(__name__, {
            "MyClass": ("mypackage.mymodule", "MyClass"),
        })
    """
    return _module_hooks(module_name, lazy_imports)[0]


_LAZY_EXPORTS = {"BaseModel": _get_base_model_class, "SafeLoader": get_safe_loader}
__getattr__, __dir__ = _module_hooks(__name__, _LAZY_EXPORTS)


_UNDER_SCORE_1: re.Pattern[str] = re.compile(r"([^_])([A-Z][a-z]+)")
_UNDER_SCORE_2: re.Pattern[str] = re.compile(r"([a-z0-9])([A-Z])")


@lru_cache
def camel_to_snake(string: str, target_python_version: PythonVersion | None = None) -> str:
    """Convert camelCase or PascalCase to snake_case, lowercasing by the case rules of the target Python version."""
    from datamodel_code_generator._target_unicode import lower  # noqa: PLC0415

    subbed = _UNDER_SCORE_1.sub(r"\1_\2", string)
    return lower(_UNDER_SCORE_2.sub(r"\1_\2", subbed), target_python_version)


def last_camel_word(string: str) -> str:
    """Return the last word of a camelCase, PascalCase or snake_case name, split where ``camel_to_snake`` splits it."""
    marked = _UNDER_SCORE_2.sub("\\1\x00\\2", _UNDER_SCORE_1.sub("\\1\x00\\2", string))
    return re.split(r"[\x00_]", marked)[-1]
