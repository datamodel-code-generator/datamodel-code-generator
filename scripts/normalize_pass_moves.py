"""Show what changed, beyond the mechanical rewrite, in code moved from ``parser/base.py`` into ``parser/_passes/``.

A pass moves from a ``Parser`` method to a function of a pass module by a mechanical rewrite: ``self`` (``cls`` of a
classmethod) becomes the ``parser`` parameter (``host`` in ``_shared.py``) or goes when the function does not read the
parser, ``self.__helper(...)`` becomes ``helper(parser, ...)``, the method name loses its leading underscores and the
parameters take the types of the pass modules. This script undoes that
rewrite on the AST of both sides, so formatting, comments, docstring indentation, parameter annotations and the rewrite
itself drop out, and prints every definition added to ``parser/_passes/`` since ``BASE`` as identical, changed (with a
diff of the normalized code) or new (no definition of that name in ``parser/base.py`` at ``BASE``).

Usage: python scripts/normalize_pass_moves.py BASE [--rename OLD=NEW ...]
"""

from __future__ import annotations

import argparse
import ast
import difflib
import inspect
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSER = "src/datamodel_code_generator/parser"
PASSES = f"{PARSER}/_passes"


def _source_at(base: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{base}:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _definitions(tree: ast.Module) -> dict[str, ast.stmt]:
    found: dict[str, ast.stmt] = {}
    for node in tree.body:
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                found[node.name] = node
            case ast.Assign(targets=[ast.Name() as target]) | ast.AnnAssign(target=ast.Name() as target):
                found[target.id] = node
            case _:
                pass
    return found


_PARSER_PARAMETERS = frozenset({"host", "parser"})
_RECEIVER_DECORATORS = frozenset({"classmethod", "staticmethod"})


def _takes_parser(node: ast.stmt) -> bool:
    return isinstance(node, ast.FunctionDef) and bool(node.args.args) and node.args.args[0].arg in _PARSER_PARAMETERS


class _UndoRewrite(ast.NodeTransformer):
    """Rewrite a ``Parser`` method the way its move does: ``self`` to ``parser``, private calls to functions."""

    def __init__(self, renames: dict[str, str], takes_parser: dict[str, bool]) -> None:
        self.renames = renames
        self.takes_parser = takes_parser

    def function_name(self, method: str) -> str:
        return self.renames.get(method, method.removeprefix("__"))

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "parser"
            and func.attr.startswith("__")
            and not func.attr.endswith("__")
        ):
            return node
        name = self.function_name(func.attr)
        args = [ast.Name("parser", ast.Load()), *node.args] if self.takes_parser.get(name, True) else node.args
        return ast.Call(ast.Name(name, ast.Load()), args, node.keywords)

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: PLR6301
        return ast.Name("parser", node.ctx) if node.id == "self" else node


def _without_parameter_annotations(node: ast.stmt) -> str:
    if isinstance(node, ast.FunctionDef):
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            argument.annotation = None
    for child in ast.walk(node):
        match child:
            case (
                ast.FunctionDef(body=[ast.Expr(value=ast.Constant(value=str() as docstring)) as expr, *_])
                | ast.ClassDef(body=[ast.Expr(value=ast.Constant(value=str() as docstring)) as expr, *_])
            ):
                expr.value = ast.Constant(inspect.cleandoc(docstring))
            case _:
                pass
    return ast.unparse(node)


def _as_instance_method(node: ast.stmt) -> ast.stmt:
    """Rewrite a classmethod or staticmethod as the instance method that its move treats it as."""
    if not isinstance(node, ast.FunctionDef):
        return node
    kinds = {decorator.id for decorator in node.decorator_list if isinstance(decorator, ast.Name)}
    node.decorator_list = [
        decorator
        for decorator in node.decorator_list
        if not (isinstance(decorator, ast.Name) and decorator.id in _RECEIVER_DECORATORS)
    ]
    if "staticmethod" in kinds:
        node.args.args.insert(0, ast.arg("self"))
    elif "classmethod" in kinds and node.args.args:
        node = _Renamed(node.args.args[0].arg, "self").visit(node)
        node.args.args[0].arg = "self"
    return node


def _normalized_old(node: ast.stmt, name: str, transformer: _UndoRewrite, *, takes_parser: bool) -> str:
    node = transformer.visit(_as_instance_method(ast.parse(ast.unparse(node)).body[0]))
    if isinstance(node, ast.FunctionDef):
        node.name = name
        if node.args.args and node.args.args[0].arg == "self":
            if takes_parser:
                node.args.args[0].arg = "parser"
            else:
                del node.args.args[0]
    return _without_parameter_annotations(node)


def _normalized_new(node: ast.stmt) -> str:
    node = ast.parse(ast.unparse(node)).body[0]
    if _takes_parser(node) and (name := node.args.args[0].arg) != "parser":
        node = _Renamed(name).visit(node)
        node.args.args[0].arg = "parser"
    return _without_parameter_annotations(node)


class _Renamed(ast.NodeTransformer):
    """Rename a parameter: the parser parameter of a shared helper (``host``) to ``parser``, ``cls`` to ``self``."""

    def __init__(self, name: str, target: str = "parser") -> None:
        self.name = name
        self.target = target

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.Name(self.target, node.ctx) if node.id == self.name else node


def main() -> int:
    """Print each definition added to the pass modules since the base and how it differs from its old form."""
    arguments = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    arguments.add_argument("base")
    arguments.add_argument("--rename", action="append", default=[], metavar="OLD=NEW")
    options = arguments.parse_args()
    renames = dict(item.split("=", 1) for item in options.rename)
    old_base = ast.parse(_source_at(options.base, f"{PARSER}/base.py") or "")
    old_module = _definitions(old_base)
    parser_class = old_module.get("Parser")
    old_methods = _definitions(ast.Module(parser_class.body, [])) if isinstance(parser_class, ast.ClassDef) else {}
    new_by_name: dict[str, tuple[str, ast.stmt]] = {}
    for path in sorted((ROOT / PASSES).glob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        before = _definitions(ast.parse(_source_at(options.base, relative) or ""))
        for name, node in _definitions(ast.parse(path.read_text(encoding="utf-8"))).items():
            if name not in before:
                new_by_name[name] = (relative, node)
    takes_parser = {name: _takes_parser(node) for name, (_, node) in new_by_name.items()}
    transformer = _UndoRewrite(renames, takes_parser)
    old_by_new_name = {transformer.function_name(method): method for method in old_methods if method.startswith("__")}
    changed = 0
    for name, (relative, node) in new_by_name.items():
        if (method := old_by_new_name.get(name)) is not None:
            old = _normalized_old(old_methods[method], name, transformer, takes_parser=takes_parser[name])
            label = f"Parser.{method}"
        elif name in old_module:
            old, label = _without_parameter_annotations(old_module[name]), name
        else:
            print(f"new        {relative}:{name}")
            continue
        new = _normalized_new(node)
        if old == new:
            print(f"identical  {relative}:{name} <- {label}")
            continue
        changed += 1
        print(f"changed    {relative}:{name} <- {label}")
        sys.stdout.writelines(
            difflib.unified_diff(
                old.splitlines(keepends=True), new.splitlines(keepends=True), f"base.py:{label}", f"{relative}:{name}"
            )
        )
        print()
    print(f"{len(new_by_name)} definitions, {changed} changed beyond the mechanical rewrite")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
