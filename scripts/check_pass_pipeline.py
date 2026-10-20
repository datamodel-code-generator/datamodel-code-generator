"""Check the pass pipeline of ``Parser.parse()``, and record which passes one generation runs.

Every run checks the tables of ``src/datamodel_code_generator/parser/base.py`` (``Parser`` runs them through
``parser/_pipeline.py``):

* the table runs every declaration of ``parser/_pass_decls.py`` once, in the order ``PASS_DECLS`` lists them;
* each pass runs in the phase its segment enters, module passes in module bodies and global passes elsewhere;
* the segments enter the boundaries in the order ``BOUNDARIES`` gives;
* the passes of each segment satisfy the order rule (``check_order``);
* the repair passes are those ``REPAIRS`` lists, a set that only shrinks;
* no code outside the body of ``Parser`` reaches a name-mangled member of ``Parser``, which the tables call;
* every ``warn()`` of the parser, model and type modules whose ``stacklevel`` reaches past its caller is listed in
  ``DEEP_WARNINGS`` with the frame it names, so that a warning the pipeline would attribute to its own frames shows.

A trace (``--trace``) has one line per pass that runs, named as in ``parser/_pass_decls.py``, and a ``-- module`` line
where the pipeline starts processing each output module. The test suite compares the traces of a few inputs with stored
ones, so that a change to the pass order shows in review, and checks that some input runs every declared pass.

Usage:
    python scripts/check_pass_pipeline.py
    python scripts/check_pass_pipeline.py --trace "<datamodel-codegen arguments>"
"""

from __future__ import annotations

import argparse
import ast
import shlex
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "datamodel_code_generator"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from datamodel_code_generator.__main__ import main as generate_main  # noqa: E402
from datamodel_code_generator.parser._facts import Fact, Phase, RenderFact  # noqa: E402, PLC2701
from datamodel_code_generator.parser._finality import MODULE_TRACE_MARK, recording_pass_trace  # noqa: E402, PLC2701
from datamodel_code_generator.parser._pass_decls import (  # noqa: E402, PLC2701
    PASS_DECLS,
    After,
    PassDecl,
    PassKind,
    Scope,
)
from datamodel_code_generator.parser._pipeline import (  # noqa: E402, PLC2701
    Boundary,
    ForEachModule,
    HookCall,
    PassSpec,
    Segment,
    StepLike,
    check_order,
    flatten,
    segment_bodies,
)
from datamodel_code_generator.parser.base import Parser  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

__all__ = ["After", "Fact", "PassDecl", "PassKind", "Phase", "RenderFact", "Scope", "check_order"]

PIPELINE: tuple[StepLike[Any, Any], ...] = Parser._Parser__PIPELINE  # noqa: SLF001
BOUNDARIES = (Boundary.PARSED, Boundary.FACTS_FINAL, Boundary.DECIDED, Boundary.RENDER_READY)
"""The boundaries the segments enter, in order; ``MODULE_DONE`` ends each module body."""
REPAIRS = frozenset({
    "bind_module_field_names",
    "fix_tag_opt_out_field_ordering",
    "import_enum_member_literal_classes",
    "import_removed_reuse_canonicals",
    "import_renamed_types",
    "refresh_sequence_interface_after_renames",
    "remove_unused_imports",
    "replan_schema_validators_after_renames",
})
"""The repair passes: a change that removes one removes its entry, and no entry is added."""
DEEP_WARNINGS = {
    "model/pydantic_v2/base_model.py:_warn_template_update": "the caller of render() (L7)",
    "parser/_intersection.py:warn_unsatisfiable": "the parser method that met the unsatisfiable schema (L1)",
    "parser/_intersection.py:warn_untaken_patterns": "the parser method that conjoined the patterns (L1)",
    "parser/jsonschema.py:_build_allof_type": "the parser method that built the allOf (L1)",
    "parser/jsonschema.py:_check_array_version_features": "the parser method that checked the array (L1)",
    "parser/jsonschema.py:_check_version_specific_features": "the parser method that checked the schema (L1)",
    "parser/jsonschema.py:_report_parse_diagnostics": "the caller of Parser.parse() (stacklevel_at_parse_caller)",
    "parser/jsonschema.py:_resolve_local_ref_path": "the parser method that resolved the reference (L0)",
    "parser/protobuf.py:_effective_version": "the parser method that read the syntax (L1)",
}
"""Each ``warn()`` with a ``stacklevel`` above 2, by ``file:function``, with the frame its location names."""
TEMPORARY_DIRECTORY = "{tmp}"
"""Replaced, in traced arguments, by a temporary directory that the run may write files into."""
WARNING_FUNCTIONS = frozenset({"warn", "warn_deprecated"})
CALLER_STACKLEVEL = 2
"""The ``stacklevel`` that names the caller of the function that warns."""


