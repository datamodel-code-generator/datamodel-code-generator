"""The private-scope-walk rule: syntax classes, symtable and comment tokens read by origin, not by spelling.

A class of ``ast`` read by any alias or by name counts; building a node, an attribute of a node and a class outside the
binding, scope and import forms do not.
"""

import ast
import ast as syntax
import symtable
import tokenize
from ast import FunctionDef


def stores(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        match node:
            case syntax.Name(ctx=ast.Store()):
                names.append(node.id)
            case ast.Call():
                pass
    return names


def definitions(tree: ast.Module) -> list[ast.stmt]:
    return [node for node in ast.walk(tree) if isinstance(node, FunctionDef)]


def built() -> ast.expr:
    return syntax.Name(id="x", ctx=syntax.Load())


def table(text: str) -> list[str]:
    return symtable.symtable(text, "m", "exec").get_identifiers()


def comment(token: tokenize.TokenInfo) -> bool:
    return token.type == tokenize.COMMENT and token.string.startswith("#")


class Finder(ast.NodeVisitor):
    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
