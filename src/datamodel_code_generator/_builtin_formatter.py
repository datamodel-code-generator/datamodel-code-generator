"""Dependency-free formatter for generated Python code."""

from __future__ import annotations

import ast
import re
import sys
import tokenize
from collections import defaultdict
from enum import IntEnum
from io import StringIO
from typing import TYPE_CHECKING, Any, TypeGuard, cast

from datamodel_code_generator.util import load_toml

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from pathlib import Path
    from typing import Protocol

    class PythonVersion(Protocol):
        @property
        def version_key(self) -> tuple[int, int]:
            raise NotImplementedError

    class _SourceLocationNode(Protocol):
        lineno: int
        end_lineno: int | None
        col_offset: int
        end_col_offset: int | None


class _AliasSortCategory(IntEnum):
    CONSTANT = 0
    CLASS = 1
    OTHER = 2


class _ImportCategory(IntEnum):
    FUTURE = 0
    STANDARD_LIBRARY = 1
    THIRD_PARTY = 2
    FIRST_PARTY = 3
    LOCAL = 4


DEFAULT_LINE_LENGTH = 88
DEFAULT_KNOWN_FIRST_PARTY = frozenset({"datamodel_code_generator", "tests"})
MAX_TOP_LEVEL_BLANK_LINES = 2
MAX_SHORT_DEFAULT_OVERFLOW = 13
LONG_TARGET_PREFIX_LENGTH = 30
TYPE_ALIAS_INLINE_ARGUMENT_COUNT = 2
STRING_PREFIX_PATTERN = re.compile(r"(?i)^([rubf]*)(\"\"\"|'''|\"|')")
PEP695_TYPE_ALIAS_START_PATTERN = re.compile(r"^(?P<indent>\s*)type\s+(?P<target>[A-Za-z_]\w*(?:\[.*?\])?)\s*=")
PEP695_TYPE_ALIAS_PLACEHOLDER = "__datamodel_codegen_builtin_type_alias__"
_SOURCE_LINES_CACHE: list[tuple[str, list[str]]] = []


def _split_python_lines(source: str) -> list[str]:
    """Split physical Python lines while preserving Unicode separators."""
    if not source:
        return []
    if "\r" in source:
        source = source.replace("\r\n", "\n").replace("\r", "\n")

    lines = source.split("\n")
    if lines[-1]:
        return lines
    lines.pop()
    return lines


def _is_valid_builtin_line_length(line_length: Any) -> TypeGuard[int]:
    return isinstance(line_length, int) and not isinstance(line_length, bool) and line_length > 0


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _indent_first_line(lines: Sequence[str], indent: str) -> Iterator[str]:
    return (f"{indent}{line}" if index == 0 else line for index, line in enumerate(lines))


def _find_pyproject_toml(settings_path: Path) -> Path | None:
    for path in (settings_path, *settings_path.parents):
        pyproject_toml = path / "pyproject.toml"
        if pyproject_toml.is_file():
            return pyproject_toml
    return None


def _get_builtin_line_length(
    settings_path: Path,
    explicit_line_length: int | None = None,
    tool_config: Mapping[str, Any] | None = None,
) -> int:
    if explicit_line_length is not None:
        if _is_valid_builtin_line_length(explicit_line_length):
            return explicit_line_length
        msg = "builtin_format_line_length must be a positive integer"
        raise ValueError(msg)

    if tool_config is None:
        if (pyproject_toml_path := _find_pyproject_toml(settings_path)) is None:
            return DEFAULT_LINE_LENGTH
        tool_config = load_toml(pyproject_toml_path).get("tool", {})

    datamodel_codegen_config = tool_config.get("datamodel-codegen", {})
    ruff_config = tool_config.get("ruff", {})
    black_config = tool_config.get("black", {})
    isort_config = tool_config.get("isort", {})
    for line_length in (
        datamodel_codegen_config.get("builtin-format-line-length"),
        datamodel_codegen_config.get("builtin_format_line_length"),
        ruff_config.get("line-length"),
        black_config.get("line-length"),
        isort_config.get("line_length"),
    ):
        if _is_valid_builtin_line_length(line_length):
            return line_length
    return DEFAULT_LINE_LENGTH