def _scoped(steps: Iterable[StepLike[Any, Any]], *, per_module: bool = False) -> Iterator[tuple[PassDecl, bool]]:
    """Yield each pass of ``steps`` with whether it runs per module, in table order."""
    for step in steps:
        match step:
            case PassSpec(decl=decl):
                yield decl, per_module
            case ForEachModule(body=body):
                yield from _scoped(body, per_module=True)
            case HookCall(body=body):
                yield from _scoped(body, per_module=per_module or step.per_module)
            case Segment(steps=inner):
                yield from _scoped(inner, per_module=per_module)


def _boundaries(steps: Iterable[StepLike[Any, Any]]) -> Iterator[Boundary]:
    for step in steps:
        match step:
            case Segment(enter=enter, steps=inner):
                if enter is not None:
                    yield enter
                yield from _boundaries(inner)
            case HookCall(body=body):
                yield from _boundaries(body)


def table_problems(pipeline: tuple[StepLike[Any, Any], ...] = PIPELINE) -> list[str]:
    """Return what is wrong with the pipeline tables, against the declarations and the rules above."""
    problems: list[str] = []
    table = [decl.name for decl in flatten(pipeline)]
    if table != (declared := [decl.name for decl in PASS_DECLS]):
        problems.append(f"the table runs {table}, not the declarations in PASS_DECLS order {declared}")
    problems.extend(
        f"{decl.name} declares the {decl.scope.value} scope but runs {'per module' if per_module else 'once'}"
        for decl, per_module in _scoped(pipeline)
        if (decl.scope is Scope.MODULE) is not per_module
    )
    for segment, body in segment_bodies(pipeline):
        problems.extend(
            f"{decl.name} declares {decl.phase.name} but runs in the {segment.name} segment of {segment.phase.name}"
            for decl in body
            if decl.phase is not segment.phase
        )
        problems.extend(f"{segment.name}: {problem}" for problem in check_order(body).problems)
    if (entered := tuple(_boundaries(pipeline))) != BOUNDARIES:
        problems.append(f"the segments enter {[b.name for b in entered]}, not {[b.name for b in BOUNDARIES]}")
    repairs = {decl.name for decl in PASS_DECLS if decl.kind is PassKind.REPAIR}
    problems.extend(f"repair pass {name} is new: the repair set only shrinks" for name in sorted(repairs - REPAIRS))
    problems.extend(f"REPAIRS lists {name}, which is no repair pass: remove it" for name in sorted(REPAIRS - repairs))
    return problems


def provisional_reads(pipeline: tuple[StepLike[Any, Any], ...] = PIPELINE) -> list[str]:
    """Return the provisional reads the explicit edges of every segment record, in table order."""
    return [read for _, body in segment_bodies(pipeline) for read in check_order(body).provisional_reads]


def _parser_body(tree: ast.Module) -> ast.ClassDef:
    return next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Parser")


def _mangled_members(parser_body: ast.ClassDef) -> frozenset[str]:
    """Return the ``__name`` members that the body of ``Parser`` defines."""
    names: set[str] = set()
    for node in parser_body.body:
        match node:
            case ast.FunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
                names.add(name)
            case ast.Assign(targets=targets):
                names.update(target.id for target in targets if isinstance(target, ast.Name))
    return frozenset(name for name in names if name.startswith("__") and not name.endswith("__"))


