"""Neutral source decoding and process-local parsed-source caching."""

from __future__ import annotations

import contextlib
import sys
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.util import source_from_cache
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, TextIO, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

# Pydantic 2.5 cannot build schemas from stdlib TypeAliasType on Python 3.12.
if sys.version_info >= (3, 14):
    from typing import TypeAliasType
else:
    from typing_extensions import TypeAliasType

YamlValue = TypeAliasType(
    "YamlValue",
    "dict[str, YamlValue] | list[YamlValue] | str | int | float | bool | None",
)

_IGNORED_TEXT_PREFIX_CHARS: frozenset[str] = frozenset({"\ufeff", " ", "\t", "\r", "\n"})
_PROTOBUF_TRIVIA = r"(?:\s|//[^\r\n]*(?=[\r\n]|$)|/\*[^*]*(?:\*(?!/)[^*]*)*\*/)*"
_PROTOBUF_DECLARATION_PATTERN = (
    rf"\ufeff?{_PROTOBUF_TRIVIA}(?:syntax{_PROTOBUF_TRIVIA}={_PROTOBUF_TRIVIA}"
    rf"(?P<syntax_quote>['\"])proto[23](?P=syntax_quote)|"
    rf"edition{_PROTOBUF_TRIVIA}={_PROTOBUF_TRIVIA}"
    rf"(?P<edition_quote>['\"])2023(?P=edition_quote)){_PROTOBUF_TRIVIA};"
)
_PARSER_SOURCE_DATA_CACHE_MAX_SIZE = 128
_ParserSourceDataCacheKey: TypeAlias = tuple[Path, str, str, str]
_ParserSourceDataSeenKey: TypeAlias = tuple[Path, str]
# Serialized snapshots isolate mutable callers and are faster to restore than reparsing source text.
_parser_source_data_cache: OrderedDict[_ParserSourceDataCacheKey, bytes] = OrderedDict()
_parser_source_data_seen_keys: OrderedDict[_ParserSourceDataSeenKey, None] = OrderedDict()
_parser_source_data_cache_lock = RLock()
_parsed_source_cache_enable_count = 0
_enable_parsed_source_cache = False


@dataclass(frozen=True, slots=True)
class DirectoryInputFilter:
    """Exclude configured artifacts without excluding schemas by their suffix."""

    files: frozenset[Path]
    output_directory: Path | None
    headers: tuple[str, ...]

    def _is_generated(self, text: str) -> bool:
        """Require Python output after a header that a schema may also share."""
        for header in self.headers:
            if not text.startswith(header):
                continue
            start = len(header)
            while start < len(text):
                if text[start].isspace():
                    start += 1
                elif text[start] == "#":
                    if (line_end := text.find("\n", start)) < 0:
                        return True
                    start = line_end + 1
                else:
                    break
            if start == len(text):
                return True
            line_end = text.find("\n", start)
            line = text[start : None if line_end < 0 else line_end]
            target, assignment, _ = line.partition("=")
            if not (
                line.startswith(("from ", "import ", "class ", "@"))
                or (assignment and target.removeprefix("type ").strip().isidentifier())
            ):
                continue
            if ":" not in line:
                return True

            from datamodel_code_generator.util import get_yaml_parse_errors  # noqa: PLC0415

            # A Python-looking statement can still be a YAML mapping key.
            # Prefer the existing input interpretation in this ambiguous case.
            try:
                return not isinstance(load_yaml(text), (dict, list))
            except get_yaml_parse_errors():
                return True
        return False

    def iter_files(self, paths: Iterable[Path], encoding: str) -> Iterator[tuple[Path, bytes | None]]:
        """Retain candidate bytes so recognizing an artifact never rereads a schema."""
        for path in paths:
            if not path.is_file() or path in self.files:
                continue
            data = None
            if self.output_directory is not None and path.is_relative_to(self.output_directory):
                source_path = path
                if path.suffix == ".pyc" and path.parent.name == "__pycache__":
                    with contextlib.suppress(ValueError):
                        source_path = Path(source_from_cache(str(path)))
                if source_path.suffix == ".py" and (source_path == path or source_path.is_file()):
                    source_data = source_path.read_bytes()
                    text = source_data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
                    if self._is_generated(text) or (source_path.name == "__init__.py" and not text.strip()):
                        continue
                    if source_path == path:
                        data = source_data
            yield path, data


