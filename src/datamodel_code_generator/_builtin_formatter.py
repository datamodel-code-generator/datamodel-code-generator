"""Dependency-free formatter for generated Python code."""

from __future__ import annotations

import ast
import re
import sys
import tokenize
from collections import defaultdict
from enum import IntEnum
from functools import partial
from io import StringIO
from typing import TYPE_CHECKING, Any, NamedTuple, TypeAlias, TypeGuard

from datamodel_code_generator._builtin_line_split import format_logical_line, str_width
from datamodel_code_generator._constraint_applicability import json_integer
from datamodel_code_generator._format_types import (
    DEFAULT_KNOWN_FIRST_PARTY,
    DEFAULT_LINE_LENGTH,
    MAX_TOP_LEVEL_BLANK_LINES,
    STRING_PREFIX_PATTERN,
    PythonVersionMin,
)
from datamodel_code_generator._module_paths import relative_level
from datamodel_code_generator._python_decorator import string_token_spans
from datamodel_code_generator._python_syntax import (
    aliased,
    from_import,
    module_import,
    normalize_newlines,
)
from datamodel_code_generator._python_target_names import target_stdlib_module_names
from datamodel_code_generator._target_unicode import (
    NEWEST_TARGET,
    HostIdentifiers,
    host_identifiers,
    is_upper,
    lower,
    target_pattern,
)
from datamodel_code_generator.util import _find_pyproject_toml, load_toml

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from datamodel_code_generator._format_types import PythonVersion


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


class _ImportNames(NamedTuple):
    """How import sorting reads names: with lent letters given back, by the case rules of the target."""

    restore: Callable[[str], str] = str
    python_version: PythonVersion | None = None


_PLAIN_NAMES = _ImportNames()

_DIGIT_RUN = re.compile(r"(\d+)")
_NaturalKey: TypeAlias = "tuple[tuple[str | int, ...], str]"


def _natural_key(text: str) -> _NaturalKey:
    """Order ``text`` like isort's ``naturally``: digit runs compare as numbers, the text itself breaks ties."""
    return tuple(int(part) if part.isdigit() else part for part in _DIGIT_RUN.split(text)), text


_DEF_PREFIXES = ("class ", "def ", "async def ")
PEP695_TYPE_ALIAS_START_PATTERN = re.compile(r"^(?P<indent>\s*)type\s+(?P<target>[A-Za-z_]\w*(?:\[.*?\])?)\s*=")
_PEP695_IDENTIFIER_TYPE_ALIAS_START = r"^(?P<indent>\s*)type\s+(?P<target>(?#identifier)(?:\[.*?\])?)\s*="
PEP695_TYPE_ALIAS_PLACEHOLDER = "__datamodel_codegen_builtin_type_alias__"


def _split_python_lines(source: str) -> list[str]:
    """Split physical Python lines while preserving Unicode separators."""
    if not source:
        return []
    lines = normalize_newlines(source).split("\n")
    if lines[-1]:
        return lines
    lines.pop()
    return lines


def _is_valid_builtin_line_length(line_length: Any) -> TypeGuard[int]:
    return json_integer(line_length) and line_length > 0


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _check_builtin_line_length(line_length: Any, is_valid: Callable[[Any], bool]) -> None:
    """Reject an explicit line length that ``is_valid``, the caller's module global, does not accept."""
    if line_length is not None and not is_valid(line_length):
        msg = "builtin_format_line_length must be a positive integer"
        raise ValueError(msg)