def _reached_member(node: ast.AST, members: frozenset[str]) -> str | None:
    """Return the mangled ``Parser`` member that ``node`` reaches, by attribute or by a string ``*attr`` argument."""
    attr = node.attr if isinstance(node, ast.Attribute) else _attribute_argument(node)
    return attr if attr is not None and (attr.startswith("_Parser__") or attr in members) else None


def _attribute_argument(node: ast.AST) -> str | None:
    """Return the string attribute name a ``getattr``-family call passes, if ``node`` is one."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
        return None
    if node.func.id not in {"getattr", "setattr", "hasattr", "delattr"} or len(node.args) < 2:  # noqa: PLR2004
        return None
    name = node.args[1]
    return name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else None


def mangling_problems(package: Path = PACKAGE) -> list[str]:
    """Return the places outside the body of ``Parser`` that reach a name-mangled member of ``Parser``."""
    base = package / "parser" / "base.py"
    members = _mangled_members(_parser_body(ast.parse(base.read_text(encoding="utf-8"))))
    problems: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        inside = {id(node) for node in ast.walk(_parser_body(tree))} if path == base else set()
        problems.extend(
            f"{path.relative_to(package).as_posix()}:{node.lineno} reaches Parser.{attr} outside the body of Parser"
            for node in ast.walk(tree)
            if id(node) not in inside and (attr := _reached_member(node, members)) is not None
        )
    return problems


def _deep_warning(call: ast.Call) -> bool:
    """Return whether ``call`` warns with a ``stacklevel`` that is a call or a number above 2."""
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
    return name in WARNING_FUNCTIONS and any(
        keyword.arg == "stacklevel"
        and (
            isinstance(keyword.value, ast.Call)
            or (isinstance(keyword.value, ast.Constant) and keyword.value.value > CALLER_STACKLEVEL)
        )
        for keyword in call.keywords
    )


def warning_problems(package: Path = PACKAGE) -> list[str]:
    """Return the deep warnings of the parser, model and type modules that ``DEEP_WARNINGS`` does not list.

    The entries that no warning matches any more are problems too.
    """
    sites: set[str] = set()
    for path in sorted({*package.glob("parser/**/*.py"), *package.glob("model/**/*.py"), package / "types.py"}):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if isinstance(function, ast.FunctionDef):
                sites.update(
                    f"{path.relative_to(package).as_posix()}:{function.name}"
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call) and _deep_warning(node)
                )
    return [
        *(
            f"{site} warns with a stacklevel above 2: list the frame it names in DEEP_WARNINGS"
            for site in sorted(sites - DEEP_WARNINGS.keys())
        ),
        *(
            f"DEEP_WARNINGS lists {site}, which no longer warns that deep: remove it"
            for site in sorted(DEEP_WARNINGS.keys() - sites)
        ),
    ]


def call_trace(call: Callable[[], object]) -> list[str]:
    """Return the pass trace of ``call``, which runs one or more generations."""
    with recording_pass_trace() as trace:
        call()
    return trace


def pass_trace(arguments: str) -> str:
    """Return the pass trace of one ``datamodel-codegen`` run with ``arguments``, and its exit status."""
    with TemporaryDirectory() as directory:
        argv = [*shlex.split(arguments.replace(TEMPORARY_DIRECTORY, directory)), "--output", f"{directory}/output"]
        status: list[int] = []
        trace = call_trace(lambda: status.append(int(generate_main(argv))))
    return "".join(f"{line}\n" for line in [*trace, f"exit status: {status[0]}"])


def unrun_passes(traces: Iterable[str]) -> list[str]:
    """Return the declared passes that none of ``traces`` runs, in declaration order."""
    run = {line for trace in traces for line in trace.splitlines() if line != MODULE_TRACE_MARK}
    return [decl.name for decl in PASS_DECLS if decl.name not in run]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="check_pass_pipeline.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--trace", metavar="ARGUMENTS", help="print the pass trace of these datamodel-codegen arguments"
    )
    args = parser.parse_args(argv)
    if args.trace is not None:
        sys.stdout.write(pass_trace(args.trace))
        return 0
    problems = [*table_problems(), *mangling_problems(), *warning_problems()]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