def enable_parsed_source_cache() -> Callable[[], None]:
    """Enable the process-local parsed source cache and return a restore callback."""
    global _enable_parsed_source_cache, _parsed_source_cache_enable_count  # noqa: PLW0603

    with _parser_source_data_cache_lock:
        _parsed_source_cache_enable_count += 1
        _enable_parsed_source_cache = True
    restored = False

    def restore() -> None:
        nonlocal restored
        global _enable_parsed_source_cache, _parsed_source_cache_enable_count  # noqa: PLW0603

        with _parser_source_data_cache_lock:
            if restored:
                return
            restored = True
            _parsed_source_cache_enable_count -= 1
            _enable_parsed_source_cache = _parsed_source_cache_enable_count > 0

    return restore


def _is_parsed_source_cache_enabled() -> bool:
    return _enable_parsed_source_cache


def load_yaml(stream: str | TextIO) -> YamlValue:
    """Load YAML content using ryaml (if available) or PyYAML.

    PyYAML reads a document that ryaml cannot, such as one holding an integer outside 64 bits; when PyYAML cannot
    read it either, such as an integer beyond Python's digit limit or nesting beyond the stack, ryaml's error is raised.
    """
    from datamodel_code_generator.util import get_yaml_backend, reject_unsupported_yaml_tags  # noqa: PLC0415

    text = stream if isinstance(stream, str) else stream.read()
    reject_unsupported_yaml_tags(text)

    if get_yaml_backend() == "ryaml":
        import ryaml  # noqa: PLC0415  # ty: ignore[unresolved-import]

        from datamodel_code_generator.util import warn_yaml_deprecated_bool_values  # noqa: PLC0415

        warn_yaml_deprecated_bool_values(text)
        try:
            return ryaml.loads(text)
        except ryaml.InvalidYamlError:
            import yaml  # noqa: PLC0415

            with contextlib.suppress(yaml.YAMLError, ValueError, RecursionError):
                return _load_pyyaml(text)
            raise
    return _load_pyyaml(text)


def _load_pyyaml(text: str) -> YamlValue:
    """Load YAML content with PyYAML."""
    import yaml  # noqa: PLC0415

    from datamodel_code_generator.util import SafeLoader  # noqa: PLC0415

    return yaml.load(text, Loader=SafeLoader)  # noqa: S506


def load_yaml_dict(stream: str | TextIO) -> dict[str, YamlValue]:
    """Load YAML and return as dict. Raises TypeError if result is not a dict."""
    result = load_yaml(stream)
    if not isinstance(result, dict):
        msg = f"Expected dict, got {type(result).__name__}"
        raise TypeError(msg)
    return result


def load_yaml_dict_from_path(path: Path, encoding: str) -> dict[str, YamlValue]:
    """Load a YAML mapping from a path, caching by path and modification time."""
    from datamodel_code_generator.util import record_watch_dependency  # noqa: PLC0415

    record_watch_dependency(path)
    cache_path = path if path.is_absolute() else path.absolute()
    return _load_yaml_dict_from_path_cached(cache_path, cache_path.stat().st_mtime, encoding)


@lru_cache(maxsize=128)
def _load_yaml_dict_from_path_cached(
    path: Path,
    mtime: float,  # noqa: ARG001  # Used as cache key for invalidation
    encoding: str,
) -> dict[str, YamlValue]:
    """Load a YAML mapping from a path after cache-key normalization."""
    with path.open(encoding=encoding) as stream:
        return load_yaml_dict(stream)


def _first_significant_text_char(text: str) -> str | None:
    for char in text:
        if char not in _IGNORED_TEXT_PREFIX_CHARS:
            return char
    return None


def _is_json_text(text: str) -> bool:
    """Return whether text starts like JSON after whitespace and BOM."""
    return _first_significant_text_char(text) in {"{", "["}


def _is_xml_text(text: str) -> bool:
    """Return whether text starts like XML after whitespace and BOM."""
    return _first_significant_text_char(text) == "<"


def _has_protobuf_declaration(text: str) -> bool:
    """Recognize an explicit Protobuf declaration before attempting YAML decoding."""
    if _first_significant_text_char(text) not in {"s", "e", "/"}:
        return False
    import re  # noqa: PLC0415

    return re.match(_PROTOBUF_DECLARATION_PATTERN, text) is not None


def _is_protobuf_text(text: str) -> bool:
    """Return whether text contains a leading Protocol Buffers declaration."""
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped or stripped.startswith("//"):
            continue
        if stripped.startswith("syntax"):
            return '"proto2"' in stripped or '"proto3"' in stripped
        if stripped.startswith("edition"):
            return '"2023"' in stripped
        if stripped.startswith(("package ", "import ", "message ", "enum ", "service ")):
            return True
    return False


