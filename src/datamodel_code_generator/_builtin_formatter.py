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
from typing import TYPE_CHECKING, Any, NamedTuple, TypeAlias, TypeGuard, cast

from datamodel_code_generator._builtin_line_split import format_logical_line, iter_fexpr_spans, str_width
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
    return python_version is not None and python_version.version_key >= (3, 12) and sys.version_info[:2] < (3, 12)


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
) -> Iterator[tuple[int, str]]:
    """Keep aliased names, and plain names also imported under an alias, on their own lines like isort.

    Each line comes with the index of the import its names come from.
    """
    sorted_aliases = sorted(aliases, key=lambda item: _alias_sort_key(item[1], names))
    renamed = {_alias_imported_name(alias) for _, alias, is_aliased in aliases if is_aliased}
    chunk: list[str] = []
    chunk_group = -1
    for group_index, alias, is_aliased in sorted_aliases:
        if is_aliased or alias in renamed:
            if chunk:
                yield chunk_group, _format_from_import(module, chunk, line_length, names.restore)
                chunk = []
            yield group_index, _format_from_import(module, [alias], line_length, names.restore)
            continue

        if chunk and chunk_group != group_index:
            yield chunk_group, _format_from_import(module, chunk, line_length, names.restore)
            chunk = []
        chunk.append(alias)
        chunk_group = group_index

    if chunk:
        yield chunk_group, _format_from_import(module, chunk, line_length, names.restore)


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


def _build_builtin_import_block(  # noqa: PLR0913
    import_nodes: list[ast.Import | ast.ImportFrom],
    line_length: int,
    lines: list[str],
    known_first_party: frozenset[str],
    names: _ImportNames = _PLAIN_NAMES,
    *,
    comments_above: Mapping[int, list[str]] | None = None,
) -> str:
    """Sort, merge and group the imports as isort does.

    ``comments_above`` maps the index of an import to the comment lines right above it, which stay above the line it
    becomes, after a blank line when an import comes before it, as black puts one after an import.
    """
    categorized_lines: defaultdict[_ImportCategory, list[str]] = defaultdict(list)
    grouped_from_imports: defaultdict[tuple[_ImportCategory, str], list[str]] = defaultdict(list)
    aliased_from_imports: defaultdict[tuple[_ImportCategory, str], list[tuple[int, str, bool]]] = defaultdict(list)
    aliased_modules = _modules_with_aliased_imports(import_nodes)
    line_comments: defaultdict[str, list[str]] = defaultdict(list)
    module_comments: defaultdict[tuple[_ImportCategory, str], list[str]] = defaultdict(list)
    comments_above = comments_above or {}

    for node_index, node in enumerate(import_nodes):
        comments = comments_above.get(node_index, ())
        if _has_inline_comment(lines, node):
            category, raw_import = _format_import_node_without_reordering(node, lines, known_first_party, names)
            categorized_lines[category].append(raw_import)
            line_comments[raw_import].extend(comments)
            continue

        if isinstance(node, ast.Import):
            for alias in node.names:
                category = _import_category(names.restore(alias.name), 0, known_first_party, names.python_version)
                categorized_lines[category].append(import_line := module_import(_format_alias(alias)))
                line_comments[import_line].extend(comments)
                comments = ()
            continue

        module = "." * node.level + (node.module or "")
        category = _import_node_category(node, known_first_party, names)
        if _from_import_key(node) in aliased_modules:
            aliased_from_imports[category, module].extend(
                (node_index, _format_alias(alias), alias.asname is not None) for alias in node.names
            )
        else:
            grouped_from_imports[category, module].extend(_format_alias(alias) for alias in node.names)
            module_comments[category, module].extend(comments)

    for (category, module), aliases in grouped_from_imports.items():
        categorized_lines[category].append(
            import_line := _format_from_import(
                module,
                sorted(dict.fromkeys(aliases), key=lambda alias: _alias_sort_key(alias, names)),
                line_length,
                names.restore,
            )
        )
        line_comments[import_line].extend(module_comments[category, module])
    for (category, module), aliases in aliased_from_imports.items():
        commented = set(comments_above)
        for node_index, import_line in _iter_aliased_from_import_lines(module, aliases, line_length, names):
            categorized_lines[category].append(import_line)
            if node_index in commented:
                line_comments[import_line].extend(comments_above[node_index])
                commented.discard(node_index)

    groups = (
        _import_group(categorized_lines[category], line_comments, names) for category in sorted(categorized_lines)
    )
    return "\n\n".join(group for group in groups if group)


def _import_group(import_lines: list[str], line_comments: Mapping[str, list[str]], names: _ImportNames) -> str:
    """Sort the import lines of one section, each after its comments, with a blank line before comments after a line."""
    group: list[str] = []
    for import_line in sorted(dict.fromkeys(import_lines), key=lambda line: _import_line_sort_key(line, names)):
        if comments := line_comments.get(import_line):
            group.extend(("", *comments) if group else comments)
        group.append(import_line)
    return "\n".join(group)


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