def _get_builtin_known_first_party(
    settings_path: Path,
    tool_config: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    if tool_config is None:
        if (pyproject_toml_path := _find_pyproject_toml(settings_path)) is None:
            return DEFAULT_KNOWN_FIRST_PARTY
        tool_config = load_toml(pyproject_toml_path).get("tool", {})

    isort_config = tool_config.get("isort", {})
    known_first_party = isort_config.get("known_first_party", [])
    if not isinstance(known_first_party, list):
        return DEFAULT_KNOWN_FIRST_PARTY
    return DEFAULT_KNOWN_FIRST_PARTY | frozenset(item for item in known_first_party if isinstance(item, str))


def _get_builtin_string_normalization(
    settings_path: Path,
    *,
    skip_string_normalization: bool,
    tool_config: Mapping[str, Any] | None = None,
) -> bool:
    if not skip_string_normalization:
        return True

    if tool_config is None:
        if (pyproject_toml_path := _find_pyproject_toml(settings_path)) is None:
            return False
        tool_config = load_toml(pyproject_toml_path).get("tool", {})

    black_config = tool_config.get("black", {})
    skip_black_string_normalization = black_config.get("skip-string-normalization")
    if isinstance(skip_black_string_normalization, bool):
        return not skip_black_string_normalization
    return False


def _format_alias(alias: ast.alias) -> str:
    if alias.asname:
        return f"{alias.name} as {alias.asname}"
    return alias.name


def _alias_imported_name(alias: str) -> str:
    if " as " in alias:
        return alias.split(" as ", 1)[0]
    return alias


def _alias_sort_key(alias: str) -> tuple[_AliasSortCategory, str]:
    name = _alias_imported_name(alias)
    if name.isupper():
        category = _AliasSortCategory.CONSTANT
    elif name[:1].isupper():
        category = _AliasSortCategory.CLASS
    else:
        category = _AliasSortCategory.OTHER
    return category, name.lower()


def _format_from_import(module: str, aliases: list[str], line_length: int) -> str:
    line = f"from {module} import {', '.join(aliases)}"
    if len(line) <= line_length and "*" not in aliases:
        return line
    imports = "\n".join(f"    {alias}," for alias in aliases)
    return f"from {module} import (\n{imports}\n)"


def _import_category(module: str, level: int, known_first_party: frozenset[str]) -> _ImportCategory:
    if module == "__future__":
        return _ImportCategory.FUTURE
    if level:
        return _ImportCategory.LOCAL
    top_level_module = module.split(".", 1)[0]
    if top_level_module in sys.stdlib_module_names:
        return _ImportCategory.STANDARD_LIBRARY
    if top_level_module in known_first_party:
        return _ImportCategory.FIRST_PARTY
    return _ImportCategory.THIRD_PARTY


def _has_inline_comment(lines: list[str], node: ast.AST) -> bool:
    lineno = getattr(node, "lineno", 1)
    end_lineno = getattr(node, "end_lineno", None) or lineno
    return any(_has_comment_token(line) for line in lines[lineno - 1 : end_lineno])


def _has_comment_token(line: str) -> bool:
    if "#" not in line:
        return False
    try:
        tokens = tokenize.generate_tokens(StringIO(line).readline)
        return any(token.type == tokenize.COMMENT for token in tokens)
    except tokenize.TokenError:
        return "#" in line


def _needs_pep695_type_alias_placeholders(python_version: PythonVersion | None) -> bool:
    return (
        python_version is not None
        and python_version.version_key >= (3, 12)
        and (3, 10) <= sys.version_info[:2] < (3, 12)
    )


def _pep695_type_alias_statement_end(lines: list[str], start_index: int) -> int:
    bracket_depth = 0
    try:
        tokens = tokenize.generate_tokens(StringIO("\n".join(lines[start_index:])).readline)
        for token in tokens:
            if token.type == tokenize.OP:
                if token.string in {"(", "[", "{"}:
                    bracket_depth += 1
                elif token.string in {")", "]", "}"}:
                    bracket_depth -= 1
            if token.type == tokenize.NEWLINE and bracket_depth <= 0:
                return start_index + token.end[0] - 1
    except tokenize.TokenError:
        return start_index
    return start_index


def _replace_pep695_type_aliases_with_placeholders(code: str) -> str:
    lines = _split_python_lines(code)
    placeholder_lines = list(lines)
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if not PEP695_TYPE_ALIAS_START_PATTERN.match(line):
            line_index += 1
            continue

        indent = _line_indent(line)
        end_index = _pep695_type_alias_statement_end(lines, line_index)
        placeholder_lines[line_index] = f"{indent}{PEP695_TYPE_ALIAS_PLACEHOLDER} = None"
        for continuation_index in range(line_index + 1, end_index + 1):
            placeholder_lines[continuation_index] = ""
        line_index = end_index + 1
    return "\n".join(placeholder_lines)


def _parse_builtin_code_with_source_validity(
    code: str,
    python_version: PythonVersion | None,
) -> tuple[ast.Module | None, bool]:
    feature_version = python_version.version_key if python_version is not None else None
    try:
        return ast.parse(code, feature_version=feature_version), True
    except SyntaxError:
        if not _needs_pep695_type_alias_placeholders(python_version):
            return None, False

    placeholder_code = _replace_pep695_type_aliases_with_placeholders(code)
    if placeholder_code == code:
        return None, False
    try:
        return ast.parse(placeholder_code, feature_version=feature_version), False
    except SyntaxError:
        return None, False


def _parse_builtin_code(code: str, python_version: PythonVersion | None) -> ast.Module | None:
    return _parse_builtin_code_with_source_validity(code, python_version)[0]


def _format_import_node_without_reordering(
    node: ast.Import | ast.ImportFrom,
    lines: list[str],
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
) -> tuple[_ImportCategory, str]:
    end_lineno = node.end_lineno or node.lineno
    raw_import = "\n".join(lines[node.lineno - 1 : end_lineno])
    return _import_node_category(node, known_first_party), raw_import


def _import_node_category(node: ast.Import | ast.ImportFrom, known_first_party: frozenset[str]) -> _ImportCategory:
    match node:
        case ast.Import():
            return min(_import_category(alias.name, 0, known_first_party) for alias in node.names)
        case ast.ImportFrom():
            return _import_category(node.module or "", node.level, known_first_party)
    msg = f"Unsupported import node: {type(node).__name__}"
    raise TypeError(msg)


def _format_import_node(
    node: ast.Import | ast.ImportFrom,
    line_length: int,
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
) -> tuple[_ImportCategory, str]:
    category = _import_node_category(node, known_first_party)
    match node:
        case ast.Import():
            lines = [f"import {_format_alias(alias)}" for alias in sorted(node.names, key=lambda alias: alias.name)]
            return category, "\n".join(lines)
        case ast.ImportFrom():
            module = "." * node.level + (node.module or "")
            if any(alias.asname is not None for alias in node.names):
                lines = [
                    _format_from_import(module, [_format_alias(alias)], line_length)
                    for alias in sorted(node.names, key=lambda alias: _alias_sort_key(_format_alias(alias)))
                ]
                return category, "\n".join(lines)

            aliases = sorted((_format_alias(alias) for alias in node.names), key=_alias_sort_key)
            return category, _format_from_import(module, aliases, line_length)
    raise AssertionError  # pragma: no cover


def _from_import_key(node: ast.ImportFrom) -> tuple[int, str]:
    return node.level, node.module or ""


def _modules_with_aliased_imports(import_nodes: list[ast.Import | ast.ImportFrom]) -> set[tuple[int, str]]:
    return {
        _from_import_key(node)
        for node in import_nodes
        if isinstance(node, ast.ImportFrom) and any(alias.asname is not None for alias in node.names)
    }


def _can_merge_from_imports(node: ast.ImportFrom, aliased_modules: set[tuple[int, str]]) -> bool:
    return node.level == 0 and _from_import_key(node) not in aliased_modules


def _iter_aliased_from_import_lines(
    module: str,
    aliases: list[tuple[int, str, bool]],
    line_length: int,
) -> Iterator[str]:
    sorted_aliases = sorted(aliases, key=lambda item: _alias_sort_key(item[1]))
    chunk: list[str] = []
    chunk_group: int | None = None
    for group_index, alias, is_aliased in sorted_aliases:
        if is_aliased:
            if chunk:
                yield _format_from_import(module, chunk, line_length)
                chunk = []
                chunk_group = None
            yield _format_from_import(module, [alias], line_length)
            continue

        if chunk and chunk_group != group_index:
            yield _format_from_import(module, chunk, line_length)
            chunk = []
        chunk.append(alias)
        chunk_group = group_index

    if chunk:
        yield _format_from_import(module, chunk, line_length)


def _import_line_sort_key(line: str) -> tuple[int, int, str, int, str, str]:
    if not line.startswith("from "):
        return 0, 0, line.lower(), 0, "", line

    module, _, imported = line.removeprefix("from ").partition(" import ")
    relative_level = len(module) - len(module.lstrip("."))
    alias_category, alias_name = _alias_sort_key(imported.split(",", 1)[0].strip())
    if relative_level:
        return 1, -relative_level, module[relative_level:].lower(), int(alias_category), alias_name, line.lower()
    return 1, 0, module.lower(), int(alias_category), alias_name, line.lower()


def _build_builtin_import_block(
    import_nodes: list[ast.Import | ast.ImportFrom],
    line_length: int,
    lines: list[str],
    known_first_party: frozenset[str],
) -> str:
    categorized_lines: defaultdict[_ImportCategory, set[str]] = defaultdict(set)
    grouped_from_imports: defaultdict[tuple[_ImportCategory, int, str], set[str]] = defaultdict(set)
    aliased_from_imports: defaultdict[tuple[_ImportCategory, int, str], list[tuple[int, str, bool]]] = defaultdict(list)
    aliased_modules = _modules_with_aliased_imports(import_nodes)

    for node_index, node in enumerate(import_nodes):
        if _has_inline_comment(lines, node):
            category, raw_import = _format_import_node_without_reordering(node, lines, known_first_party)
            categorized_lines[category].add(raw_import)
            continue

        if isinstance(node, ast.ImportFrom) and _from_import_key(node) in aliased_modules:
            module = "." * node.level + (node.module or "")
            category = _import_category(node.module or "", node.level, known_first_party)
            for alias in node.names:
                aliased_from_imports[category, node.level, module].append((
                    node_index,
                    _format_alias(alias),
                    alias.asname is not None,
                ))
            continue

        if isinstance(node, ast.ImportFrom) and _can_merge_from_imports(node, aliased_modules):
            module = node.module or ""
            category = _import_category(module, node.level, known_first_party)
            for alias in node.names:
                grouped_from_imports[category, node.level, module].add(_format_alias(alias))
            continue

        category, import_line = _format_import_node(node, line_length, known_first_party)
        categorized_lines[category].add(import_line)

    for (category, _level, module), aliases in grouped_from_imports.items():
        categorized_lines[category].add(_format_from_import(module, sorted(aliases, key=_alias_sort_key), line_length))
    for (category, _level, module), aliases in aliased_from_imports.items():
        for import_line in _iter_aliased_from_import_lines(module, aliases, line_length):
            categorized_lines[category].add(import_line)

    groups = [
        "\n".join(sorted(categorized_lines[category], key=_import_line_sort_key))
        for category in sorted(categorized_lines)
    ]
    return "\n\n".join(group for group in groups if group)


def _is_name_or_attr(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name
    return isinstance(node, ast.Attribute) and node.attr == name


def _is_type_checking_if(node: ast.AST) -> TypeGuard[ast.If]:
    return isinstance(node, ast.If) and _is_name_or_attr(node.test, "TYPE_CHECKING")


def _splitlines_no_ff(source: str) -> list[str]:
    """Split source lines like the Python parser, without form-feed splitting."""
    index = 0
    lines: list[str] = []
    next_line = ""
    while index < len(source):
        character = source[index]
        next_line += character
        index += 1

        if character == "\r" and index < len(source) and source[index] == "\n":
            next_line += "\n"
            index += 1
        if character in "\r\n":
            lines.append(next_line)
            next_line = ""

    if next_line:
        lines.append(next_line)
    return lines


def _source_lines(source: str) -> list[str]:
    if _SOURCE_LINES_CACHE:
        cached_source, cached_lines = _SOURCE_LINES_CACHE[0]
        if source is cached_source or source == cached_source:
            return cached_lines

    lines = _splitlines_no_ff(source)
    _SOURCE_LINES_CACHE[:] = [(source, lines)]
    return lines


def _source_segment_from_cached_lines(source: str, node: ast.AST) -> str | None:
    if not (
        hasattr(node, "lineno")
        and hasattr(node, "end_lineno")
        and hasattr(node, "col_offset")
        and hasattr(node, "end_col_offset")
    ):
        return None

    location_node = cast("_SourceLocationNode", node)
    lineno = location_node.lineno
    end_lineno = location_node.end_lineno
    col_offset = location_node.col_offset
    end_col_offset = location_node.end_col_offset
    if end_lineno is None or end_col_offset is None:
        return None

    lineno -= 1
    end_lineno -= 1

    lines = _source_lines(source)
    if end_lineno == lineno:
        return lines[lineno].encode()[col_offset:end_col_offset].decode()

    first = lines[lineno].encode()[col_offset:].decode()
    last = lines[end_lineno].encode()[:end_col_offset].decode()
    return "".join([first, *lines[lineno + 1 : end_lineno], last])


def _source_segment(source: str, node: ast.AST) -> str:
    return _source_segment_from_cached_lines(source, node) or ast.unparse(node)


def _inline_source_segment(source: str, node: ast.AST) -> str:
    if isinstance(node, ast.Lambda):
        return re.sub(r"^lambda\s+:", "lambda:", ast.unparse(node))
    return _source_segment(source, node)


def _format_call_argument(keyword: ast.keyword, source: str) -> str:
    if keyword.arg is None:
        return f"**{_source_segment(source, keyword.value)}"
    return f"{keyword.arg}={_inline_source_segment(source, keyword.value)}"


def _format_nested_literal(
    node: ast.AST,
    indent: str,
    source: str,
    line_length: int,
    *,
    preserve_dict_trailing_comma: bool = False,
) -> str | None:
    match node:
        case ast.Dict(keys=[_, *_]):
            return _format_dict_literal(
                node,
                indent,
                source,
                line_length,
                preserve_trailing_comma=preserve_dict_trailing_comma,
            )
        case ast.Tuple(elts=[_, *_]):
            return _format_tuple_literal(node, indent, source, line_length)
    return None


def _format_dict_literal(
    dict_node: ast.Dict,
    indent: str,
    source: str,
    line_length: int = DEFAULT_LINE_LENGTH,
    *,
    preserve_trailing_comma: bool = False,
) -> str:
    entries: list[str] = []
    entry_indent = f"{indent}    "
    has_trailing_comma = len(dict_node.keys) > 1 or (
        preserve_trailing_comma and _source_segment(source, dict_node).rstrip()[:-1].rstrip().endswith(",")
    )
    for key, value in zip(dict_node.keys, dict_node.values, strict=True):
        trailing_comma = "," if has_trailing_comma else ""
        if key is None:
            entries.append(f"{entry_indent}**{_source_segment(source, value)}{trailing_comma}")
            continue

        key_source = _source_segment(source, key)
        value_source = _source_segment(source, value)
        entry = f"{entry_indent}{key_source}: {value_source}{trailing_comma}"
        if (
            len(entry) > line_length
            and (
                nested_literal := _format_nested_literal(
                    value,
                    entry_indent,
                    source,
                    line_length,
                    preserve_dict_trailing_comma=preserve_trailing_comma,
                )
            )
            is not None
        ):
            nested_lines = _split_python_lines(nested_literal)
            entries.append(f"{entry_indent}{key_source}: {nested_lines[0]}")
            entries.extend(nested_lines[1:-1])
            entries.append(f"{nested_lines[-1]}{trailing_comma}")
        else:
            entries.append(entry)
    return "{\n" + "\n".join(entries) + f"\n{indent}}}"


def _format_tuple_literal(
    tuple_node: ast.Tuple,
    indent: str,
    source: str,
    line_length: int = DEFAULT_LINE_LENGTH,
) -> str:
    element_indent = f"{indent}    "
    elements: list[str] = []
    for element in tuple_node.elts:
        element_source = _source_segment(source, element)
        if "\n" not in element_source and len(f"{element_indent}{element_source},") <= line_length:
            elements.append(f"{element_indent}{element_source},")
            continue
        if (
            nested_literal := _format_nested_literal(
                element,
                element_indent,
                source,
                line_length,
                preserve_dict_trailing_comma=True,
            )
        ) is None:
            elements.append(f"{element_indent}{element_source},")
            continue
        nested_lines = _split_python_lines(nested_literal)
        elements.extend(_indent_first_line(nested_lines, element_indent))
        elements[-1] = f"{elements[-1]},"
    return "(\n" + "\n".join(elements) + f"\n{indent})"


def _format_list_literal(list_node: ast.List, indent: str, source: str) -> str:
    element_indent = f"{indent}    "
    has_trailing_comma = len(list_node.elts) > 1
    elements = "\n".join(
        f"{element_indent}{_source_segment(source, element)}{',' if has_trailing_comma else ''}"
        for element in list_node.elts
    )
    return f"[\n{elements}\n{indent}]"


def _split_escaped_string_literal(content: str, max_length: int) -> list[str]:
    chunks: list[str] = []
    remaining = content
    while len(remaining) > max_length:
        split_index = remaining.rfind(" ", 0, max_length + 1)
        if split_index <= 0:
            split_index = max_length
            if remaining[split_index - 1] == "\\":
                split_index -= 1
        chunks.append(remaining[:split_index])
        remaining = remaining[split_index:]
    chunks.append(remaining)
    return chunks


def _format_wrapped_string_literal(value: str, indent: str, line_length: int) -> str:
    value_repr = repr(value)
    quote = value_repr[0]
    escaped = value_repr[1:-1] if quote in {"'", '"'} and value_repr.endswith(quote) else value_repr
    literal_indent = f"{indent}    "
    max_content_length = line_length - len(literal_indent) - 2
    literal_lines = "\n".join(
        f"{literal_indent}{quote}{chunk}{quote}" for chunk in _split_escaped_string_literal(escaped, max_content_length)
    )
    return f"(\n{literal_lines}\n{indent})"


def _format_call_argument_for_block(
    keyword: ast.keyword,
    indent: str,
    line_length: int,
    source: str,
    *,
    wrap_string_literal: bool,
) -> str:
    if keyword.arg is None:
        return f"**{_source_segment(source, keyword.value)}"
    argument = f"{keyword.arg}={_inline_source_segment(source, keyword.value)}"
    if len(f"{indent}{argument}") <= line_length:
        return argument

    formatted_argument = argument
    match keyword.value:
        case ast.Constant(value=str() as value) if wrap_string_literal:
            formatted_argument = f"{keyword.arg}={_format_wrapped_string_literal(value, indent, line_length)}"
        case ast.Dict() as value:
            formatted_argument = f"{keyword.arg}={_format_dict_literal(value, indent, source, line_length)}"
        case ast.Lambda(body=ast.Call() as value):
            formatted_value = _format_call(
                value,
                indent,
                line_length,
                source,
                wrap_string_literal=wrap_string_literal,
            )
            formatted_argument = f"{keyword.arg}=lambda: {formatted_value}"
        case ast.Call() as value:
            formatted_value = _format_call(
                value,
                indent,
                line_length,
                source,
                wrap_string_literal=wrap_string_literal,
            )
            formatted_argument = f"{keyword.arg}={formatted_value}"
    return formatted_argument


def _format_call(  # noqa: PLR0913
    call: ast.Call,
    indent: str,
    line_length: int,
    source: str,
    *,
    force_trailing_comma: bool = False,
    wrap_string_literal: bool = False,
) -> str:
    call_name = _source_segment(source, call.func)
    arguments = [_source_segment(source, argument) for argument in call.args]
    continuation_indent = f"{indent}    "
    arguments.extend(
        _format_call_argument_for_block(
            keyword,
            continuation_indent,
            line_length,
            source,
            wrap_string_literal=wrap_string_literal,
        )
        for keyword in call.keywords
    )
    if not arguments:
        return f"{call_name}()"

    joined_arguments = ", ".join(arguments)
    if "\n" not in joined_arguments and len(f"{continuation_indent}{joined_arguments}") <= line_length:
        return f"{call_name}(\n{continuation_indent}{joined_arguments}\n{indent})"

    has_trailing_comma = force_trailing_comma or len(arguments) > 1
    argument_lines = "\n".join(
        f"{continuation_indent}{argument}{',' if has_trailing_comma else ''}" for argument in arguments
    )
    return f"{call_name}(\n{argument_lines}\n{indent})"


def _format_constrained_call(call: ast.Call, indent: str, line_length: int, source: str) -> str:
    call_name = _source_segment(source, call.func)
    arguments = [_source_segment(source, argument) for argument in call.args]
    arguments.extend(_format_call_argument(keyword, source) for keyword in call.keywords)
    if not arguments:
        return f"{call_name}()"

    inline_call = f"{call_name}({', '.join(arguments)})"
    if len(f"{indent}{inline_call}") <= line_length:
        return inline_call

    continuation_indent = f"{indent}    "
    has_trailing_comma = len(arguments) > 1
    argument_lines = "\n".join(
        f"{continuation_indent}{argument}{',' if has_trailing_comma else ''}" for argument in arguments
    )
    return f"{call_name}(\n{argument_lines}\n{indent})"


def _is_call(node: ast.AST | None, name: str) -> TypeGuard[ast.Call]:
    return isinstance(node, ast.Call) and _is_name_or_attr(node.func, name)


def _has_attribute_root(node: ast.AST, name: str) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == name


def _is_datetime_module_call(node: ast.AST | None) -> TypeGuard[ast.Call]:
    return isinstance(node, ast.Call) and _has_attribute_root(node.func, "datetime_module")


def _is_annotated(node: ast.AST) -> TypeGuard[ast.Subscript]:
    return isinstance(node, ast.Subscript) and _is_name_or_attr(node.value, "Annotated")


def _is_list_of_annotated(node: ast.AST) -> TypeGuard[ast.Subscript]:
    return isinstance(node, ast.Subscript) and _is_name_or_attr(node.value, "list") and _is_annotated(node.slice)


def _is_union(node: ast.AST) -> TypeGuard[ast.Subscript]:
    return isinstance(node, ast.Subscript) and _is_name_or_attr(node.value, "Union")


_CONSTRAINED_CALL_NAMES = frozenset({"conbytes", "condecimal", "confloat", "conint", "conlist", "conset", "constr"})


def _is_constrained_string_call(node: ast.AST | None) -> TypeGuard[ast.Call]:
    return _is_call(node, "constr")


def _is_constrained_call(node: ast.AST | None) -> TypeGuard[ast.Call]:
    return any(_is_call(node, name) for name in _CONSTRAINED_CALL_NAMES)


def _contains_constrained_string_call(node: ast.AST) -> bool:
    return any(_is_constrained_string_call(child) for child in ast.walk(node))


def _contains_annotated(node: ast.AST) -> bool:
    return any(_is_annotated(child) for child in ast.walk(node))


def _contains_list_of_annotated(node: ast.AST) -> bool:
    return any(_is_list_of_annotated(child) for child in ast.walk(node))


def _is_simple_union_annotation(node: ast.AST) -> bool:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _is_simple_union_annotation(node.left) and _is_simple_union_annotation(node.right)
    return isinstance(node, ast.Name | ast.Attribute) or (isinstance(node, ast.Constant) and node.value is None)


def _should_format_constrained_call_union(
    annotation: ast.AST,
    value: ast.AST | None,
    annotation_prefix: str,
    line_length: int,
    source: str,
) -> TypeGuard[ast.BinOp]:
    if not isinstance(annotation, ast.BinOp) or not isinstance(annotation.op, ast.BitOr) or value is None:
        return False
    if not (_is_constrained_call(annotation.left) or _is_constrained_call(annotation.right)):
        return False
    constrained_call = annotation.left if _is_constrained_call(annotation.left) else annotation.right
    return (
        not _is_call(value, "Field")
        or len(annotation_prefix) > line_length
        or len(_source_segment(source, constrained_call)) > line_length - 24
    )


def _can_parenthesize_field_value(annotation: ast.AST, target: str) -> bool:
    if isinstance(annotation, ast.BinOp):
        return False
    if _is_constrained_string_call(annotation):
        return target != "root"
    return not _contains_constrained_string_call(annotation)


def _should_format_union_annotation(
    annotation: ast.AST,
    value: ast.AST | None,
    target_prefix: str,
    annotation_prefix: str,
    line_length: int,
) -> TypeGuard[ast.BinOp]:
    return (
        isinstance(annotation, ast.BinOp)
        and isinstance(annotation.op, ast.BitOr)
        and value is not None
        and not (isinstance(value, ast.Constant) and isinstance(value.value, str))
        and (
            (
                len(annotation_prefix) > line_length
                and (_contains_annotated(annotation) or _is_simple_union_annotation(annotation))
            )
            or _contains_list_of_annotated(annotation)
            or (
                len(f"{annotation_prefix} = {ast.unparse(value)}") > line_length
                and len(target_prefix) > LONG_TARGET_PREFIX_LENGTH
                and (_contains_annotated(annotation) or _is_simple_union_annotation(annotation))
            )
            or (
                len(f"{annotation_prefix} = (") > line_length
                and (_contains_annotated(annotation) or _is_simple_union_annotation(annotation))
            )
        )
    )


def _iter_bit_or_elements(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [*_iter_bit_or_elements(node.left), *_iter_bit_or_elements(node.right)]
    return [node]


def _format_bit_or_element(
    element: ast.AST,
    indent: str,
    line_length: int,
    source: str,
    prefix: str = "",
) -> list[str]:
    element_source = _source_segment(source, element)
    if not isinstance(element, ast.Subscript) or len(f"{indent}{prefix}{element_source}") <= line_length:
        return [f"{indent}{prefix}{element_source}"]

    value = _source_segment(source, element.value)
    elements = _iter_subscript_elements(element)
    trailing_comma = isinstance(element.slice, ast.Tuple)
    formatted_lines = [f"{indent}{prefix}{value}["]
    formatted_lines.extend(
        f"{indent}    {_source_segment(source, subscript_element)}{',' if trailing_comma else ''}"
        for subscript_element in elements
    )
    formatted_lines.append(f"{indent}]")
    return formatted_lines


def _format_bit_or_elements(annotation: ast.BinOp, indent: str, line_length: int, source: str) -> list[str]:
    elements = _iter_bit_or_elements(annotation)
    formatted_elements = _format_bit_or_element(elements[0], indent, line_length, source)
    for element in elements[1:]:
        formatted_elements.extend(_format_bit_or_element(element, indent, line_length, source, "| "))
    return formatted_elements


def _format_parenthesized_bit_or_annotation(
    annotation: ast.BinOp,
    indent: str,
    line_length: int,
    source: str,
) -> str:
    annotation_source = _source_segment(source, annotation)
    if len(f"{indent}    {annotation_source}") <= line_length:
        formatted_annotation = f"{indent}    {annotation_source}"
    else:
        formatted_annotation = "\n".join(_format_bit_or_elements(annotation, f"{indent}    ", line_length, source))
    return f"(\n{formatted_annotation}\n{indent})"


def _should_format_field_bit_or_annotation_assignment(
    statement: ast.AnnAssign,
    field_prefix: str,
    annotation: str,
    line_length: int,
) -> bool:
    value_node = statement.value
    if (
        not isinstance(statement.annotation, ast.BinOp)
        or not isinstance(statement.annotation.op, ast.BitOr)
        or not _is_call(value_node, "Field")
    ):
        return False

    return len(f"{field_prefix}{annotation} = (") > line_length


def _should_format_field_bit_or_value_assignment(
    statement: ast.AnnAssign,
    value_prefix: str,
    line_length: int,
    source: str,
) -> bool:
    value_node = statement.value
    if (
        not isinstance(statement.annotation, ast.BinOp)
        or not isinstance(statement.annotation.op, ast.BitOr)
        or not _is_call(value_node, "Field")
    ):
        return False

    call_start = f"{_source_segment(source, value_node.func)}("
    return len(f"{value_prefix}{call_start}") > line_length and len(f"{value_prefix}(") <= line_length


def _format_parenthesized_field_value(
    statement: ast.AnnAssign,
    value_prefix: str,
    line_length: int,
    source: str,
    *,
    wrap_string_literal: bool,
) -> str:
    value_node = statement.value
    assert _is_call(value_node, "Field")
    indent = value_prefix[: len(value_prefix) - len(value_prefix.lstrip())]
    value = _source_segment(source, value_node)
    if len(f"{indent}    {value}") <= line_length:
        return f"{value_prefix}(\n{indent}    {value}\n{indent})"

    formatted_value = _format_call(
        value_node,
        f"{indent}    ",
        line_length,
        source,
        wrap_string_literal=wrap_string_literal,
    )
    return f"{value_prefix}(\n{indent}    {formatted_value}\n{indent})"


def _should_format_string_bit_or_annotation_assignment(
    statement: ast.AnnAssign,
    field_prefix: str,
    annotation: str,
    line_length: int,
    source: str,
) -> bool:
    return (
        isinstance(statement.annotation, ast.BinOp)
        and isinstance(statement.annotation.op, ast.BitOr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
        and len(f"{field_prefix}{annotation} = {_source_segment(source, statement.value)}") > line_length
        and len(f"{field_prefix}{annotation} = (") > line_length
    )


def _format_bit_or_annotation_assignment(
    statement: ast.AnnAssign,
    field_prefix: str,
    line_length: int,
    source: str,
) -> str:
    value_node = statement.value
    assert isinstance(statement.annotation, ast.BinOp)
    assert value_node is not None
    indent = field_prefix[: len(field_prefix) - len(field_prefix.lstrip())]
    value = _source_segment(source, value_node)
    formatted_annotation = _format_parenthesized_bit_or_annotation(
        statement.annotation,
        indent,
        line_length,
        source,
    )
    return f"{field_prefix}{formatted_annotation} = {value}"


def _iter_subscript_elements(node: ast.Subscript) -> list[ast.AST]:
    if isinstance(node.slice, ast.Tuple):
        return list(node.slice.elts)
    return [node.slice]


def _format_annotated(  # noqa: PLR0913
    annotation: ast.Subscript,
    indent: str,
    line_length: int,
    source: str,
    closing_suffix: str = "",
    *,
    wrap_string_literal: bool = False,
) -> str:
    continuation_indent = f"{indent}    "
    inline_elements = [_source_segment(source, element) for element in _iter_subscript_elements(annotation)]
    joined_elements = ", ".join(inline_elements)
    if len(f"{continuation_indent}{joined_elements}") <= line_length:
        return f"Annotated[\n{continuation_indent}{joined_elements}\n{indent}]{closing_suffix}"

    formatted_lines: list[str] = ["Annotated["]
    for element in _iter_subscript_elements(annotation):
        if _is_call(element, "Field") or _is_call(element, "Meta"):
            inline_field = _source_segment(source, element)
            if len(f"{continuation_indent}{inline_field},") <= line_length:
                formatted_lines.append(f"{continuation_indent}{inline_field},")
                continue
            call_lines = _split_python_lines(
                _format_call(
                    element,
                    continuation_indent,
                    line_length,
                    source,
                    wrap_string_literal=wrap_string_literal,
                )
            )
            call_lines[-1] = f"{call_lines[-1]},"
            formatted_lines.extend(_indent_first_line(call_lines, continuation_indent))
        else:
            formatted_lines.append(f"{continuation_indent}{_source_segment(source, element)},")
    formatted_lines.append(f"{indent}]{closing_suffix}")
    return "\n".join(formatted_lines)


def _config_dict_assignment(statement: ast.stmt) -> tuple[ast.Assign, ast.Call] | None:
    if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and _is_call(statement.value, "ConfigDict"):
        return statement, statement.value
    return None


def _classvar_tuple_value(statement: ast.AnnAssign) -> ast.Tuple | None:
    match statement:
        case ast.AnnAssign(
            annotation=ast.Subscript(value=class_var),
            value=ast.Tuple() as value,
        ) if _is_name_or_attr(class_var, "ClassVar"):
            return value
    return None


def _format_generated_annotation_assignment(  # noqa: PLR0911, PLR0912
    statement: ast.AnnAssign,
    line: str,
    line_length: int,
    source: str,
    *,
    wrap_string_literal: bool,
) -> str | None:
    assert isinstance(statement.target, ast.Name)
    indent = _line_indent(line)
    target = statement.target.id
    annotation = _source_segment(source, statement.annotation)
    target_prefix = f"{indent}{target}: "
    annotation_prefix = f"{target_prefix}{annotation}"
    value_prefix = f"{annotation_prefix} = "
    if (
        statement.lineno != (statement.end_lineno or statement.lineno)
        and (tuple_value := _classvar_tuple_value(statement)) is not None
    ):
        return f"{value_prefix}{_format_tuple_literal(tuple_value, indent, source, line_length)}"
    if statement.value is None and _is_list_of_annotated(statement.annotation):
        return f"{target_prefix}{_format_list_of_annotated(statement.annotation, indent, line_length, source)}"
    if _should_format_constrained_call_union(
        statement.annotation,
        statement.value,
        annotation_prefix,
        line_length,
        source,
    ):
        assert statement.value is not None
        value = _source_segment(source, statement.value)
        return (
            f"{target_prefix}{_format_constrained_call_union(statement.annotation, indent, line_length, source)}"
            f" = {value}"
        )
    if _should_format_field_bit_or_annotation_assignment(
        statement,
        target_prefix,
        annotation,
        line_length,
    ):
        return _format_bit_or_annotation_assignment(
            statement,
            target_prefix,
            line_length,
            source,
        )
    if _should_format_field_bit_or_value_assignment(
        statement,
        value_prefix,
        line_length,
        source,
    ):
        return _format_parenthesized_field_value(
            statement,
            value_prefix,
            line_length,
            source,
            wrap_string_literal=wrap_string_literal,
        )
    if _should_format_string_bit_or_annotation_assignment(
        statement,
        target_prefix,
        annotation,
        line_length,
        source,
    ):
        return _format_bit_or_annotation_assignment(
            statement,
            target_prefix,
            line_length,
            source,
        )
    if (
        isinstance(statement.annotation, ast.BinOp)
        and isinstance(statement.annotation.op, ast.BitOr)
        and statement.value is not None
        and _contains_constrained_string_call(statement.annotation)
        and len(annotation_prefix) > line_length
    ):
        value = _source_segment(source, statement.value)
        return f"{target_prefix}(\n{indent}    {annotation}\n{indent}) = {value}"
    if _should_format_union_annotation(
        statement.annotation,
        statement.value,
        target_prefix,
        annotation_prefix,
        line_length,
    ):
        assert statement.value is not None
        value = _source_segment(source, statement.value)
        formatted_annotation = _format_annotated_union(statement.annotation, indent, line_length, source)
        if len(f"{indent}) = {value}") > line_length:
            return f"{target_prefix}{formatted_annotation} = (\n{indent}    {value}\n{indent})"
        return f"{target_prefix}{formatted_annotation} = {value}"
    if _is_union(statement.annotation) and statement.value is not None and len(annotation_prefix) > line_length:
        value = _source_segment(source, statement.value)
        return (
            f"{target_prefix}"
            f"{_format_union_subscript(statement.annotation, indent, source, f' = {value}', line_length)}"
        )
    if _is_call(statement.value, "Field"):
        value = _source_segment(source, statement.value)
        if (
            len(value_prefix) > line_length - 16
            and len(f"{value_prefix}{value}") > line_length
            and _can_parenthesize_field_value(statement.annotation, target)
        ):
            return _format_parenthesized_field_value(
                statement,
                value_prefix,
                line_length,
                source,
                wrap_string_literal=wrap_string_literal,
            )
        formatted_value = _format_call(
            statement.value,
            indent,
            line_length,
            source,
            wrap_string_literal=wrap_string_literal,
        )
        return f"{value_prefix}{formatted_value}"
    if (
        _is_annotated(statement.annotation)
        and isinstance(statement.value, ast.List)
        and not any(isinstance(element, ast.Dict) for element in statement.value.elts)
        and len(f"{value_prefix}{_source_segment(source, statement.value)}") > line_length
    ):
        closing_suffix = f" = {_source_segment(source, statement.value)}"
        formatted_annotation = _format_annotated(
            statement.annotation,
            indent,
            line_length,
            source,
            closing_suffix,
            wrap_string_literal=wrap_string_literal,
        )
        return f"{target_prefix}{formatted_annotation}"
    if isinstance(statement.value, ast.Dict):
        return f"{value_prefix}{_format_dict_literal(statement.value, indent, source, line_length)}"
    if isinstance(statement.value, ast.List):
        return f"{value_prefix}{_format_list_literal(statement.value, indent, source)}"
    if _is_call(statement.value, "field"):
        formatted_value = _format_call(
            statement.value,
            indent,
            line_length,
            source,
            wrap_string_literal=wrap_string_literal,
        )
        return f"{value_prefix}{formatted_value}"
    if _is_datetime_module_call(statement.value):
        formatted_value = _format_call(
            statement.value,
            indent,
            line_length,
            source,
            wrap_string_literal=wrap_string_literal,
        )
        return f"{value_prefix}{formatted_value}"
    if _is_annotated(statement.annotation):
        if (
            statement.value is not None
            and len(annotation_prefix) <= line_length
            and len(line) <= line_length + MAX_SHORT_DEFAULT_OVERFLOW
        ):
            value = _source_segment(source, statement.value)
            return f"{value_prefix}(\n{indent}    {value}\n{indent})"
        closing_suffix = "" if statement.value is None else f" = {_source_segment(source, statement.value)}"
        formatted_annotation = _format_annotated(
            statement.annotation,
            indent,
            line_length,
            source,
            closing_suffix,
            wrap_string_literal=wrap_string_literal,
        )
        return f"{target_prefix}{formatted_annotation}"
    if statement.value is not None:
        value = _source_segment(source, statement.value)
        return f"{value_prefix}(\n{indent}    {value}\n{indent})"
    return None


def _format_generated_class_statement(
    statement: ast.stmt,
    line: str,
    line_length: int,
    source: str,
    *,
    wrap_string_literal: bool,
) -> str | None:
    indent = _line_indent(line)
    config_dict = _config_dict_assignment(statement)
    config_dict_needs_formatting = config_dict is not None and (
        len(line) > line_length
        or any(
            isinstance(keyword.value, ast.Dict)
            and len(f"{indent}    {_format_call_argument(keyword, source)}") > line_length
            for keyword in config_dict[1].keywords
        )
    )
    if (len(line) <= line_length and not config_dict_needs_formatting) or _has_comment_token(line):
        return None

    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        signature = line
        return_annotation = ""
        if (returns := statement.returns) is not None:
            if returns.lineno != statement.lineno or returns.end_lineno != statement.lineno:
                return None
            signature = line.encode()[: returns.col_offset].decode()
            return_annotation = line[len(signature) :]
        before_arguments, _, after_open = signature.partition("(")
        arguments, _, suffix = after_open.rpartition(")")
        suffix += return_annotation
        if arguments and suffix.endswith(":"):
            return f"{before_arguments}(\n{indent}    {arguments}\n{indent}){suffix}"

    if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        return _format_generated_annotation_assignment(
            statement,
            line,
            line_length,
            source,
            wrap_string_literal=wrap_string_literal,
        )

    if config_dict is not None and config_dict_needs_formatting:
        target = _source_segment(source, config_dict[0].targets[0])
        formatted_value = _format_call(
            config_dict[1],
            indent,
            line_length,
            source,
            force_trailing_comma=True,
            wrap_string_literal=wrap_string_literal,
        )
        return f"{indent}{target} = {formatted_value}"

    return None


def _format_constrained_call_union(
    annotation: ast.BinOp,
    indent: str,
    line_length: int,
    source: str,
) -> str:
    left = annotation.left
    right = annotation.right
    if _is_constrained_call(left):
        formatted_left = _format_constrained_call(left, f"{indent}    ", line_length, source)
        inline_union = f"{formatted_left} | {_source_segment(source, right)}"
        if "\n" not in formatted_left and len(f"{indent}    {inline_union}") <= line_length:
            return f"(\n{indent}    {inline_union}\n{indent})"
        return f"(\n{indent}    {formatted_left}\n{indent}    | {_source_segment(source, right)}\n{indent})"
    if _is_constrained_call(right):
        return f"{_source_segment(source, left)} | {_format_constrained_call(right, indent, line_length, source)}"
    return _source_segment(source, annotation)  # pragma: no cover


def _format_annotated_union(annotation: ast.BinOp, indent: str, line_length: int, source: str) -> str:
    left = annotation.left
    right = annotation.right
    inline_union = f"{_source_segment(source, left)} | {_source_segment(source, right)}"
    if len(f"{indent}    {inline_union}") <= line_length:
        return f"(\n{indent}    {inline_union}\n{indent})"
    if _is_annotated(left):
        if len(f"{indent}    {_source_segment(source, left)}") > line_length:
            annotated_lines = _split_python_lines(_format_annotated(left, f"{indent}    ", line_length, source))
            formatted_annotated = "\n".join(_indent_first_line(annotated_lines, f"{indent}    "))
        else:
            formatted_annotated = f"{indent}    {_source_segment(source, left)}"
        return f"(\n{formatted_annotated}\n{indent}    | {_source_segment(source, right)}\n{indent})"
    if _is_annotated(right):
        return (
            f"(\n{indent}    {_source_segment(source, left)}\n{indent}    | {_source_segment(source, right)}\n{indent})"
        )
    return _format_parenthesized_bit_or_annotation(annotation, indent, line_length, source)


def _format_list_of_annotated(annotation: ast.Subscript, indent: str, line_length: int, source: str) -> str:
    continuation_indent = f"{indent}    "
    assert _is_annotated(annotation.slice)
    inline_annotated = _source_segment(source, annotation.slice)
    if len(f"{continuation_indent}{inline_annotated}") <= line_length:
        formatted_annotated = f"{continuation_indent}{inline_annotated}"
    else:
        annotated_lines = _split_python_lines(
            _format_annotated(annotation.slice, continuation_indent, line_length, source)
        )
        formatted_annotated = "\n".join(_indent_first_line(annotated_lines, continuation_indent))
    return f"list[\n{formatted_annotated}\n{indent}]"


def _format_union_subscript(
    union: ast.Subscript,
    indent: str,
    source: str,
    closing_suffix: str = "",
    line_length: int = DEFAULT_LINE_LENGTH,
) -> str:
    continuation_indent = f"{indent}    "
    elements = [_source_segment(source, element) for element in _iter_subscript_elements(union)]
    joined_elements = ", ".join(elements)
    if len(f"{continuation_indent}{joined_elements}") <= line_length:
        return f"Union[\n{continuation_indent}{joined_elements}\n{indent}]{closing_suffix}"

    formatted_lines = ["Union["]
    formatted_lines.extend(f"{continuation_indent}{element}," for element in elements)
    formatted_lines.append(f"{indent}]{closing_suffix}")
    return "\n".join(formatted_lines)


def _format_subscript_value(
    subscript: ast.Subscript,
    indent: str,
    line_length: int,
    source: str,
    closing_suffix: str = "",
) -> str:
    continuation_indent = f"{indent}    "
    value = _source_segment(source, subscript.value)
    elements = _iter_subscript_elements(subscript)
    joined_elements = ", ".join(_source_segment(source, element) for element in elements)
    trailing_comma = isinstance(subscript.slice, ast.Tuple)
    if len(f"{continuation_indent}{joined_elements}") <= line_length:
        return f"{value}[\n{continuation_indent}{joined_elements}\n{indent}]{closing_suffix}"

    formatted_lines = [f"{value}["]
    for element in elements:
        element_source = _source_segment(source, element)
        if isinstance(element, ast.BinOp) and isinstance(element.op, ast.BitOr):
            element_lines = _format_bit_or_elements(element, continuation_indent, line_length, source)
            if trailing_comma:
                element_lines[-1] = f"{element_lines[-1]},"
            formatted_lines.extend(element_lines)
        elif isinstance(element, ast.Subscript) and len(f"{continuation_indent}{element_source}") > line_length:
            element_lines = _split_python_lines(
                _format_subscript_value(element, continuation_indent, line_length, source, ",")
            )
            formatted_lines.extend(_indent_first_line(element_lines, continuation_indent))
        else:
            formatted_lines.append(f"{continuation_indent}{element_source}{',' if trailing_comma else ''}")
    formatted_lines.append(f"{indent}]{closing_suffix}")
    return "\n".join(formatted_lines)


def _format_type_alias_type_argument(
    argument: ast.AST,
    continuation_indent: str,
    line_length: int,
    source: str,
) -> Iterable[str]:
    if _is_union(argument):
        union_lines = _split_python_lines(_format_union_subscript(argument, continuation_indent, source, ",", 0))
        return _indent_first_line(union_lines, continuation_indent)

    argument_source = _source_segment(source, argument)
    if not _is_annotated(argument):
        return [f"{continuation_indent}{argument_source},"]
    match argument.slice:
        case ast.Tuple(elts=[ast.BinOp(op=ast.BitOr()), *_]):
            has_bit_or_value = True
        case _:
            has_bit_or_value = False
    if (
        not has_bit_or_value
        and "\n" not in argument_source
        and len(f"{continuation_indent}{argument_source},") <= line_length
    ):
        return [f"{continuation_indent}{argument_source},"]

    annotated_lines = _split_python_lines(_format_annotated(argument, continuation_indent, line_length, source, ","))
    return _indent_first_line(annotated_lines, continuation_indent)


def _format_type_alias_type_call(call: ast.Call, indent: str, line_length: int, source: str) -> str:
    continuation_indent = f"{indent}    "
    inline_arguments = ", ".join(_source_segment(source, argument) for argument in call.args)
    can_inline_type_alias_arguments = (
        len(call.args) == TYPE_ALIAS_INLINE_ARGUMENT_COUNT
        and not call.keywords
        and (_is_annotated(call.args[1]) or _is_union(call.args[1]))
        and "\n" not in inline_arguments
    )
    if can_inline_type_alias_arguments and len(f"{continuation_indent}{inline_arguments}") <= line_length:
        return f"TypeAliasType(\n{continuation_indent}{inline_arguments}\n{indent})"

    formatted_lines = ["TypeAliasType("]
    for argument in call.args:
        formatted_lines.extend(_format_type_alias_type_argument(argument, continuation_indent, line_length, source))
    formatted_lines.extend(
        f"{continuation_indent}{_format_call_argument(keyword, source)}," for keyword in call.keywords
    )
    formatted_lines.append(f"{indent})")
    return "\n".join(formatted_lines)


def _format_type_alias_union_assignment(
    statement: ast.AnnAssign,
    line: str,
    line_length: int,
    source: str,
) -> str | None:
    if len(line) <= line_length and statement.lineno == (statement.end_lineno or statement.lineno):
        return None

    indent = _line_indent(line)
    match statement:
        case ast.AnnAssign(
            target=ast.Name(id=target),
            annotation=ast.Name(id="TypeAlias"),
            value=ast.Subscript() as value,
        ) if _is_union(value):
            union = _format_union_subscript(value, indent, source, line_length=0)
            return f"{indent}{target}: TypeAlias = {union}"
        case ast.AnnAssign(
            target=ast.Name(id=target),
            annotation=ast.Name(id="TypeAlias"),
            value=ast.Subscript() as value,
        ) if _is_annotated(value) and statement.lineno == (statement.end_lineno or statement.lineno):
            return f"{indent}{target}: TypeAlias = {_format_annotated(value, indent, line_length, source)}"

    return None


def _format_typed_dict_call(call: ast.Call, indent: str, source: str) -> str:
    continuation_indent = f"{indent}    "
    formatted_lines = ["TypedDict("]
    for argument in call.args:
        if isinstance(argument, ast.Dict):
            dict_lines = _split_python_lines(_format_dict_literal(argument, continuation_indent, source))
            dict_lines[-1] = f"{dict_lines[-1]},"
            formatted_lines.extend(_indent_first_line(dict_lines, continuation_indent))
        else:
            formatted_lines.append(f"{continuation_indent}{_source_segment(source, argument)},")
    formatted_lines.extend(
        f"{continuation_indent}{_format_call_argument(keyword, source)}," for keyword in call.keywords
    )
    formatted_lines.append(f"{indent})")
    return "\n".join(formatted_lines)


def _is_root_model_constrained_union(base: ast.AST) -> TypeGuard[ast.Subscript]:
    return (
        isinstance(base, ast.Subscript)
        and _is_name_or_attr(base.value, "RootModel")
        and isinstance(base.slice, ast.BinOp)
        and isinstance(base.slice.op, ast.BitOr)
        and (_is_constrained_string_call(base.slice.left) or _is_constrained_string_call(base.slice.right))
    )


def _format_root_model_constrained_union_base(
    base: ast.Subscript,
    indent: str,
    line_length: int,
    source: str,
) -> str:
    continuation_indent = f"{indent}    "
    inner_indent = f"{continuation_indent}    "
    union = base.slice
    assert isinstance(union, ast.BinOp)
    formatted_lines = ["RootModel["]
    if _is_constrained_string_call(union.left):
        call_lines = _split_python_lines(_format_constrained_call(union.left, inner_indent, line_length, source))
        formatted_lines.extend(_indent_first_line(call_lines, inner_indent))
        formatted_lines.append(f"{inner_indent}| {_source_segment(source, union.right)}")
    elif _is_constrained_string_call(union.right):  # pragma: no branch
        formatted_lines.append(f"{inner_indent}{_source_segment(source, union.left)}")
        call_lines = _split_python_lines(_format_constrained_call(union.right, inner_indent, line_length, source))
        formatted_lines.append(f"{inner_indent}| {call_lines[0]}")
        formatted_lines.extend(call_lines[1:])
    formatted_lines.append(f"{continuation_indent}]")
    return "\n".join(formatted_lines)


def _format_root_model_union_base(
    base: ast.Subscript,
    indent: str,
    line_length: int,
    source: str,
) -> str:
    continuation_indent = f"{indent}    "
    inner_indent = f"{continuation_indent}    "
    assert isinstance(base.slice, ast.BinOp)
    formatted_lines = ["RootModel["]
    formatted_lines.extend(_format_bit_or_elements(base.slice, inner_indent, line_length, source))
    formatted_lines.append(f"{continuation_indent}]")
    return "\n".join(formatted_lines)


def _format_generated_class_definition(
    statement: ast.ClassDef,
    line: str,
    line_length: int,
    source: str,
) -> str | None:
    if len(line) <= line_length or _has_comment_token(line):
        return None

    indent = _line_indent(line)
    if len(statement.bases) > 1:
        formatted_bases = "\n".join(f"{indent}    {_source_segment(source, base)}," for base in statement.bases)
        return f"{indent}class {statement.name}(\n{formatted_bases}\n{indent}):"
    if len(statement.bases) != 1:
        return None

    if _is_root_model_constrained_union(statement.bases[0]):
        base = _format_root_model_constrained_union_base(statement.bases[0], indent, line_length, source)
    elif (
        isinstance(statement.bases[0], ast.Subscript)
        and _is_name_or_attr(statement.bases[0].value, "RootModel")
        and isinstance(statement.bases[0].slice, ast.BinOp)
        and isinstance(statement.bases[0].slice.op, ast.BitOr)
        and len(f"{indent}    {_source_segment(source, statement.bases[0])}") > line_length
    ):
        base = _format_root_model_union_base(statement.bases[0], indent, line_length, source)
    elif _is_name_or_attr(statement.bases[0], "RootModel") or (
        isinstance(statement.bases[0], ast.Subscript) and _is_name_or_attr(statement.bases[0].value, "RootModel")
    ):
        base = _source_segment(source, statement.bases[0])
    else:
        return None
    return f"{indent}class {statement.name}(\n{indent}    {base}\n{indent}):"


def _format_generated_module_statement(  # noqa: PLR0911
    statement: ast.stmt, line: str, line_length: int, source: str
) -> str | None:
    if _has_comment_token(line):
        return None
    if (
        isinstance(statement, ast.AnnAssign)
        and (formatted_alias := _format_type_alias_union_assignment(statement, line, line_length, source)) is not None
    ):
        return formatted_alias
    if (
        sys.version_info >= (3, 12)
        and isinstance(statement, ast.TypeAlias)
        and _is_annotated(statement.value)
        and len(line) > line_length
    ):
        indent = _line_indent(line)
        target = _source_segment(source, statement.name)
        return f"{indent}type {target} = {_format_annotated(statement.value, indent, line_length, source)}"
    if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
        return None
    if isinstance(statement.value, ast.Subscript) and (
        len(line) > line_length or statement.lineno != (statement.end_lineno or statement.lineno)
    ):
        indent = _line_indent(line)
        target = _source_segment(source, statement.targets[0])
        return f"{indent}{target} = {_format_subscript_value(statement.value, indent, line_length, source)}"
    if _is_call(statement.value, "TypedDict") and any(
        isinstance(argument, ast.Dict) for argument in statement.value.args
    ):
        indent = _line_indent(line)
        target = _source_segment(source, statement.targets[0])
        return f"{indent}{target} = {_format_typed_dict_call(statement.value, indent, source)}"
    if not _is_call(statement.value, "TypeAliasType"):
        return None
    if not any(
        (
            _is_union(argument)
            and (len(line) > line_length or argument.lineno != (argument.end_lineno or argument.lineno))
        )
        or (_is_annotated(argument) and len(line) > line_length)
        for argument in statement.value.args
    ):
        return None
    if len(line) <= line_length and statement.lineno == (statement.end_lineno or statement.lineno):
        return None  # pragma: no cover

    indent = _line_indent(line)
    target = _source_segment(source, statement.targets[0])
    return f"{indent}{target} = {_format_type_alias_type_call(statement.value, indent, line_length, source)}"


def _format_type_checking_block(
    node: ast.If, lines: list[str], line_length: int, known_first_party: frozenset[str]
) -> str | None:
    import_nodes = [statement for statement in node.body if isinstance(statement, (ast.Import, ast.ImportFrom))]
    if not import_nodes or len(import_nodes) != len(node.body):
        return None

    indent = _line_indent(lines[node.lineno - 1])
    import_block = _build_builtin_import_block(import_nodes, line_length, lines, known_first_party)
    indented_import_block = "\n".join(
        f"{indent}    {line}" if line else line for line in _split_python_lines(import_block)
    )
    return f"{indent}if TYPE_CHECKING:\n{indented_import_block}"


_LineReplacement = tuple[int, int, list[str]]


def _pep695_type_alias_replacement(lines: list[str], start_index: int, line_length: int) -> _LineReplacement | None:
    line = lines[start_index]
    if (match := PEP695_TYPE_ALIAS_START_PATTERN.match(line)) is None:
        return None

    end_index = _pep695_type_alias_statement_end(lines, start_index)
    statement_lines = lines[start_index : end_index + 1]
    if any(_has_comment_token(statement_line) for statement_line in statement_lines):
        return None

    rhs_first_line = line[match.end() :].lstrip()
    rhs_source = "\n".join([rhs_first_line, *statement_lines[1:]])
    try:
        value = ast.parse(rhs_source, mode="eval").body
    except SyntaxError:
        return None
    if not _is_annotated(value) or len(line) <= line_length:
        return None

    indent = match.group("indent")
    target = match.group("target")
    formatted_value = _format_annotated(value, indent, line_length, rhs_source)
    return start_index + 1, end_index + 1, _split_python_lines(f"{indent}type {target} = {formatted_value}")


def _collect_pep695_type_alias_replacements(lines: list[str], line_length: int) -> list[_LineReplacement]:
    replacements: list[_LineReplacement] = []
    line_index = 0
    while line_index < len(lines):
        if (replacement := _pep695_type_alias_replacement(lines, line_index, line_length)) is None:
            line_index += 1
            continue
        replacements.append(replacement)
        line_index = replacement[1]
    return replacements


def _has_overlong_tuple_value(statement: ast.stmt, lines: list[str], line_length: int) -> bool:
    match statement:
        case ast.AnnAssign(target=ast.Name()) if (value := _classvar_tuple_value(statement)) is not None:
            end_lineno = statement.end_lineno or statement.lineno
            statement_lines = lines[statement.lineno - 1 : end_lineno]
            if len(statement_lines[0]) > line_length or any(_has_comment_token(line) for line in statement_lines):
                return False
            overlong_line_numbers = {
                line_number
                for line_number, line in enumerate(statement_lines, start=statement.lineno)
                if len(line) > line_length
            }
            return any(
                node is not value
                and isinstance(node, ast.Tuple)
                and bool(node.elts)
                and any(
                    line_number in overlong_line_numbers
                    for line_number in range(node.lineno, (node.end_lineno or node.lineno) + 1)
                )
                for node in ast.walk(value)
            )
    return False


def _iter_boolean_operand_lines(node: ast.BoolOp, source: bytes, indent: str, line_length: int) -> Iterator[str]:
    """Split boolean operands, wrapping nested groups that exceed the line length."""
    operator = "and" if isinstance(node.op, ast.And) else "or"
    operator_bytes = operator.encode()
    start = node.col_offset
    last_index = len(node.values) - 1
    for index, value in enumerate(node.values):
        # The gap between AST operands contains only their parentheses and this operator.
        end = (
            source.index(operator_bytes, value.end_col_offset, node.values[index + 1].col_offset)
            if index < last_index
            else node.end_col_offset
        )
        operand = source[start:end].decode().strip()
        start = (end or 0) + len(operator_bytes)
        operand_prefix = f"{operator} " if index else ""
        line = f"{indent}{operand_prefix}{operand}"
        if len(line) > line_length and isinstance(value, ast.BoolOp):
            yield from _format_boolean_group(value, source, indent, line_length, operand_prefix)
        else:
            yield line


def _format_boolean_group(
    node: ast.BoolOp, source: bytes, indent: str, line_length: int, prefix: str = ""
) -> list[str]:
    """Wrap a boolean group and any nested groups exposed by the added indent."""
    operand_indent = f"{indent}    "
    single_line = f"{operand_indent}{source[node.col_offset : node.end_col_offset].decode()}"
    return [
        f"{indent}{prefix}(",
        *(
            (single_line,)
            if len(single_line) <= line_length
            else _iter_boolean_operand_lines(node, source, operand_indent, line_length)
        ),
        f"{indent})",
    ]


def _collect_function_boolean_replacements(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    line_length: int,
) -> list[_LineReplacement]:
    """Wrap parenthesized boolean groups and guards inside generated methods."""
    candidates = {
        number
        for number in range(function.lineno, (function.end_lineno or function.lineno) + 1)
        if len(line := lines[number - 1]) > line_length and (" and " in line or " or " in line)
    }
    if not candidates:
        return []
    replacements: list[_LineReplacement] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.BoolOp) or node.lineno not in candidates or node.lineno != node.end_lineno:
            continue
        line = lines[node.lineno - 1]
        source = line.encode()
        before, after = source[: node.col_offset].strip(), source[node.end_col_offset :].strip()
        indent = _line_indent(line)
        match before, after:
            case (b"(", b")") | (b"if not (", _) | (_, b"):") if (
                before == b"("
                or (after == b"):" and before.startswith(b"if ") and before.endswith(b"("))
                or (after.startswith((b") or ", b") and ")) and after.endswith(b":"))
            ):
                formatted = _format_boolean_group(node, source, indent, line_length, before[:-1].decode())
                formatted[-1] += after[1:].decode()
            case b"", b"" if (
                1 < node.lineno < len(lines)
                and lines[node.lineno - 2].endswith("(")
                and lines[node.lineno].lstrip().startswith(")")
            ):
                formatted = list(_iter_boolean_operand_lines(node, source, indent, line_length))
            case _:
                continue
        replacements.append((node.lineno, node.lineno, formatted))
        candidates.discard(node.lineno)
    return replacements


def _statement_start(statement: ast.stmt) -> int:
    return min(
        (decorator.lineno for decorator in getattr(statement, "decorator_list", [])),
        default=statement.lineno,
    )


def _collect_builtin_replacements(  # noqa: PLR0912, PLR0913
    tree: ast.Module,
    lines: list[str],
    line_length: int,
    source: str,
    known_first_party: frozenset[str],
    *,
    wrap_string_literal: bool,
) -> list[_LineReplacement]:
    replacements: list[_LineReplacement] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            replacements.extend(_collect_function_boolean_replacements(node, lines, line_length))
        if _is_type_checking_if(node):
            formatted_type_checking = _format_type_checking_block(node, lines, line_length, known_first_party)
            if formatted_type_checking is not None:
                replacements.append((
                    node.lineno,
                    node.end_lineno or node.lineno,
                    _split_python_lines(formatted_type_checking),
                ))
            continue

        formatted_statement = _format_generated_module_statement(node, lines[node.lineno - 1], line_length, source)
        if formatted_statement is not None:
            replacements.append((
                node.lineno,
                node.end_lineno or node.lineno,
                _split_python_lines(formatted_statement),
            ))

        if isinstance(node, ast.ClassDef):
            formatted_definition = _format_generated_class_definition(node, lines[node.lineno - 1], line_length, source)
            if formatted_definition is not None:
                replacements.append((node.lineno, node.lineno, _split_python_lines(formatted_definition)))
            if len(node.body) > 1 and (docstring_node := _docstring_node(node.body[0])) is not None:
                docstring_end = docstring_node.end_lineno or docstring_node.lineno
                next_start = _statement_start(node.body[1])
                if docstring_end + 1 == next_start:
                    replacements.append((docstring_end, docstring_end, [lines[docstring_end - 1], ""]))
                elif docstring_end + 2 < next_start:
                    replacements.append((docstring_end + 1, next_start - 1, [""]))
            for previous_statement, next_statement in zip(node.body, node.body[1:], strict=False):
                if previous_statement is node.body[0] and _docstring_node(previous_statement) is not None:
                    continue
                previous_end = previous_statement.end_lineno or previous_statement.lineno
                next_start = _statement_start(next_statement)
                if previous_end + 2 < next_start:
                    replacements.append((previous_end + 1, next_start - 1, [""]))
            for statement in node.body:
                is_long_function_definition = False
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    replacements.extend(_collect_function_boolean_replacements(statement, lines, line_length))
                    is_long_function_definition = len(lines[statement.lineno - 1]) > line_length
                is_multiline_statement = statement.lineno != (statement.end_lineno or statement.lineno)
                format_tuple_value = (
                    is_multiline_statement
                    and isinstance(statement, ast.AnnAssign)
                    and _has_overlong_tuple_value(statement, lines, line_length)
                )
                if (
                    is_multiline_statement
                    and not is_long_function_definition
                    and not format_tuple_value
                    and not (
                        isinstance(statement, ast.Assign)
                        and len(statement.targets) == 1
                        and _is_call(statement.value, "ConfigDict")
                    )
                ):
                    continue
                if format_tuple_value:
                    assert isinstance(statement, ast.AnnAssign)
                    formatted_statement = _format_generated_annotation_assignment(
                        statement,
                        lines[statement.lineno - 1],
                        line_length,
                        source,
                        wrap_string_literal=wrap_string_literal,
                    )
                else:
                    formatted_statement = _format_generated_class_statement(
                        statement,
                        lines[statement.lineno - 1],
                        line_length,
                        source,
                        wrap_string_literal=wrap_string_literal,
                    )
                if formatted_statement is not None:
                    replacement_end = statement.lineno if is_long_function_definition else statement.end_lineno
                    replacements.append((
                        statement.lineno,
                        replacement_end or statement.lineno,
                        _split_python_lines(formatted_statement),
                    ))

    return replacements


def _module_docstring_node(tree: ast.Module) -> ast.Expr | None:
    if not tree.body:
        return None
    return _docstring_node(tree.body[0])


def _docstring_node(first_node: ast.stmt) -> ast.Expr | None:
    if (
        isinstance(first_node, ast.Expr)
        and isinstance(first_node.value, ast.Constant)
        and isinstance(first_node.value.value, str)
    ):
        return first_node
    return None


def _leading_lines_before_imports(lines: list[str], first_import_line: int, tree: ast.Module) -> list[str]:
    leading_lines = list(lines[: first_import_line - 1])
    docstring_node = _module_docstring_node(tree)
    if (
        docstring_node is not None
        and docstring_node.end_lineno == len(leading_lines)
        and (not leading_lines or leading_lines[-1].strip())
    ):
        leading_lines.append("")
    return leading_lines


def _iter_module_import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    body_nodes = tree.body
    if _module_docstring_node(tree) is not None:
        body_nodes = body_nodes[1:]

    import_nodes: list[ast.Import | ast.ImportFrom] = []
    for node in body_nodes:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            break
        import_nodes.append(node)
    return import_nodes


def _apply_line_replacements(lines: list[str], replacements: list[_LineReplacement], *, offset: int = 0) -> list[str]:
    formatted_lines = list(lines)
    for start, end, replacement_lines in sorted(replacements, reverse=True):
        start_index = start - offset - 1
        end_index = end - offset
        if start_index < 0 or end_index > len(formatted_lines):  # pragma: no cover
            continue
        formatted_lines[start_index:end_index] = replacement_lines
    return formatted_lines


def _previous_non_empty_line_index(lines: list[str]) -> int:
    index = len(lines) - 1
    while index >= 0 and not lines[index]:
        index -= 1
    return index


def _ensure_post_class_annotation_assignment_spacing(
    formatted_lines: list[str],
    line: str,
    *,
    previous_top_level_class_or_function: bool,
) -> None:
    if not previous_top_level_class_or_function or ".__annotations__[" not in line:
        return

    previous_line_index = _previous_non_empty_line_index(formatted_lines)
    if previous_line_index < 0 or not formatted_lines[previous_line_index].startswith((" ", "\t")):
        return

    while len(formatted_lines) - previous_line_index - 1 < MAX_TOP_LEVEL_BLANK_LINES:
        formatted_lines.append("")


def _normalize_top_level_blank_lines(code: str) -> str:
    string_lines: set[int] = set()
    # Multi-line STRING tokens can only come from triple quotes or backslash-continuation.
    if ('"""' in code) or ("'''" in code) or ("\\\n" in code) or ("\\\r\n" in code):
        for token in tokenize.generate_tokens(StringIO(code).readline):
            if token.type == tokenize.STRING and token.start[0] != token.end[0]:
                string_lines.update(range(token.start[0], token.end[0] + 1))

    lines = _split_python_lines(code)
    formatted_lines: list[str] = []
    previous_top_level_class_or_function = False
    for line_number, line in enumerate(lines, start=1):
        if line and not line.startswith((" ", "\t")) and line_number not in string_lines:
            _ensure_post_class_annotation_assignment_spacing(
                formatted_lines,
                line,
                previous_top_level_class_or_function=previous_top_level_class_or_function,
            )
            if line.startswith(("@", "class ", "def ", "async def ")):
                previous_line_index = _previous_non_empty_line_index(formatted_lines)
                if previous_line_index >= 0 and formatted_lines[previous_line_index].startswith("@"):
                    while formatted_lines and not formatted_lines[-1]:
                        formatted_lines.pop()
                elif (
                    line.startswith(("class ", "def ", "async def "))
                    and previous_line_index >= 0
                    and not formatted_lines[previous_line_index].startswith((" ", "\t"))
                ):
                    while len(formatted_lines) - previous_line_index - 1 < MAX_TOP_LEVEL_BLANK_LINES:
                        formatted_lines.append("")
            while len(formatted_lines) > MAX_TOP_LEVEL_BLANK_LINES and formatted_lines[
                -(MAX_TOP_LEVEL_BLANK_LINES + 1) :
            ] == [""] * (MAX_TOP_LEVEL_BLANK_LINES + 1):
                formatted_lines.pop()
            previous_top_level_class_or_function = line.startswith(("class ", "def ", "async def "))
        formatted_lines.append(line)
    return "\n".join(formatted_lines)


def _normalize_string_quotes(code: str) -> str:
    tokens: list[tokenize.TokenInfo] = []
    for token in tokenize.generate_tokens(StringIO(code).readline):
        normalized_token = token
        if token.type == tokenize.STRING and (string_match := STRING_PREFIX_PATTERN.match(token.string)) is not None:
            prefix, quote = string_match.groups()
            if quote == "'":
                body = token.string[string_match.end() : -1]
                if '"' not in body and not body.endswith("\\"):
                    normalized_token = tokenize.TokenInfo(
                        token.type,
                        f'{prefix}"{body}"',
                        token.start,
                        token.end,
                        token.line,
                    )
        tokens.append(normalized_token)
    return tokenize.untokenize(tokens)


def _normalize_string_quotes_if_needed(code: str, *, source_is_valid: bool) -> str:
    """Normalize string quotes when the already-validated source may contain a candidate."""
    if type(code) is not str or not source_is_valid or "'" in code or "\t" in code or "\f" in code:
        return _normalize_string_quotes(code)
    return code


def _finalize_builtin_code(
    code: str,
    *,
    string_normalization: bool,
    source_is_valid: bool = False,
) -> str:
    formatted_code = _normalize_top_level_blank_lines(code.strip("\n"))
    if string_normalization:
        formatted_code = _normalize_string_quotes_if_needed(formatted_code, source_is_valid=source_is_valid)
    return f"{formatted_code}\n"


def _generated_import_prefix_end(lines: list[str]) -> int | None:
    """Return the end of a simple generated import prefix, or None when unsupported."""
    line_index = 0
    import_end = 0
    while line_index < len(lines):
        line = lines[line_index]
        if not line:
            line_index += 1
            continue
        if not line.startswith(("from ", "import ")):
            stripped_line = line.lstrip()
            return None if not import_end and stripped_line.startswith(("#", "'''", '"""')) else import_end

        bracket_depth = 0
        while line_index < len(lines):
            import_line = lines[line_index]
            bracket_depth += import_line.count("(") - import_line.count(")")
            line_index += 1
            if bracket_depth <= 0:
                import_end = line_index
                break
        else:
            return None
    return import_end


def _try_apply_builtin_generated_formatter(  # noqa: PLR0911, PLR0913
    code: str,
    *,
    line_length: int,
    known_first_party: frozenset[str],
    wrap_string_literal: bool,
    string_normalization: bool,
    python_version: PythonVersion | None,
) -> str | None:
    """Format trusted generated code without parsing the complete module."""
    if wrap_string_literal or string_normalization:
        return None
    if python_version is not None and python_version.version_key > sys.version_info[:2]:
        return None

    lines: list[str] = []
    for source_line in _split_python_lines(code):
        line = source_line.rstrip()
        if (
            len(line) > line_length  # noqa: PLR0916
            or "ConfigDict(" in line
            or "TypeAlias" in line
            or "TypedDict(" in line
            or "if TYPE_CHECKING:" in line
            or "'''" in line
            or '"""' in line
        ):
            return None
        lines.append(line)

    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return ""
    if (import_end := _generated_import_prefix_end(lines)) is None:
        return None
    if not import_end:
        return _finalize_builtin_code("\n".join(lines), string_normalization=False)

    try:
        import_tree = ast.parse("\n".join(lines[:import_end]))
    except SyntaxError:
        return None
    if not (import_nodes := _iter_module_import_nodes(import_tree)) or len(import_nodes) != len(import_tree.body):
        return None

    import_block = _build_builtin_import_block(import_nodes, line_length, lines, known_first_party)
    body_start = import_end
    while body_start < len(lines) and not lines[body_start]:
        body_start += 1
    del lines[:body_start]
    if not lines:
        return _finalize_builtin_code(import_block, string_normalization=False)
    body = "\n".join(lines)
    separator = "\n\n\n" if body.startswith(("class ", "def ", "async def ", "@")) else "\n\n"
    return _finalize_builtin_code(f"{import_block}{separator}{body}", string_normalization=False)


def _apply_builtin_generated_formatter(  # noqa: PLR0913
    code: str,
    *,
    line_length: int = DEFAULT_LINE_LENGTH,
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
    wrap_string_literal: bool = False,
    string_normalization: bool = False,
    python_version: PythonVersion | None = None,
) -> str:
    """Format generator-owned source, falling back for unsupported code."""
    if (
        formatted := _try_apply_builtin_generated_formatter(
            code,
            line_length=line_length,
            known_first_party=known_first_party,
            wrap_string_literal=wrap_string_literal,
            string_normalization=string_normalization,
            python_version=python_version,
        )
    ) is not None:
        return formatted
    return apply_builtin_formatter(
        code,
        line_length=line_length,
        known_first_party=known_first_party,
        wrap_string_literal=wrap_string_literal,
        string_normalization=string_normalization,
        python_version=python_version,
    )


def apply_builtin_formatter(  # noqa: PLR0913, PLR0914
    code: str,
    *,
    line_length: int = DEFAULT_LINE_LENGTH,
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
    wrap_string_literal: bool = False,
    string_normalization: bool = False,
    python_version: PythonVersion | None = None,
) -> str:
    """Apply dependency-free formatting for generated Python code."""
    lines = [line.rstrip() for line in _split_python_lines(code)]
    start = 0
    while start < len(lines) and not lines[start]:
        start += 1
    if start:
        del lines[:start]
    code = "\n".join(lines).strip("\n")
    if not code:
        return ""

    tree, source_is_valid = _parse_builtin_code_with_source_validity(code, python_version)
    if tree is None:
        return f"{code}\n"

    replacements = _collect_builtin_replacements(
        tree,
        lines,
        line_length,
        code,
        known_first_party,
        wrap_string_literal=wrap_string_literal,
    )
    if _needs_pep695_type_alias_placeholders(python_version):
        replacements.extend(_collect_pep695_type_alias_replacements(lines, line_length))
    import_nodes = _iter_module_import_nodes(tree)

    if not import_nodes:
        formatted_lines = _apply_line_replacements(lines, replacements)
        return _finalize_builtin_code(
            "\n".join(formatted_lines),
            string_normalization=string_normalization,
            source_is_valid=source_is_valid,
        )

    first_line = import_nodes[0].lineno
    last_line = import_nodes[-1].end_lineno or import_nodes[-1].lineno
    leading = "\n".join(_leading_lines_before_imports(lines, first_line, tree))
    body_lines = _apply_line_replacements(lines[last_line:], replacements, offset=last_line)
    body = "\n".join(body_lines).strip("\n")
    import_block = _build_builtin_import_block(import_nodes, line_length, lines, known_first_party)
    formatted_code = f"{leading}\n{import_block}" if leading else import_block
    if body:
        separator = "\n\n\n" if body.startswith(("class ", "def ", "async def ", "@")) else "\n\n"
        formatted_code = f"{formatted_code}{separator}{body}" if formatted_code else body
    return _finalize_builtin_code(
        formatted_code,
        string_normalization=string_normalization,
        source_is_valid=source_is_valid,
    )