def load_data(text: str) -> dict[str, YamlValue]:
    """Load text as a JSON or YAML mapping based on its content."""
    import json  # noqa: PLC0415

    if _is_json_text(text):
        with contextlib.suppress(json.JSONDecodeError):
            if isinstance(result := json.loads(text), dict):
                return result
    return load_yaml_dict(text)


def load_data_from_path(path: Path, encoding: str) -> dict[str, YamlValue]:
    """Load a JSON or YAML mapping from a path."""
    result = _load_parser_source_data_from_path(path, encoding)
    if isinstance(result, dict):
        return result
    msg = f"Expected dict, got {type(result).__name__}"
    raise TypeError(msg)


def _load_parser_source_data_from_path(path: Path, encoding: str) -> YamlValue:
    return _read_parser_source_data_from_path(path, encoding)[1]


def _read_parser_source_data_from_path(
    path: Path, encoding: str, *, data: bytes | None = None
) -> tuple[bytes, YamlValue]:
    resolved_path = path.resolve()
    from datamodel_code_generator.util import record_watch_dependency  # noqa: PLC0415

    record_watch_dependency(resolved_path)
    if data is None:
        data = resolved_path.read_bytes()
    return data, _load_parser_source_data_from_path_bytes(resolved_path, data, encoding)


def _load_parser_source_data_from_path_bytes(resolved_path: Path, data: bytes, encoding: str) -> YamlValue:
    seen_key = (resolved_path, encoding)
    with _parser_source_data_cache_lock:
        use_cache = seen_key in _parser_source_data_seen_keys
        _parser_source_data_seen_keys[seen_key] = None
        _parser_source_data_seen_keys.move_to_end(seen_key)
        while len(_parser_source_data_seen_keys) > _PARSER_SOURCE_DATA_CACHE_MAX_SIZE:
            _parser_source_data_seen_keys.popitem(last=False)

    if not use_cache:
        return _load_parser_source_data_from_bytes(resolved_path, data, encoding)

    digest = sha256(data).hexdigest()
    return _load_parser_source_data_from_bytes_with_cache(resolved_path, data, digest, encoding)


def _load_cached_parser_source_data(cache_key: _ParserSourceDataCacheKey) -> YamlValue | None:
    with _parser_source_data_cache_lock:
        if (cached_data := _parser_source_data_cache.get(cache_key)) is None:
            return None
        _parser_source_data_cache.move_to_end(cache_key)

    import marshal  # noqa: PLC0415

    # Entries are process-local values emitted by marshal.dumps below, never external bytes.
    return marshal.loads(cached_data)  # noqa: S302


def _store_parser_source_data(cache_key: _ParserSourceDataCacheKey, parsed_data: YamlValue) -> YamlValue:
    import marshal  # noqa: PLC0415

    try:
        cached_data = marshal.dumps(parsed_data)
    except Exception:  # noqa: BLE001
        # Values outside the primitive YamlValue shape remain valid uncached input.
        return parsed_data
    with _parser_source_data_cache_lock:
        _parser_source_data_cache[cache_key] = cached_data
        _parser_source_data_cache.move_to_end(cache_key)
        while len(_parser_source_data_cache) > _PARSER_SOURCE_DATA_CACHE_MAX_SIZE:
            _parser_source_data_cache.popitem(last=False)
    return parsed_data


def _load_parser_source_data_from_bytes_with_cache(
    path: Path,
    data: bytes,
    digest: str,
    encoding: str,
) -> YamlValue:
    import json  # noqa: PLC0415

    text = data.decode(encoding)
    if path.suffix.lower() == ".json":
        cache_key = (path, digest, encoding, "json")
        if (cached_data := _load_cached_parser_source_data(cache_key)) is not None:
            return cached_data

        with contextlib.suppress(json.JSONDecodeError):
            return _store_parser_source_data(cache_key, json.loads(text))

    from datamodel_code_generator.util import get_yaml_backend  # noqa: PLC0415

    parser_backend = f"yaml:{get_yaml_backend()}"
    cache_key = (path, digest, encoding, parser_backend)
    if (cached_data := _load_cached_parser_source_data(cache_key)) is not None:
        return cached_data

    return _store_parser_source_data(cache_key, load_yaml(text))


def _load_parser_source_data_from_bytes(path: Path, data: bytes, encoding: str) -> YamlValue:
    import json  # noqa: PLC0415

    text = data.decode(encoding)
    if path.suffix.lower() == ".json":
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(text)

    return load_yaml(text)


def _clear_parser_source_data_cache() -> None:
    with _parser_source_data_cache_lock:
        _parser_source_data_cache.clear()
        _parser_source_data_seen_keys.clear()