def _load_builtin_tool_config(
    settings_path: Path,
    find_pyproject_toml: Callable[[Path], Path | None],
    load: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Return the ``[tool]`` table of the nearest pyproject.toml, empty when there is none.

    The finder and loader are the caller's module globals, so a patch of either module's names applies.
    """
    if (pyproject_toml_path := find_pyproject_toml(settings_path)) is None:
        return {}
    return load(pyproject_toml_path).get("tool", {})


def _get_builtin_line_length(
    settings_path: Path,
    explicit_line_length: int | None = None,
    tool_config: Mapping[str, Any] | None = None,
) -> int:
    _check_builtin_line_length(explicit_line_length, _is_valid_builtin_line_length)
    if explicit_line_length is not None:
        return explicit_line_length
    if tool_config is None:
        tool_config = _load_builtin_tool_config(settings_path, _find_pyproject_toml, load_toml)

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
        tool_config = _load_builtin_tool_config(settings_path, _find_pyproject_toml, load_toml)

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
        tool_config = _load_builtin_tool_config(settings_path, _find_pyproject_toml, load_toml)

    black_config = tool_config.get("black", {})
    skip_black_string_normalization = black_config.get("skip-string-normalization")
    if isinstance(skip_black_string_normalization, bool):
        return not skip_black_string_normalization
    return False


def _format_alias(alias: ast.alias) -> str:
    if alias.asname:
        return aliased(alias.name, alias.asname)
    return alias.name


def _alias_imported_name(alias: str) -> str:
    if " as " in alias:
        return alias.split(" as ", 1)[0]
    return alias


def _alias_sort_key(alias: str, names: _ImportNames = _PLAIN_NAMES) -> tuple[_AliasSortCategory, _NaturalKey]:
    name = names.restore(_alias_imported_name(alias))
    target = names.python_version
    if len(name) > 1 and is_upper(name, target):
        category = _AliasSortCategory.CONSTANT
    elif is_upper(name[:1], target):
        category = _AliasSortCategory.CLASS
    else:
        category = _AliasSortCategory.OTHER
    return category, _natural_key(lower(name, target))


def _format_from_import(module: str, aliases: list[str], line_length: int, restore: Callable[[str], str] = str) -> str:
    """Lay out one ``from`` import as isort does: a star import stays a statement of its own, before the other names."""
    if "*" in aliases:
        rest = [alias for alias in aliases if alias != "*"]
        star = from_import(module, ["*"])
        return f"{star}\n{_format_from_import(module, rest, line_length, restore)}" if rest else star
    line = from_import(module, aliases)
    if str_width(restore(line)) <= line_length:
        return line
    imports = "\n".join(f"    {alias}," for alias in aliases)
    return f"from {module} import (\n{imports}\n)"


def _import_category(
    module: str, level: int, known_first_party: frozenset[str], python_version: PythonVersion | None = None
) -> _ImportCategory:
    if module == "__future__":
        return _ImportCategory.FUTURE
    if level:
        return _ImportCategory.LOCAL
    top_level_module = module.split(".", 1)[0]
    if top_level_module in target_stdlib_module_names((python_version or PythonVersionMin).version_key):
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


def _pep695_type_alias_start(line: str) -> re.Match[str] | None:
    """Match the start of a type statement named with any identifier, or the letters lent for its characters."""
    return (
        PEP695_TYPE_ALIAS_START_PATTERN
        if line.isascii()
        else target_pattern(_PEP695_IDENTIFIER_TYPE_ALIAS_START, NEWEST_TARGET)
    ).match(line)


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
        if not _pep695_type_alias_start(line):
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


def _format_import_node_without_reordering(
    node: ast.Import | ast.ImportFrom,
    lines: list[str],
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
    names: _ImportNames = _PLAIN_NAMES,
) -> tuple[_ImportCategory, str]:
    end_lineno = node.end_lineno or node.lineno
    indent = _line_indent(lines[node.lineno - 1])
    raw_import = "\n".join(line.removeprefix(indent) for line in lines[node.lineno - 1 : end_lineno])
    return _import_node_category(node, known_first_party, names), raw_import


def _import_node_category(
    node: ast.Import | ast.ImportFrom, known_first_party: frozenset[str], names: _ImportNames = _PLAIN_NAMES
) -> _ImportCategory:
    match node:
        case ast.Import():
            return min(
                _import_category(names.restore(alias.name), 0, known_first_party, names.python_version)
                for alias in node.names
            )
        case ast.ImportFrom():
            return _import_category(
                names.restore(node.module or ""), node.level, known_first_party, names.python_version
            )
    msg = f"Unsupported import node: {type(node).__name__}"
    raise TypeError(msg)


def _from_import_key(node: ast.ImportFrom) -> tuple[int, str]:
    return node.level, node.module or ""


def _modules_with_aliased_imports(import_nodes: list[ast.Import | ast.ImportFrom]) -> set[tuple[int, str]]:
    return {
        _from_import_key(node)
        for node in import_nodes
        if isinstance(node, ast.ImportFrom) and any(alias.asname is not None for alias in node.names)
    }


def _iter_aliased_from_import_lines(
    module: str,
    aliases: list[tuple[int, str, bool]],
    line_length: int,
    names: _ImportNames = _PLAIN_NAMES,
) -> Iterator[str]:
    """Keep aliased names, and plain names also imported under an alias, on their own lines like isort."""
    sorted_aliases = sorted(aliases, key=lambda item: _alias_sort_key(item[1], names))
    renamed = {_alias_imported_name(alias) for _, alias, is_aliased in aliases if is_aliased}
    chunk: list[str] = []
    chunk_group: int | None = None
    for group_index, alias, is_aliased in sorted_aliases:
        if is_aliased or alias in renamed:
            if chunk:
                yield _format_from_import(module, chunk, line_length, names.restore)
                chunk = []
                chunk_group = None
            yield _format_from_import(module, [alias], line_length, names.restore)
            continue

        if chunk and chunk_group != group_index:
            yield _format_from_import(module, chunk, line_length, names.restore)
            chunk = []
        chunk.append(alias)
        chunk_group = group_index

    if chunk:
        yield _format_from_import(module, chunk, line_length, names.restore)


def _import_line_sort_key(
    line: str, names: _ImportNames = _PLAIN_NAMES
) -> tuple[int, int, _NaturalKey, int, _NaturalKey, _NaturalKey]:
    line = names.restore(line)
    target = names.python_version
    line_key = _natural_key(lower(line, target))
    if not line.startswith("from "):
        return 0, 0, line_key, 0, _natural_key(""), line_key

    module, _, imported = line.removeprefix("from ").partition(" import ")
    level, relative_module = relative_level(module)
    alias_category, alias_name = _alias_sort_key(imported.removeprefix("(").split(",", 1)[0].strip(), names)
    if level:
        return 1, -level, _natural_key(lower(relative_module, target)), int(alias_category), alias_name, line_key
    return 1, 0, _natural_key(lower(module, target)), int(alias_category), alias_name, line_key


def _build_builtin_import_block(
    import_nodes: list[ast.Import | ast.ImportFrom],
    line_length: int,
    lines: list[str],
    known_first_party: frozenset[str],
    names: _ImportNames = _PLAIN_NAMES,
) -> str:
    categorized_lines: defaultdict[_ImportCategory, list[str]] = defaultdict(list)
    grouped_from_imports: defaultdict[tuple[_ImportCategory, str], list[str]] = defaultdict(list)
    aliased_from_imports: defaultdict[tuple[_ImportCategory, str], list[tuple[int, str, bool]]] = defaultdict(list)
    aliased_modules = _modules_with_aliased_imports(import_nodes)

    for node_index, node in enumerate(import_nodes):
        if _has_inline_comment(lines, node):
            category, raw_import = _format_import_node_without_reordering(node, lines, known_first_party, names)
            categorized_lines[category].append(raw_import)
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                category = _import_category(names.restore(alias.name), 0, known_first_party, names.python_version)
                categorized_lines[category].append(module_import(_format_alias(alias)))
            continue

        module = "." * node.level + (node.module or "")
        category = _import_node_category(node, known_first_party, names)
        if _from_import_key(node) in aliased_modules:
            aliased_from_imports[category, module].extend(
                (node_index, _format_alias(alias), alias.asname is not None) for alias in node.names
            )
        else:
            grouped_from_imports[category, module].extend(_format_alias(alias) for alias in node.names)

    for (category, module), aliases in grouped_from_imports.items():
        categorized_lines[category].append(
            _format_from_import(
                module,
                sorted(dict.fromkeys(aliases), key=lambda alias: _alias_sort_key(alias, names)),
                line_length,
                names.restore,
            )
        )
    for (category, module), aliases in aliased_from_imports.items():
        for import_line in _iter_aliased_from_import_lines(module, aliases, line_length, names):
            categorized_lines[category].append(import_line)

    groups = [
        "\n".join(
            sorted(dict.fromkeys(categorized_lines[category]), key=lambda line: _import_line_sort_key(line, names))
        )
        for category in sorted(categorized_lines)
    ]
    return "\n\n".join(group for group in groups if group)


def _is_name_or_attr(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name
    return isinstance(node, ast.Attribute) and node.attr == name


def _is_string_constant(node: ast.AST | None) -> TypeGuard[ast.Constant]:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


_LineReplacement = tuple[int, int, list[str]]
_NON_CODE_TOKENS = frozenset({tokenize.NL, tokenize.COMMENT})


def _takes_own_lines(statement: ast.stmt, lines: list[str]) -> bool:
    """Return whether ``statement`` alone takes its lines: nothing before it on its first line or after it on its last.

    A statement after a ``;`` or after a compound statement's ``:`` on the same line does not.
    """
    end = statement.end_lineno or statement.lineno
    return statement.col_offset == len(_line_indent(lines[statement.lineno - 1])) and statement.end_col_offset == len(
        lines[end - 1].encode()
    )


def _nested_bodies(statement: ast.stmt) -> Iterator[list[ast.stmt]]:
    """Yield the bodies nested in ``statement``: its own, its ``else`` and ``finally``, and those of its clauses."""
    for name in ("body", "orelse", "finalbody"):
        if body := getattr(statement, name, None):
            yield body
    for clause in (*getattr(statement, "handlers", ()), *getattr(statement, "cases", ())):
        yield clause.body


def _import_runs(
    body: list[ast.stmt], lines: list[str]
) -> Iterator[tuple[list[ast.Import | ast.ImportFrom], ast.stmt | None]]:
    """Yield the runs of imports in ``body`` that isort sorts as one block, each with the statement after it.

    A run holds imports that take their own lines with only blank lines between them; a comment ends it.
    """
    run: list[ast.Import | ast.ImportFrom] = []
    for statement in body:
        if run and not (
            isinstance(statement, (ast.Import, ast.ImportFrom))
            and _takes_own_lines(statement, lines)
            and not any(line.strip() for line in lines[run[-1].end_lineno or run[-1].lineno : statement.lineno - 1])
        ):
            yield run, statement
            run = []
        if isinstance(statement, (ast.Import, ast.ImportFrom)) and _takes_own_lines(statement, lines):
            run.append(statement)
    if run:
        yield run, None


def _collect_nested_import_replacements(
    statement: ast.stmt,
    lines: list[str],
    line_length: int,
    known_first_party: frozenset[str],
    names: _ImportNames = _PLAIN_NAMES,
) -> Iterator[_LineReplacement]:
    """Sort the import runs of the bodies nested in ``statement``, at any depth, as isort sorts the module's imports.

    The lines of a run are indented as its first import, and its line length is what that indent leaves. A statement
    other than an import right after a run gets a blank line before it, as Black puts one after an import.
    """
    for body in _nested_bodies(statement):
        for run, following in _import_runs(body, lines):
            indent = _line_indent(lines[run[0].lineno - 1])
            block = _build_builtin_import_block(run, line_length - len(indent), lines, known_first_party, names)
            end = run[-1].end_lineno or run[-1].lineno
            blank = following is not None and not isinstance(following, (ast.Import, ast.ImportFrom)) and lines[end]
            yield (
                run[0].lineno,
                end,
                [
                    *(f"{indent}{line}" if line else line for line in _split_python_lines(block)),
                    *([""] if blank else []),
                ],
            )
        for child in body:
            yield from _collect_nested_import_replacements(child, lines, line_length, known_first_party, names)


def _iter_boolean_operand_lines(
    node: ast.BoolOp, source: bytes, indent: str, line_length: int, restore: Callable[[str], str] = str
) -> Iterator[str]:
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
        if str_width(restore(line)) > line_length and isinstance(value, ast.BoolOp):
            yield from _format_boolean_group(value, source, indent, line_length, operand_prefix, restore=restore)
        else:
            yield line


def _format_boolean_group(  # noqa: PLR0913
    node: ast.BoolOp,
    source: bytes,
    indent: str,
    line_length: int,
    prefix: str = "",
    *,
    restore: Callable[[str], str] = str,
) -> list[str]:
    """Wrap a boolean group and any nested groups exposed by the added indent."""
    operand_indent = f"{indent}    "
    single_line = f"{operand_indent}{source[node.col_offset : node.end_col_offset].decode()}"
    return [
        f"{indent}{prefix}(",
        *(
            (single_line,)
            if str_width(restore(single_line)) <= line_length
            else _iter_boolean_operand_lines(node, source, operand_indent, line_length, restore)
        ),
        f"{indent})",
    ]


def _collect_function_boolean_replacements(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    line_length: int,
    restore: Callable[[str], str] = str,
) -> list[_LineReplacement]:
    """Wrap parenthesized boolean groups and guards inside generated methods."""
    candidates = {
        number
        for number in range(function.lineno, (function.end_lineno or function.lineno) + 1)
        if str_width(restore(line := lines[number - 1])) > line_length and (" and " in line or " or " in line)
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
                formatted = _format_boolean_group(
                    node, source, indent, line_length, before[:-1].decode(), restore=restore
                )
                formatted[-1] += after[1:].decode()
            case b"", b"" if (
                1 < node.lineno < len(lines)
                and lines[node.lineno - 2].endswith("(")
                and lines[node.lineno].lstrip().startswith(")")
            ):
                formatted = list(_iter_boolean_operand_lines(node, source, indent, line_length, restore))
            case _:
                continue
        replacements.append((node.lineno, node.lineno, formatted))
        candidates.discard(node.lineno)
    return replacements


def _statement_start(statement: ast.stmt, lines: list[str]) -> int:
    if not (decorators := getattr(statement, "decorator_list", None)):
        return statement.lineno
    start = decorators[0].lineno
    while not lines[start - 1].lstrip().startswith("@"):
        start -= 1
    return start


_SIMPLE_STATEMENTS = tuple(
    getattr(ast, name) for name in ("Assign", "AnnAssign", "AugAssign", "Expr", "TypeAlias") if hasattr(ast, name)
)
_MAGIC_TRAILING_COMMA = re.compile(r",\s*[)\]}]")
_JOINED_STRINGS = re.compile(r"""['"]\s+[rRbBuUfF]*['"]""")


def _logical_line_replacement(  # noqa: PLR0913
    lines: list[str],
    start: int,
    end: int,
    line_length: int,
    *,
    wrap_string_literal: bool,
    assignment: bool,
    restore: Callable[[str], str] = str,
) -> _LineReplacement | None:
    """Lay out the logical line on ``lines[start - 1:end]`` as black does; ``None`` when it stays as it is."""
    source_lines = lines[start - 1 : end]
    if (
        len(source_lines) == 1
        and str_width(restore(line := source_lines[0])) <= line_length
        and not _MAGIC_TRAILING_COMMA.search(line)
        and not (wrap_string_literal and _JOINED_STRINGS.search(line))
    ):
        return None
    result = format_logical_line(
        "\n".join(source_lines),
        line_length=line_length,
        wrap_string_literal=wrap_string_literal,
        restore=restore,
        assignment=assignment,
    )
    if result is None:
        return None
    rows, formatted = result
    if formatted == lines[start - 1 : start - 1 + rows]:
        return None
    return start, start - 1 + rows, formatted


def _statement_replacement(
    statement: ast.stmt,
    lines: list[str],
    line_length: int,
    *,
    wrap_string_literal: bool,
    restore: Callable[[str], str] = str,
) -> _LineReplacement | None:
    """Lay out a simple statement as black does; docstrings and other bare strings stay as they are.

    A statement that shares its lines with another one, after a ``;`` or a class header's ``:``, stays as it is too.
    """
    if not isinstance(statement, _SIMPLE_STATEMENTS) or (
        isinstance(statement, ast.Expr) and _is_string_constant(statement.value)
    ):
        return None
    end = statement.end_lineno or statement.lineno
    if isinstance(statement, ast.Assign) and _is_name_or_attr(statement.targets[0], PEP695_TYPE_ALIAS_PLACEHOLDER):
        end = _pep695_type_alias_statement_end(lines, statement.lineno - 1) + 1
    elif not _takes_own_lines(statement, lines):
        return None
    return _logical_line_replacement(
        lines,
        statement.lineno,
        end,
        line_length,
        wrap_string_literal=wrap_string_literal,
        restore=restore,
        assignment=not isinstance(statement, ast.Expr),
    )


def _collect_definition_header_replacements(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    lines: list[str],
    line_length: int,
    *,
    wrap_string_literal: bool,
    restore: Callable[[str], str] = str,
) -> Iterator[_LineReplacement]:
    """Lay out the decorators and the header line of a class or function definition as black does."""
    lay_out = partial(
        _logical_line_replacement,
        lines,
        line_length=line_length,
        wrap_string_literal=wrap_string_literal,
        restore=restore,
    )
    for decorator in node.decorator_list:
        if (
            replacement := lay_out(decorator.lineno, decorator.end_lineno or decorator.lineno, assignment=False)
        ) is not None:
            yield replacement
    if (
        node.body[0].lineno > node.lineno
        and (replacement := lay_out(node.lineno, node.body[0].lineno - 1, assignment=False)) is not None
    ):
        yield replacement


def _collect_class_replacements(
    node: ast.ClassDef,
    lines: list[str],
    line_length: int,
    *,
    wrap_string_literal: bool,
    restore: Callable[[str], str] = str,
) -> Iterator[_LineReplacement]:
    """Lay out the decorators, header and simple statements of a class, and of the definitions nested in it."""
    yield from _collect_definition_header_replacements(
        node, lines, line_length, wrap_string_literal=wrap_string_literal, restore=restore
    )
    for statement in node.body:
        match statement:
            case ast.ClassDef():
                yield from _collect_class_replacements(
                    statement, lines, line_length, wrap_string_literal=wrap_string_literal, restore=restore
                )
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                yield from _collect_function_boolean_replacements(statement, lines, line_length, restore)
                yield from _collect_definition_header_replacements(
                    statement, lines, line_length, wrap_string_literal=wrap_string_literal, restore=restore
                )
            case _:
                if (
                    replacement := _statement_replacement(
                        statement, lines, line_length, wrap_string_literal=wrap_string_literal, restore=restore
                    )
                ) is not None:
                    yield replacement


def _collect_builtin_replacements(  # noqa: PLR0913
    tree: ast.Module,
    lines: list[str],
    line_length: int,
    known_first_party: frozenset[str],
    *,
    wrap_string_literal: bool,
    names: _ImportNames = _PLAIN_NAMES,
) -> list[_LineReplacement]:
    replacements: list[_LineReplacement] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            replacements.extend(_collect_function_boolean_replacements(node, lines, line_length, names.restore))
            replacements.extend(
                _collect_definition_header_replacements(
                    node, lines, line_length, wrap_string_literal=wrap_string_literal, restore=names.restore
                )
            )
        replacements.extend(_collect_nested_import_replacements(node, lines, line_length, known_first_party, names))
        if (
            replacement := _statement_replacement(
                node, lines, line_length, wrap_string_literal=wrap_string_literal, restore=names.restore
            )
        ) is not None:
            replacements.append(replacement)

        if isinstance(node, ast.ClassDef):
            replacements.extend(
                _collect_class_replacements(
                    node, lines, line_length, wrap_string_literal=wrap_string_literal, restore=names.restore
                )
            )
            if len(node.body) > 1 and (docstring_node := _docstring_node(node.body[0])) is not None:
                docstring_end = docstring_node.end_lineno or docstring_node.lineno
                next_start = _statement_start(node.body[1], lines)
                if docstring_end + 1 == next_start:
                    replacements.append((docstring_end, docstring_end, [lines[docstring_end - 1], ""]))
                elif docstring_end + 2 < next_start:
                    replacements.append((docstring_end + 1, next_start - 1, [""]))
            for previous_statement, next_statement in zip(node.body, node.body[1:], strict=False):
                if previous_statement is node.body[0] and _docstring_node(previous_statement) is not None:
                    continue
                previous_end = previous_statement.end_lineno or previous_statement.lineno
                next_start = _statement_start(next_statement, lines)
                if previous_end + 2 < next_start:
                    replacements.append((previous_end + 1, next_start - 1, [""]))

    return replacements


def _module_docstring_node(tree: ast.Module) -> ast.Expr | None:
    if not tree.body:
        return None
    return _docstring_node(tree.body[0])


def _docstring_node(first_node: ast.stmt) -> ast.Expr | None:
    if isinstance(first_node, ast.Expr) and _is_string_constant(first_node.value):
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


def _disjoint_replacements(replacements: list[_LineReplacement]) -> list[_LineReplacement]:
    """Return the replacements in line order, leaving out each one whose lines another one before it replaces.

    A replacement inside another one, such as the blank lines between two imports of a sorted run, is part of it.
    """
    kept: list[_LineReplacement] = []
    for replacement in sorted(replacements, key=lambda item: (item[0], -item[1])):
        if not kept or replacement[0] > kept[-1][1]:
            kept.append(replacement)
    return kept


def _apply_line_replacements(lines: list[str], replacements: list[_LineReplacement], *, offset: int = 0) -> list[str]:
    formatted_lines = list(lines)
    for start, end, replacement_lines in reversed(_disjoint_replacements(replacements)):
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


def _normalize_top_level_blank_lines(code: str) -> str:
    string_lines: set[int] = set()
    # Multi-line STRING tokens can only come from triple quotes or backslash-continuation.
    if ('"""' in code) or ("'''" in code) or ("\\\n" in code) or ("\\\r\n" in code):
        for token in _string_tokens(code):
            if token.type == tokenize.STRING and token.start[0] != token.end[0]:
                string_lines.update(range(token.start[0], token.end[0] + 1))

    lines = _split_python_lines(code)
    formatted_lines: list[str] = []
    previous_definition = False
    in_decorator = False
    for line_number, line in enumerate(lines, start=1):
        if line and not line.startswith((" ", "\t")) and line_number not in string_lines:
            continuation = line.startswith((")", "]", "}"))
            previous_line_index = _previous_non_empty_line_index(formatted_lines)
            starts_definition = not continuation and line.startswith(("@", *_DEF_PREFIXES))
            if starts_definition and in_decorator:
                while formatted_lines and not formatted_lines[-1]:
                    formatted_lines.pop()
            elif previous_line_index >= 0 and (  # noqa: PLR0916
                (
                    starts_definition
                    and not (
                        formatted_lines[previous_line_index].startswith("#")
                        and previous_line_index == len(formatted_lines) - 1
                    )
                )
                or (
                    not continuation
                    and previous_definition
                    and formatted_lines[previous_line_index].startswith((" ", "\t"))
                )
            ):
                while len(formatted_lines) - previous_line_index - 1 < MAX_TOP_LEVEL_BLANK_LINES:
                    formatted_lines.append("")
            while len(formatted_lines) > MAX_TOP_LEVEL_BLANK_LINES and formatted_lines[
                -(MAX_TOP_LEVEL_BLANK_LINES + 1) :
            ] == [""] * (MAX_TOP_LEVEL_BLANK_LINES + 1):
                formatted_lines.pop()
            if not continuation:
                previous_definition = line.startswith(_DEF_PREFIXES)
                in_decorator = line.startswith("@")
        formatted_lines.append(line)
    return "\n".join(formatted_lines)


def _source_span(lines: list[str], start: tuple[int, int], end: tuple[int, int]) -> str:
    """Return the text between two ``(row, column)`` token positions of ``lines``."""
    (start_row, start_column), (end_row, end_column) = start, end
    if start_row == end_row:
        return lines[start_row - 1][start_column:end_column]
    middle = lines[start_row : end_row - 1]
    return "\n".join((lines[start_row - 1][start_column:], *middle, lines[end_row - 1][:end_column]))


def _string_tokens(code: str) -> Iterator[tokenize.TokenInfo]:
    """Yield the tokens of ``code`` with each f-string as one STRING token, as tokenize reads it before Python 3.12."""
    lines = code.split("\n")
    for first, last in string_token_spans(code, template_strings=False):
        if first is last:
            yield first
        else:
            text = _source_span(lines, first.start, last.end)
            yield tokenize.TokenInfo(tokenize.STRING, text, first.start, last.end, first.line)


_ESCAPED_QUOTES = {quote: re.compile(rf"([^\\]|^)\\((?:\\\\)*){quote}") for quote in "'\""}
_UNESCAPED_QUOTES = {quote: re.compile(rf"(([^\\]|^)(\\\\)*){quote}") for quote in "'\""}
_F_STRING_FIELD = re.compile(r"(?:(?<!\{)|^)\{([^{].*?)\}(?:(?!\})|$)")


def _sub_twice(pattern: re.Pattern[str], replacement: str, text: str) -> str:
    """Replace the matches of ``pattern`` twice, so that matches that share a character are all replaced."""
    return pattern.sub(replacement, pattern.sub(replacement, text))


def _has_escaped_f_string_field(prefix: str, body: str) -> bool:
    return "f" in prefix.casefold() and any("\\" in field for field in _F_STRING_FIELD.findall(body))


def _normalized_string_quotes(prefix: str, quote: str, body: str) -> str:
    """Return a one-quote string literal with the quotes black's ``normalize_string_quotes`` chooses.

    Double quotes are preferred unless they need more escapes; escapes the chosen quotes do not need are removed. A
    raw string keeps its body, and an f-string whose replacement fields would hold a backslash keeps its quotes.
    """
    new_quote = "'" if quote == '"' else '"'
    if _has_escaped_f_string_field(prefix, body):
        return f"{prefix}{quote}{body}{quote}"
    if "r" in prefix.casefold():
        if _UNESCAPED_QUOTES[new_quote].search(body):
            return f"{prefix}{quote}{body}{quote}"
        new_body = body
    else:
        body = _sub_twice(_ESCAPED_QUOTES[new_quote], rf"\1\2{new_quote}", body)
        new_body = _sub_twice(_ESCAPED_QUOTES[quote], rf"\1\2{quote}", body)
        new_body = _sub_twice(_UNESCAPED_QUOTES[new_quote], rf"\1\\{new_quote}", new_body)
    escapes, new_escapes = body.count("\\"), new_body.count("\\")
    if (
        _has_escaped_f_string_field(prefix, new_body)
        or new_escapes > escapes
        or (new_escapes == escapes and quote == '"')
    ):
        return f"{prefix}{quote}{body}{quote}"
    return f"{prefix}{new_quote}{new_body}{new_quote}"


def _normalize_string_quotes(code: str) -> str:
    """Give each one-quote string literal of ``code`` the quotes black chooses; triple-quoted strings stay."""
    tokens: list[tokenize.TokenInfo] = []
    for token in _string_tokens(code):
        if (
            token.type == tokenize.STRING
            and (string_match := STRING_PREFIX_PATTERN.match(token.string)) is not None
            and len(quote := string_match[2]) == 1
        ):
            text = _normalized_string_quotes(string_match[1], quote, token.string[string_match.end() : -1])
            token = tokenize.TokenInfo(token.type, text, token.start, token.end, token.line)  # noqa: PLW2901
        tokens.append(token)
    return tokenize.untokenize(tokens)


def _normalize_string_quotes_if_needed(code: str, *, source_is_valid: bool) -> str:
    """Normalize string quotes when the already-validated source may contain a candidate."""
    if type(code) is not str or not source_is_valid or any(character in code for character in "'\\\t\f"):
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


def _join_import_block(import_block: str, body: str) -> str:
    """Join the import block and the body, two blank lines before a leading definition and one otherwise."""
    separator = "\n\n\n" if body.startswith((*_DEF_PREFIXES, "@")) else "\n\n"
    return f"{import_block}{separator}{body}"


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
            str_width(line) > line_length  # noqa: PLR0916
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

    import_block = _build_builtin_import_block(
        import_nodes, line_length, lines, known_first_party, _ImportNames(python_version=python_version)
    )
    body_start = import_end
    while body_start < len(lines) and not lines[body_start]:
        body_start += 1
    del lines[:body_start]
    if not lines:
        return _finalize_builtin_code(import_block, string_normalization=False)
    return _finalize_builtin_code(_join_import_block(import_block, "\n".join(lines)), string_normalization=False)


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


def apply_builtin_formatter(  # noqa: PLR0913
    code: str,
    *,
    line_length: int = DEFAULT_LINE_LENGTH,
    known_first_party: frozenset[str] = DEFAULT_KNOWN_FIRST_PARTY,
    wrap_string_literal: bool = False,
    string_normalization: bool = False,
    python_version: PythonVersion | None = None,
) -> str:
    """Apply dependency-free formatting for generated Python code.

    Identifier characters of a target newer than the running interpreter are lent letters it parses while formatting,
    and the names it reads back take the target's NFKC form, so the result is the one the target's interpreter gives.
    """
    lent = host_identifiers(code)
    formatted = _apply_builtin_formatter(
        code if lent is None else lent.source,
        line_length=line_length,
        known_first_party=known_first_party,
        wrap_string_literal=wrap_string_literal,
        string_normalization=string_normalization,
        names=_ImportNames(str if lent is None else lent.restore_text, python_version),
        lent=lent,
    )
    return formatted if lent is None else lent.restore_text(formatted)


def _rename_lent_identifiers(tree: ast.AST, lent: HostIdentifiers) -> None:
    """Spell each identifier the running interpreter parsed with lent letters as the target parses it.

    Strings take their characters back, so that `repr` spells them as it does without lending.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                node.value = lent.restore_text(node.value)
            continue
        for field, value in ast.iter_fields(node):
            if isinstance(value, str) and field != "tag":
                setattr(node, field, lent.parsed_name(value))


_DOCSTRING_LINE_CONTINUATION = re.compile(r"\\\s*\n")
_STRING_PREFIX_CHARACTERS = "furbFURB"


def _leading_tabs_expanded(text: str) -> list[str]:
    """Split ``text`` into lines and expand the tabs of each line's indentation, as Python reads indentation."""
    lines = []
    for line in text.splitlines():
        stripped_line = line.lstrip()
        if not stripped_line or stripped_line == line:
            lines.append(line)
        else:
            lines.append(line[: len(line) - len(stripped_line)].expandtabs() + stripped_line)
    if text.endswith("\n"):
        lines.append("")
    return lines


def _fix_multiline_docstring(docstring: str, indent: str) -> str:
    """Re-indent the lines of a multi-line docstring to ``indent`` and strip their ends (PEP 257 trimming)."""
    lines = _leading_tabs_expanded(docstring)
    common = min((len(line) - len(line.lstrip()) for line in lines[1:] if line.lstrip()), default=None)
    trimmed = [lines[0].strip()]
    if common is not None:
        last_line_index = len(lines) - 2
        for index, line in enumerate(lines[1:]):
            stripped_line = line[common:].rstrip()
            trimmed.append(f"{indent}{stripped_line}" if stripped_line or index == last_line_index else "")
    return "\n".join(trimmed)


def _laid_out_docstring(text: str, indent: str, line_length: int) -> str:
    """Return the docstring literal ``text`` as black writes it: trimmed, re-indented and padded before its quotes.

    A docstring with a backslash at a line end stays as it is, as black leaves it.
    """
    if _DOCSTRING_LINE_CONTINUATION.search(text):
        return text
    body = text.lstrip(_STRING_PREFIX_CHARACTERS)
    prefix = text[: len(text) - len(body)]
    quote_char = body[0]
    quote_length = 3 if body[1] == quote_char else 1
    docstring = body[quote_length:-quote_length]
    started_empty = not docstring
    docstring = _fix_multiline_docstring(docstring, indent) if "\n" in text else docstring.strip()
    trailing_backslash = False
    if docstring:
        if docstring[0] == quote_char:
            docstring = f" {docstring}"
        if docstring[-1] == quote_char:
            docstring += " "
        if (len(docstring) - len(docstring.rstrip("\\"))) % 2:
            docstring += " "
            trailing_backslash = True
    elif not started_empty:
        docstring = " "
    quote = quote_char * quote_length
    lines = docstring.splitlines()
    if (
        quote_length == 3  # noqa: PLR0916, PLR2004
        and len(lines) > 1
        and len(lines[-1]) + quote_length > line_length
        and len(indent) + quote_length <= line_length
        and not trailing_backslash
        and text[-1 - quote_length] != "\n"
    ):
        return f"{prefix}{quote}{docstring}\n{indent}{quote}"
    return f"{prefix}{quote}{docstring}{quote}"


def _strip_line_ends(code: str, line_length: int) -> str:
    """Strip the end of each line outside string literals, and lay out docstrings as black does.

    A line that ends inside a string keeps its end, which is part of the string's value.
    """
    if '"""' not in code and "'''" not in code:
        return "\n".join(line.rstrip() for line in code.split("\n"))
    line_starts = [0]
    for line in code.split("\n"):
        line_starts.append(line_starts[-1] + len(line) + 1)
    try:
        spans = [
            span for span in string_token_spans(code, template_strings=False) if span[0].type not in _NON_CODE_TOKENS
        ]
    except (tokenize.TokenError, SyntaxError):
        return "\n".join(line.rstrip() for line in code.split("\n"))
    pieces: list[str] = []
    cursor = 0
    for index, (first, last) in enumerate(spans):
        if not (first is not last or first.type == tokenize.STRING):
            continue
        is_docstring = (
            first is last
            and not set(first.string[: len(first.string) - len(first.string.lstrip(_STRING_PREFIX_CHARACTERS))])
            & set("bBfF")
            and (not index or spans[index - 1][0].type == tokenize.INDENT)
            and index + 1 < len(spans)
            and spans[index + 1][0].type == tokenize.NEWLINE
        )
        if first.start[0] == last.end[0] and not is_docstring:
            continue
        start = line_starts[first.start[0] - 1] + first.start[1]
        end = line_starts[last.end[0] - 1] + last.end[1]
        pieces.append(_strip_ends_before_last(code[cursor:start]))
        text = code[start:end]
        pieces.append(_laid_out_docstring(text, " " * first.start[1], line_length) if is_docstring else text)
        cursor = end
    pieces.append("\n".join(line.rstrip() for line in code[cursor:].split("\n")))
    return "".join(pieces)


def _strip_ends_before_last(text: str) -> str:
    """Strip the end of every line of ``text`` but the last, which a string literal continues."""
    *complete, last = text.split("\n")
    return "".join(f"{line.rstrip()}\n" for line in complete) + last


def _apply_builtin_formatter(  # noqa: PLR0913, PLR0914
    code: str,
    *,
    line_length: int,
    known_first_party: frozenset[str],
    wrap_string_literal: bool,
    string_normalization: bool,
    names: _ImportNames,
    lent: HostIdentifiers | None,
) -> str:
    python_version = names.python_version
    lines = _split_python_lines(_strip_line_ends(normalize_newlines(code), line_length))
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
    if (
        string_normalization
        and (normalized := _normalize_string_quotes_if_needed(code, source_is_valid=source_is_valid)) != code
    ):
        code = normalized
        lines = _split_python_lines(code)
        tree = _parse_builtin_code_with_source_validity(code, python_version)[0] or tree
    if lent is not None:
        _rename_lent_identifiers(tree, lent)

    replacements = _collect_builtin_replacements(
        tree,
        lines,
        line_length,
        known_first_party,
        wrap_string_literal=wrap_string_literal,
        names=names,
    )
    import_nodes = _iter_module_import_nodes(tree)

    if not import_nodes:
        formatted_lines = _apply_line_replacements(lines, replacements)
        return _finalize_builtin_code(
            "\n".join(formatted_lines),
            string_normalization=string_normalization and wrap_string_literal,
            source_is_valid=source_is_valid,
        )

    first_line = import_nodes[0].lineno
    last_line = import_nodes[-1].end_lineno or import_nodes[-1].lineno
    leading = "\n".join(_leading_lines_before_imports(lines, first_line, tree))
    body_lines = _apply_line_replacements(lines[last_line:], replacements, offset=last_line)
    body = "\n".join(body_lines).strip("\n")
    import_block = _build_builtin_import_block(import_nodes, line_length, lines, known_first_party, names)
    formatted_code = f"{leading}\n{import_block}" if leading else import_block
    if body:
        formatted_code = _join_import_block(formatted_code, body) if formatted_code else body
    return _finalize_builtin_code(
        formatted_code,
        string_normalization=string_normalization and wrap_string_literal,
        source_is_valid=source_is_valid,
    )