def _import_runs(body: list[ast.stmt], lines: list[str]) -> Iterator[list[ast.Import | ast.ImportFrom]]:
    """Yield the runs of imports in ``body`` that isort sorts as one block.

    A run holds imports that take their own lines with only blank lines between them; a comment ends it.
    """
    run: list[ast.Import | ast.ImportFrom] = []
    for statement in body:
        if run and not (
            isinstance(statement, (ast.Import, ast.ImportFrom))
            and _takes_own_lines(statement, lines)
            and not any(line.strip() for line in lines[run[-1].end_lineno or run[-1].lineno : statement.lineno - 1])
        ):
            yield run
            run = []
        if isinstance(statement, (ast.Import, ast.ImportFrom)) and _takes_own_lines(statement, lines):
            run.append(statement)
    if run:
        yield run


def _statement_start(statement: ast.stmt, lines: list[str]) -> int:
    if not (decorators := getattr(statement, "decorator_list", None)):
        return statement.lineno
    start = decorators[0].lineno
    while not lines[start - 1].lstrip().startswith("@"):
        start -= 1
    return start


_ASSIGNMENTS = (ast.Assign, ast.AnnAssign, ast.AugAssign)
_SIMPLE_STATEMENTS = (
    *_ASSIGNMENTS,
    ast.Expr,
    ast.Return,
    ast.Delete,
    ast.Assert,
    ast.Raise,
    *(getattr(ast, name) for name in ("TypeAlias",) if hasattr(ast, name)),
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


def _is_type_alias_placeholder(statement: ast.stmt) -> bool:
    """Return whether ``statement`` is the placeholder a ``type`` statement was parsed as on an older interpreter."""
    return isinstance(statement, ast.Assign) and _is_name_or_attr(statement.targets[0], PEP695_TYPE_ALIAS_PLACEHOLDER)


def _statement_end(statement: ast.stmt, lines: list[str]) -> int:
    """Return the last line of ``statement``, also of a ``type`` statement parsed as a placeholder."""
    if _is_type_alias_placeholder(statement):
        return _pep695_type_alias_statement_end(lines, statement.lineno - 1) + 1
    return statement.end_lineno or statement.lineno


def _statement_replacement(
    statement: ast.stmt,
    lines: list[str],
    line_length: int,
    *,
    wrap_string_literal: bool,
    restore: Callable[[str], str] = str,
) -> _LineReplacement | None:
    """Lay out a simple statement as black does; docstrings and other bare strings stay as they are.

    A statement that shares its lines with another one, after a ``;`` or a compound statement's ``:``, stays as it is
    too.
    """
    if not isinstance(statement, _SIMPLE_STATEMENTS) or (
        isinstance(statement, ast.Expr) and _is_string_constant(statement.value)
    ):
        return None
    if not _is_type_alias_placeholder(statement) and not _takes_own_lines(statement, lines):
        return None
    return _logical_line_replacement(
        lines,
        statement.lineno,
        _statement_end(statement, lines),
        line_length,
        wrap_string_literal=wrap_string_literal,
        restore=restore,
        assignment=isinstance(statement, _ASSIGNMENTS),
    )


_DEFINITIONS = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
_ClauseStatement: TypeAlias = (
    ast.ClassDef
    | ast.FunctionDef
    | ast.AsyncFunctionDef
    | ast.If
    | ast.For
    | ast.AsyncFor
    | ast.While
    | ast.With
    | ast.AsyncWith
    | ast.Try
)
_CLAUSE_STATEMENTS = (
    *_DEFINITIONS,
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    *(getattr(ast, name) for name in ("TryStar",) if hasattr(ast, name)),
    ast.Match,
)


def _is_stub_body(body: list[ast.stmt], lines: list[str]) -> bool:
    """Return whether ``body`` is one ``...`` after the header's colon, ending the line, which black keeps there."""
    if not (
        len(body) == 1
        and isinstance(statement := body[0], ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and statement.value.value is Ellipsis
    ):
        return False
    line = lines[statement.lineno - 1]
    return statement.end_col_offset == len(line.encode()) and line.encode()[: statement.col_offset].rstrip().endswith(
        b":"
    )


def _collect_header_replacements(
    statement: ast.stmt,
    lines: list[str],
    line_length: int,
    *,
    wrap_string_literal: bool,
    restore: Callable[[str], str] = str,
) -> Iterator[_LineReplacement]:
    """Lay out the decorators of a definition and the header of each clause of a compound statement as black does.

    A definition's stub body ``...`` on its header line is laid out with the header, as black keeps it there. Another
    header with its body on the same line stays as it is.
    """
    if not isinstance(statement, _CLAUSE_STATEMENTS):
        return
    lay_out = partial(
        _logical_line_replacement,
        lines,
        line_length=line_length,
        wrap_string_literal=wrap_string_literal,
        restore=restore,
        assignment=False,
    )
    for decorator in getattr(statement, "decorator_list", ()):
        if (replacement := lay_out(decorator.lineno, decorator.end_lineno or decorator.lineno)) is not None:
            yield replacement
    for start, next_line, body in _clause_headers(statement, lines):
        if body is not None and _is_stub_body(body, lines):
            end = body[0].end_lineno or body[0].lineno
        elif next_line > start:
            end = _header_end(lines, start, next_line)
        else:
            continue
        if (replacement := lay_out(start, end)) is not None:
            yield replacement


def _clause_headers(
    statement: _ClauseStatement | ast.Match, lines: list[str]
) -> Iterator[tuple[int, int, list[ast.stmt] | None]]:
    """Yield the first line of each clause header of ``statement``, the line after it starts on and a definition's body.

    A ``match`` statement's header is followed by its first ``case``, whose header starts on its pattern's line.
    """
    if isinstance(statement, ast.Match):
        yield statement.lineno, statement.cases[0].pattern.lineno, None
        for case in statement.cases:
            if lines[case.pattern.lineno - 1].lstrip().startswith("case"):
                yield case.pattern.lineno, _statement_start(case.body[0], lines), None
        return
    yield (
        statement.lineno,
        _statement_start(statement.body[0], lines),
        statement.body if isinstance(statement, _DEFINITIONS) else None,
    )
    for handler in getattr(statement, "handlers", ()):
        yield handler.lineno, _statement_start(handler.body[0], lines), None


def _import_run_replacements(  # noqa: PLR0913
    body: list[ast.stmt],
    lines: list[str],
    line_length: int,
    known_first_party: frozenset[str],
    names: _ImportNames,
    *,
    after: int = 0,
) -> Iterator[_LineReplacement]:
    """Sort each run of imports in ``body`` that starts after line ``after`` as isort sorts the module's imports.

    A run is indented as its first import, at the line length that indent leaves.
    """
    for run in _import_runs(body, lines):
        if run[0].lineno <= after:
            continue
        indent = _line_indent(lines[run[0].lineno - 1])
        block = _build_builtin_import_block(run, line_length - len(indent), lines, known_first_party, names)
        yield (
            run[0].lineno,
            run[-1].end_lineno or run[-1].lineno,
            [f"{indent}{line}" if line else line for line in _split_python_lines(block)],
        )


def _collect_statement_replacements(  # noqa: PLR0913
    statement: ast.stmt,
    lines: list[str],
    line_length: int,
    known_first_party: frozenset[str],
    *,
    wrap_string_literal: bool,
    names: _ImportNames = _PLAIN_NAMES,
) -> Iterator[_LineReplacement]:
    """Lay out ``statement`` and the statements of the bodies nested in it, at any depth, as black does.

    A simple statement is laid out whole, a compound statement by its headers. The import runs of a nested body are
    sorted as isort sorts the module's imports: indented as their first import, at the line length that indent leaves.
    """
    if (
        replacement := _statement_replacement(
            statement, lines, line_length, wrap_string_literal=wrap_string_literal, restore=names.restore
        )
    ) is not None:
        yield replacement
    yield from _collect_header_replacements(
        statement, lines, line_length, wrap_string_literal=wrap_string_literal, restore=names.restore
    )
    for body in _nested_bodies(statement):
        yield from _import_run_replacements(body, lines, line_length, known_first_party, names)
        for child in body:
            yield from _collect_statement_replacements(
                child, lines, line_length, known_first_party, wrap_string_literal=wrap_string_literal, names=names
            )


def _collect_builtin_replacements(  # noqa: PLR0913
    tree: ast.Module,
    lines: list[str],
    line_length: int,
    known_first_party: frozenset[str],
    *,
    wrap_string_literal: bool,
    names: _ImportNames = _PLAIN_NAMES,
    import_block: tuple[int, int] = (0, 0),
    moved_comments: list[str] | None = None,
) -> list[_LineReplacement]:
    """Lay out every statement as black does and put black's blank lines between them, keeping every comment.

    The lines of ``import_block``, the first and last line of the module's import block, are rebuilt elsewhere; the
    import runs after it are sorted, and ``moved_comments``, the lines isort moves out of the block, come after it.
    """
    replacements: list[_LineReplacement] = []
    for node in tree.body:
        replacements.extend(
            _collect_statement_replacements(
                node, lines, line_length, known_first_party, wrap_string_literal=wrap_string_literal, names=names
            )
        )
    replacements.extend(
        _import_run_replacements(tree.body, lines, line_length, known_first_party, names, after=import_block[1])
    )
    replacements.extend(_BlankLinePlacer(lines, import_block, moved_comments or []).place(tree))
    return replacements


_DECORATOR, _DEF, _CLASS, _IMPORT, _COMMENT, _DOCSTRING, _STUB_DEF, _OPENS_BLOCK, _DEPENDENT_CLAUSE, _BODY_BELOW = (
    1 << bit for bit in range(10)
)
_INDEPENDENT_CLAUSES = frozenset({"with", "try", "for", "while", "if", "match"})
_FIRST_WORD = re.compile(r"\s*(\w*)")
_CASE_KEYWORD = re.compile(r"\s*case\b")
_DOCSTRING_STARTS = frozenset(b"\"'rRuU")
_LogicalLine: TypeAlias = tuple[int, int, int, int]


def _is_gap_line(line: str) -> bool:
    """Return whether ``line`` holds no code: it is blank or a comment, as the lines between statements are."""
    return not (stripped := line.lstrip()) or stripped[0] == "#"


def _header_end(lines: list[str], start: int, body_start: int) -> int:
    """Return the last line of a clause header that starts on ``start``, above the gap lines before its body."""
    end = body_start - 1
    while end > start and _is_gap_line(lines[end - 1]):
        end -= 1
    return end


def _body_on_header_line(body: list[ast.stmt], lines: list[str]) -> bool:
    return bool(lines[body[0].lineno - 1].encode()[: body[0].col_offset].strip())


def _logical_lines(body: list[ast.stmt], lines: list[str], depth: int) -> Iterator[_LogicalLine]:
    """Yield black's logical lines of ``body`` in source order, each as its first and last line, depth and kind."""
    for index, statement in enumerate(body):
        for decorator in getattr(statement, "decorator_list", ()):
            start = decorator.lineno
            while not lines[start - 1].lstrip().startswith("@"):
                start -= 1
            yield start, decorator.end_lineno or decorator.lineno, depth, _DECORATOR
        if isinstance(statement, _CLAUSE_STATEMENTS):
            for start, clause_body, clause_depth, flags in _clauses(statement, lines, depth):
                yield from _clause_lines(start, clause_body, lines, clause_depth, flags)
            continue
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            flags = _IMPORT
        elif (
            not index
            and isinstance(statement, ast.Expr)
            and _is_string_constant(statement.value)
            and lines[statement.lineno - 1].encode()[statement.col_offset] in _DOCSTRING_STARTS
        ):
            flags = _DOCSTRING
        else:
            flags = 0
        yield statement.lineno, _statement_end(statement, lines), depth, flags


def _clauses(
    statement: _ClauseStatement | ast.Match, lines: list[str], depth: int
) -> Iterator[tuple[int, list[ast.stmt] | None, int, int]]:
    """Yield the clauses of a compound statement: the first line of each header, its body, depth and kind.

    A ``match`` header has no body of its own; its ``case`` clauses are one level deeper.
    """
    if isinstance(statement, ast.Match):
        yield statement.lineno, None, depth, 0
        for case in statement.cases:
            case_line = case.pattern.lineno
            while not _CASE_KEYWORD.match(lines[case_line - 1]):
                case_line -= 1
            yield case_line, case.body, depth + 1, 0
        return
    flags = 0
    if isinstance(statement, ast.ClassDef):
        flags = _CLASS
    elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
        flags = _DEF | (_STUB_DEF if _is_stub_body(statement.body, lines) else 0)
    yield statement.lineno, statement.body, depth, flags
    orelse = getattr(statement, "orelse", [])
    while (
        len(orelse) == 1
        and isinstance(elif_ := orelse[0], ast.If)
        and lines[elif_.lineno - 1].encode()[elif_.col_offset :].startswith(b"elif")
    ):
        yield elif_.lineno, elif_.body, depth, 0
        orelse = elif_.orelse
    for handler in getattr(statement, "handlers", ()):
        yield handler.lineno, handler.body, depth, 0
    for body in (orelse, getattr(statement, "finalbody", None)):
        if body:
            if _body_on_header_line(body, lines):
                yield body[0].lineno, body, depth, 0
            else:
                yield _header_end(lines, 1, _statement_start(body[0], lines)), body, depth, 0


def _clause_lines(
    start: int, body: list[ast.stmt] | None, lines: list[str], depth: int, flags: int
) -> Iterator[_LogicalLine]:
    """Yield a clause header that starts on ``start`` and the logical lines of its body.

    A body on the header's line is read from that line, after the header, as black splits it there, but a definition's
    ``...`` stays on the header's line. The header of a ``match`` statement ends above its first ``case``.
    """
    if body is None:
        case_line = start + 1
        while not _CASE_KEYWORD.match(lines[case_line - 1]):
            case_line += 1
        end = _header_end(lines, start, case_line)
        flags |= _BODY_BELOW
    elif _body_on_header_line(body, lines):
        if _is_stub_body(body, lines) and flags & (_CLASS | _DEF):
            yield start, _statement_end(body[0], lines), depth, flags
            return
        end = body[0].lineno
    else:
        end = _header_end(lines, start, _statement_start(body[0], lines))
        flags |= _BODY_BELOW
    if (match := _FIRST_WORD.match(lines[start - 1])) is not None and match[1] not in _INDEPENDENT_CLAUSES:
        flags |= _DEPENDENT_CLAUSE
    yield start, end, depth, flags | _OPENS_BLOCK
    if body is not None:
        yield from _logical_lines(body, lines, depth + 1)


_LeadingComment: TypeAlias = "tuple[list[int], int | None, int]"


class _EmptyLineTracker:
    """Black's ``EmptyLineTracker`` (stable style) over logical lines given by their kind and depth.

    Each line gets a block ``[before, after]``: the blank lines before and after it. A comment that leads a definition
    gets the definition's blank lines in its own block, set when the definition comes.
    """

    __slots__ = ("code_lines", "defs", "leading_comment", "previous", "previous_block", "previous_depth")

    def __init__(self) -> None:
        self.defs: list[int] = []
        self.previous: int | None = None
        self.previous_depth = 0
        self.previous_block: list[int] = [0, 0]
        self.leading_comment: _LeadingComment | None = None
        self.code_lines = 0

    def add(self, flags: int, depth: int, before: int) -> list[int]:
        """Read the next logical line, with ``before`` blank lines written before it, and return its block."""
        previous, previous_block = self.previous, self.previous_block
        before, after = self._empty_lines(flags, depth, min(before, 1 if depth else 2))
        before = max(0, before - previous_block[1])
        if self._follows_module_docstring(flags):
            before = 1
        block = [before, after]
        if flags & _COMMENT:
            if previous is None or (
                not previous & _DECORATOR and (before or (not previous & _COMMENT and self.leading_comment is None))
            ):
                self.leading_comment = block, previous, previous_block[1]
        else:
            self.code_lines += 1
            if not flags & _DECORATOR or before:
                self.leading_comment = None
        self.previous, self.previous_depth, self.previous_block = flags, depth, block
        return block

    def _follows_module_docstring(self, flags: int) -> bool:
        """Return whether a line other than a definition follows the module docstring, after comments only."""
        return (
            self.previous is not None
            and bool(self.previous & _DOCSTRING)
            and self.code_lines == 1
            and not self.previous_depth
            and not flags & (_CLASS | _DEF)
        )

    def _empty_lines(self, flags: int, depth: int, before: int) -> tuple[int, int]:
        user_had_newline = bool(before)
        previous_def = None
        while self.defs and self.defs[-1] >= depth:
            previous_def = self.defs.pop()
        if flags & (_DEF | _CLASS):
            self.defs.append(depth)
        if (previous := self.previous) is None:
            return 0, 0
        if flags & _DOCSTRING:
            if previous & _CLASS:
                return 0, 1
            if previous & _OPENS_BLOCK and previous & _DEF:
                return 0, 0
        if previous_def is not None:
            before = 1 if depth or (previous_def and flags & _DEPENDENT_CLAUSE) else 2
        if flags & (_DECORATOR | _DEF | _CLASS):
            return self._empty_lines_before_definition(depth, before, user_had_newline=user_had_newline), 0
        if previous & _IMPORT and not flags & _IMPORT and depth == self.previous_depth:
            return (before or 1) if depth else 1, 0
        return before, 0

    def _empty_lines_before_definition(self, depth: int, before: int, *, user_had_newline: bool) -> int:
        previous = cast("int", self.previous)
        if previous & _DECORATOR:
            return 0
        if self.previous_depth < depth and previous & (_CLASS | _DEF):
            return int(user_had_newline)
        leading_comment = None
        if previous & _COMMENT and self.previous_depth == depth and not before:
            leading_comment = self.leading_comment
            if (
                leading_comment is None
                or leading_comment[1] is None
                or leading_comment[1] & (_CLASS | _OPENS_BLOCK)
                or leading_comment[0][0] > 1
            ):
                return 0
        newlines = 1 if depth else 2
        if previous & _STUB_DEF and not user_had_newline:
            newlines = 0
        if leading_comment is not None:
            block, _, previous_after = leading_comment
            block[0] = max(block[0], newlines) - previous_after
            return 0
        return newlines


class _BlankLinePlacer:
    """Put black's blank lines between the logical lines of a module and indent each comment as its block.

    A comment between statements belongs to the block a black parse gives it: one after the last statement of a block
    stays in it while it is indented at least as far, else it moves to the enclosing block. The lines between the
    imports of the module's import block are left as they are; the lines before and after it are always written,
    and the lines after it start with ``moved_comments``.
    """

    __slots__ = ("columns", "import_block", "indents", "lines", "moved_comments", "previous", "replacements", "tracker")

    def __init__(self, lines: list[str], import_block: tuple[int, int], moved_comments: list[str]) -> None:
        self.lines = lines
        self.import_block = import_block
        self.moved_comments = moved_comments
        self.replacements: list[_LineReplacement] = []
        self.tracker = _EmptyLineTracker()
        self.columns: list[int] = []
        self.indents: list[str] = []
        self.previous = (0, 0, 0)

    def place(self, tree: ast.Module) -> list[_LineReplacement]:
        """Return the replacements of the lines between the logical lines of ``tree``."""
        for start, end, depth, flags in _logical_lines(tree.body, self.lines, 0):
            previous_end = self.previous[0]
            if start <= previous_end:
                self.tracker.add(flags, depth, 0)
                self.previous = max(previous_end, end), self.previous[1], self.previous[2]
                continue
            line = self.lines[start - 1]
            indent = line[: len(line) - len(line.lstrip(" \t"))]
            after, comments, blank = self._gap_comments(start - 1, depth, indent)
            self._replace_gap(start - 1, after, comments, self.tracker.add(flags, depth, blank)[0])
            del self.columns[depth:], self.indents[depth:]
            self.columns.append(len(indent))
            self.indents.append(indent)
            self.previous = end, depth, flags
        if self.previous[0] < len(self.lines) or self.moved_comments:
            after, comments, _ = self._gap_comments(len(self.lines), 0, "")
            self._replace_gap(len(self.lines), after, comments, 0)
        return self.replacements

    def _gap_comments(self, end: int, depth: int, indent: str) -> tuple[int, list[tuple[list[int], str]], int]:
        """Read the comments of the gap that ends on ``end``, before a line at ``depth`` indented by ``indent``.

        Return the blank lines after the line before the gap, each comment's block and text, and the blank lines left.
        """
        previous_end, previous_depth, previous_flags = self.previous
        after = self.tracker.previous_block[1]
        comments: list[tuple[list[int], str]] = []
        blank = 0
        level = depth if previous_flags & _BODY_BELOW else previous_depth
        gap = self.lines[previous_end:end]
        if previous_end == self.import_block[1] and self.moved_comments:
            gap = [*self.moved_comments, *gap]
        for line in gap:
            if not (text := line.lstrip()):
                blank += 1
                continue
            if not previous_flags & _BODY_BELOW:
                column = len(line) - len(line.lstrip(" \t"))
                while level > depth and column < self.columns[level]:
                    level -= 1
            comments.append((
                self.tracker.add(_COMMENT, level, blank),
                f"{indent if level == depth else self.indents[level]}{text}",
            ))
            blank = 0
        return after, comments, blank

    def _replace_gap(self, end: int, after: int, comments: list[tuple[list[int], str]], blank: int) -> None:
        previous_end = self.previous[0]
        import_block_start, import_block_end = self.import_block
        if import_block_start <= previous_end < import_block_end:
            return
        new_lines: list[str] = []
        for block, text in comments:
            new_lines.extend([""] * (after + block[0]))
            new_lines.append(text)
            after = block[1]
        new_lines.extend([""] * (after + blank))
        if (
            import_block_end and (end + 1 == import_block_start or previous_end == import_block_end)
        ) or new_lines != self.lines[previous_end:end]:
            self.replacements.append((previous_end + 1, end, new_lines))


def _module_docstring_node(tree: ast.Module) -> ast.Expr | None:
    if not tree.body:
        return None
    return _docstring_node(tree.body[0])


def _docstring_node(first_node: ast.stmt) -> ast.Expr | None:
    if isinstance(first_node, ast.Expr) and _is_string_constant(first_node.value):
        return first_node
    return None


def _iter_module_import_nodes(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    body_nodes = tree.body
    if _module_docstring_node(tree) is not None:
        body_nodes = body_nodes[1:]

    import_nodes: list[ast.Import | ast.ImportFrom] = []
    for node in body_nodes:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            while import_nodes and (import_nodes[-1].end_lineno or import_nodes[-1].lineno) >= node.lineno:
                import_nodes.pop()
            break
        import_nodes.append(node)
    return import_nodes


def _import_block_comments(
    import_nodes: list[ast.Import | ast.ImportFrom], lines: list[str]
) -> tuple[dict[int, list[str]], list[str]]:
    """Split the comment lines between the imports of the module's import block as isort does.

    Return the comment lines right above each import, which move with it, and the lines from each other comment to the
    blank line that ends it, which isort moves below the block.
    """
    comments_above: dict[int, list[str]] = {}
    moved: list[str] = []
    for index in range(1, len(import_nodes)):
        previous = import_nodes[index - 1]
        gap = lines[previous.end_lineno or previous.lineno : import_nodes[index].lineno - 1]
        if not any(gap):
            continue
        split = len(gap)
        while split and gap[split - 1]:
            split -= 1
        if split < len(gap):
            comments_above[index] = [line.strip() for line in gap[split:]]
        if (first := next((line_index for line_index, line in enumerate(gap[:split]) if line), None)) is not None:
            moved.extend(line.strip() for line in gap[first:split])
    return comments_above, moved


def _disjoint_replacements(replacements: list[_LineReplacement]) -> list[_LineReplacement]:
    """Return the replacements in line order, leaving out each one whose lines another one before it replaces.

    A replacement inside another one, such as the blank lines between two imports of a sorted run, is part of it. One
    that ends before it starts inserts its lines there, before a replacement that starts on the same line.
    """
    kept: list[_LineReplacement] = []
    for replacement in sorted(replacements, key=lambda item: (item[0], item[1] >= item[0], -item[1])):
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
    """Put black's blank lines between top-level statements: two around definitions, none after a decorator.

    A definition right after a function whose body is a stub ``...`` on its header line gets none, as black keeps a
    run of ``@overload`` stubs together.
    """
    string_lines: set[int] = set()
    # Multi-line STRING tokens can only come from triple quotes or backslash-continuation.
    if ('"""' in code) or ("'''" in code) or ("\\\n" in code) or ("\\\r\n" in code):
        for token in _string_tokens(code):
            if token.type == tokenize.STRING and token.start[0] != token.end[0]:
                string_lines.update(range(token.start[0], token.end[0] + 1))

    lines = _split_python_lines(code)
    formatted_lines: list[str] = []
    previous_definition = previous_function = False
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
                        previous_line_index == len(formatted_lines) - 1
                        and (
                            formatted_lines[previous_line_index].startswith("#")
                            or (previous_function and formatted_lines[previous_line_index].endswith(": ..."))
                        )
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
                previous_function = line.startswith(_DEF_PREFIXES[1:])
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


def _sub_twice(pattern: re.Pattern[str], replacement: str, text: str) -> str:
    """Replace the matches of ``pattern`` twice, so that matches that share a character are all replaced."""
    return pattern.sub(replacement, pattern.sub(replacement, text))


def _has_escaped_f_string_field(prefix: str, body: str) -> bool:
    return "f" in prefix.casefold() and any("\\" in body[start:end] for start, end in iter_fexpr_spans(body))


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
    blank_lines_placed: bool = False,
) -> str:
    """End the code with one newline, put black's top-level blank lines unless they are placed, and normalize quotes."""
    formatted_code = code.strip("\n") if blank_lines_placed else _normalize_top_level_blank_lines(code.strip("\n"))
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
    trailing_backslash = ends_with_quote = False
    if docstring:
        if docstring[0] == quote_char:
            docstring = f" {docstring}"
        if ends_with_quote := docstring[-1] == quote_char:
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
        return f"{prefix}{quote}{docstring[:-1] if ends_with_quote else docstring}\n{indent}{quote}"
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


_COMMENT_EXCEPTIONS = " !:#'"
_FMT_DIRECTIVES = frozenset({
    "# fmt: off",
    "# fmt:off",
    "# yapf: disable",
    "# fmt: skip",
    "# fmt:skip",
    "# fmt: on",
    "# fmt:on",
    "# yapf: enable",
})
_F_STRING_STARTS = frozenset(
    getattr(tokenize, name) for name in ("FSTRING_START", "TSTRING_START") if hasattr(tokenize, name)
)
_F_STRING_ENDS = frozenset(
    getattr(tokenize, name) for name in ("FSTRING_END", "TSTRING_END") if hasattr(tokenize, name)
)


def _is_type_comment(text: str) -> bool:
    return text.startswith("#") and text[1:].lstrip().startswith("type:")


def _has_fmt_directive(comment: str) -> bool:
    """Return whether ``comment`` holds a ``fmt``/``yapf`` directive, alone or among others, as black reads it."""
    return (
        comment in _FMT_DIRECTIVES
        or any(f"# {part.strip()}" in _FMT_DIRECTIVES for part in comment.split("# ")[1:])
        or any(f"# {part.strip()}" in _FMT_DIRECTIVES for part in comment.strip("# ").split(";"))
    )


def _black_comment(comment: str) -> str:
    """Return ``comment`` as black writes it: one space after the hash, except before ``!``, ``:``, ``#``, ``'``.

    A type comment is spelled ``# type: <value>`` and a leading no-break space becomes a space.
    """
    content = comment[1:]
    if content[:1] == "\N{NO-BREAK SPACE}" and not _is_type_comment(f"# {content.lstrip()}"):
        content = f" {content[1:]}"
    if "\N{NO-BREAK SPACE}" not in content and _is_type_comment(f"#{content}"):
        type_part, value_part = content.split(":", 1)
        content = f"{type_part.strip()}: {value_part.strip()}"
    if content and content[0] not in _COMMENT_EXCEPTIONS:
        content = f" {content}"
    return f"#{content}"


def _normalize_comments(code: str) -> str:
    """Write each comment as black does, with two spaces between code and a comment after it.

    A comment in an f-string's replacement field, and the spacing before a comment with a ``fmt`` directive, stay as
    they are. Code the tokenizer rejects stays as it is.
    """
    if "#" not in code:
        return code
    lines = code.split("\n")
    f_string_depth = 0
    try:
        for token in tokenize.generate_tokens(StringIO(code).readline):
            if token.type in _F_STRING_STARTS:
                f_string_depth += 1
            elif token.type in _F_STRING_ENDS:
                f_string_depth -= 1
            elif token.type == tokenize.COMMENT and not f_string_depth:
                if _has_fmt_directive(token.string):
                    continue
                row, column = token.start
                line = lines[row - 1]
                comment = _black_comment(token.string)
                code_before = line[:column].rstrip()
                lines[row - 1] = f"{code_before}  {comment}" if code_before else f"{line[:column]}{comment}"
    except (tokenize.TokenError, SyntaxError):
        return code
    return "\n".join(lines)


_INDENT = "    "


def _is_misindented(tree: ast.Module, lines: list[str]) -> bool:
    """Return whether a logical line of ``tree`` is not indented by four spaces for each block it is in."""
    previous_end = 0
    for start, end, depth, _ in _logical_lines(tree.body, lines, 0):
        if start > previous_end and (
            not (line := lines[start - 1]).startswith(_INDENT * depth) or line[len(_INDENT) * depth] in " \t"
        ):
            return True
        previous_end = max(previous_end, end)
    return False


def _reindented(code: str) -> str:
    """Indent ``code``, which parses, by four spaces for each block, as black does.

    The lines that continue a statement move with its first line, a comment line between statements takes the indent
    of the deepest open block it is indented as far as, and the lines inside a string stay as they are.
    """
    lines = code.split("\n")
    columns = [0]
    first_row = delta = 0
    string_rows: set[int] = set()
    f_string_rows: list[int] = []
    for token in tokenize.generate_tokens(StringIO(code).readline):
        kind, (row, column) = token.type, token.start
        if kind == tokenize.INDENT:
            columns.append(len(token.string))
        elif kind == tokenize.DEDENT:
            columns.pop()
        elif kind in _F_STRING_STARTS:
            f_string_rows.append(row)
        elif kind in _F_STRING_ENDS or kind == tokenize.STRING:
            string_rows.update(range((f_string_rows.pop() if kind in _F_STRING_ENDS else row) + 1, token.end[0] + 1))
        if kind in _LAYOUT_TOKENS:
            continue
        if first_row:
            if kind == tokenize.NEWLINE:
                _shift_lines(lines, range(first_row, row), delta, string_rows)
                first_row = 0
        elif kind == tokenize.COMMENT:
            lines[row - 1] = f"{_INDENT * sum(1 for indent in columns[1:] if indent <= column)}{token.string}"
        else:
            first_row = row
            delta = len(_INDENT) * (len(columns) - 1) - column
            lines[row - 1] = f"{_INDENT * (len(columns) - 1)}{lines[row - 1][column:]}"
    return "\n".join(lines)


_LAYOUT_TOKENS = frozenset({tokenize.INDENT, tokenize.DEDENT, tokenize.NL, tokenize.ENDMARKER})


def _shift_lines(lines: list[str], indexes: range, delta: int, string_rows: set[int]) -> None:
    """Move the lines at ``indexes`` right by ``delta`` columns, but not those a string continues on."""
    for index in indexes:
        if index + 1 not in string_rows and (line := lines[index]):
            lead = len(line) - len(line.lstrip(" \t"))
            lines[index] = f"{' ' * max(0, lead + delta)}{line[lead:]}"


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
    stripped = _strip_line_ends(normalize_newlines(code), line_length)
    lines = _split_python_lines(_normalize_comments(stripped))
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
        # Code that does not parse (also newer-target syntax the running Python cannot read) keeps its comments as
        # written: the tokenizer may read part of a string as a comment.
        written = "\n".join(_split_python_lines(stripped)).strip("\n")
        return f"{written}\n"
    if _is_misindented(tree, lines):
        reindented = _strip_line_ends(_reindented(code), line_length)
        reindented_tree, reindented_is_valid = _parse_builtin_code_with_source_validity(reindented, python_version)
        if reindented_tree is not None:
            code, tree, source_is_valid = reindented, reindented_tree, reindented_is_valid
            lines = _split_python_lines(code)
    if (
        string_normalization
        and (normalized := _normalize_string_quotes_if_needed(code, source_is_valid=source_is_valid)) != code
        and (normalized_tree := _parse_builtin_code_with_source_validity(normalized, python_version)[0]) is not None
    ):
        code, tree = normalized, normalized_tree
        lines = _split_python_lines(code)
    if lent is not None:
        _rename_lent_identifiers(tree, lent)

    import_nodes = _iter_module_import_nodes(tree)
    comments_above, moved_comments = _import_block_comments(import_nodes, lines)
    replacements = _collect_builtin_replacements(
        tree,
        lines,
        line_length,
        known_first_party,
        wrap_string_literal=wrap_string_literal,
        names=names,
        import_block=(import_nodes[0].lineno, import_nodes[-1].end_lineno or import_nodes[-1].lineno)
        if import_nodes
        else (0, 0),
        moved_comments=moved_comments,
    )

    if not import_nodes:
        formatted_lines = _apply_line_replacements(lines, replacements)
        return _finalize_builtin_code(
            "\n".join(formatted_lines),
            string_normalization=string_normalization and wrap_string_literal,
            source_is_valid=source_is_valid,
            blank_lines_placed=True,
        )

    first_line = import_nodes[0].lineno
    last_line = import_nodes[-1].end_lineno or import_nodes[-1].lineno
    leading = "\n".join(
        _apply_line_replacements(lines[: first_line - 1], [item for item in replacements if item[1] < first_line])
    )
    body_lines = _apply_line_replacements(lines[last_line:], replacements, offset=last_line)
    body = "\n".join(body_lines).rstrip("\n")
    import_block = _build_builtin_import_block(
        import_nodes, line_length, lines, known_first_party, names, comments_above=comments_above
    )
    formatted_code = f"{leading}\n{import_block}" if leading else import_block
    return _finalize_builtin_code(
        f"{formatted_code}\n{body}" if body else formatted_code,
        string_normalization=string_normalization and wrap_string_literal,
        source_is_valid=source_is_valid,
        blank_lines_placed=True,
    )
