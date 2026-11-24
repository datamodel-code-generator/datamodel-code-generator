"""Check the pass pipeline of ``Parser.parse()``, and record which passes one generation runs.

Every run checks the tables of ``src/datamodel_code_generator/parser/_passes/_tables.py`` (``Parser`` runs them
through ``parser/_pipeline.py``) against ``scripts/pass_decls.py``, which declares each pass by the name of its
``PassSpec`` and labels the tables (``BOUNDARY_BEFORE``, ``HOOK_BODIES``):

* the table runs every declaration of ``PASS_DECLS`` once, in the order ``PASS_DECLS`` lists them;
* each pass runs in the phase its segment enters, module passes in module bodies and global passes elsewhere;
* the labels name segments and hooks of the tables, every hook has its body, and the segments enter the boundaries in
  the order ``BOUNDARIES`` gives;
* the passes of each segment satisfy the order rule (``check_order``);
* a ``PassSpec`` has a ``trigger`` exactly where its declaration has one, the trigger reads only facts the pass
  reads, so that the order rule covers what it reads, and only the passes ``TRIGGERS`` lists have one, each with the
  reason its exact form is not the skip (``Trigger.kept_because``);
* a ``when`` names only what the run's options set (``CONFIGURATION``), so that nothing produced gates a pass except a
  ``trigger``;
* the repair passes are those ``REPAIRS`` lists, a set that only shrinks;
* the decision registry of ``Parser`` and of every parser class that sets one (``_DECISION_PASSES``) holds global
  transforms of ``DECIDE`` with unique names, declared in ``DECISION_DECLS``, that read and write facts only, name
  their issue, and satisfy the order rule, which computes the order the registry lists them in;
* every ``warn()`` of the parser, model and type modules whose ``stacklevel`` reaches past its caller is listed in
  ``DEEP_WARNINGS`` with the frame it names, so that a warning the pipeline would attribute to its own frames shows.

A trace (``--trace``) has one line per pass that runs, named as in ``scripts/pass_decls.py``, and a ``-- module`` line
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
from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import TYPE_CHECKING, Any, NamedTuple, cast

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PACKAGE = SRC / "datamodel_code_generator"

for path in (SRC, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from datamodel_code_generator.__main__ import main as generate_main  # noqa: E402
from datamodel_code_generator.parser._facts import Phase  # noqa: E402, PLC2701
from datamodel_code_generator.parser._passes import _tables  # noqa: E402, PLC2701
from datamodel_code_generator.parser._passes._tables import PIPELINE  # noqa: E402, PLC2701
from datamodel_code_generator.parser._phases import DecisionPass, FinalFacts  # noqa: E402, PLC2701
from datamodel_code_generator.parser._pipeline import (  # noqa: E402, PLC2701
    ForEachModule,
    HookCall,
    PassSpec,
    Segment,
    StepLike,
)
from datamodel_code_generator.parser.base import Parser  # noqa: E402
from scripts._source_model import SourceModule  # noqa: E402
from scripts.fact_tables import Fact, RenderFact  # noqa: E402
from scripts.pass_decls import (  # noqa: E402
    BOUNDARY_BEFORE,
    DECISION_DECLS,
    HOOK_BODIES,
    PASS_DECLS,
    After,
    Boundary,
    PassDecl,
    PassKind,
    Scope,
    Trigger,
)
from tests.checks.trace import MODULE_TRACE_MARK, recording_pass_trace  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence


__all__ = [
    "DECISION_DECLS",
    "After",
    "DecisionPass",
    "Fact",
    "FinalFacts",
    "OrderCheck",
    "PassDecl",
    "PassKind",
    "Phase",
    "RenderFact",
    "Scope",
    "Trigger",
    "check_order",
    "decision_order",
]

BOUNDARIES = (Boundary.PARSED, Boundary.FACTS_FINAL, Boundary.DECIDED, Boundary.RENDER_READY)
"""The boundaries the segments enter, in order; ``MODULE_DONE`` ends each module body."""
TRIGGERS = frozenset({
    "alias_builtin_shadowing_module_imports",
    "fix_constructor_field_ordering",
    "fix_constructor_field_ordering_early",
    "import_enum_member_literal_classes",
    "import_models_of_hidden_submodules",
    "resolve_circular_imports",
})
"""The passes that may have a trigger, a set that only shrinks: a pass skips by the first statement of its body that
returns on an empty source. A change that removes a trigger removes its entry, and no entry is added."""
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
CONFIGURATION = frozenset({
    "ReuseScope",
    "DefaultValueType",
    "_PYDANTIC_FLOORS",
    "_import_overrides",
    "_model_type_override_imports",
    "_output_model_context",
    "_tag_capabilities",
    "_writes_unique_sets_in_modules",
    "alias_loop_failure",
    "args",
    "bool",
    "collapse_root_models",
    "collecting_class_names",
    "config",
    "custom_template_dir",
    "data_model_field_type",
    "data_model_type",
    "deserialize_default_value_types",
    "follows_hiding_field_renames",
    "generate_schema_validators",
    "generic_base_class_config",
    "keep_model_order",
    "model_resolver.model_name_record",
    "order_constructor_fields_by_final_names",
    "order_constructor_fields_by_written_defaults",
    "renames_hiding_fields",
    "reuse_model",
    "reuse_scope",
    "run_context",
    "set_default_enum_member",
    "suppress_settle_diagnostics",
    "type_overrides",
    "use_annotated",
    "use_default_factory_for_optional_nested_models",
    "use_generic_base_class",
    "use_one_literal_as_default",
    "use_root_model_sequence_interface",
    "with_import",
})
"""What a ``when`` may name: the options of the run (attributes ``Parser.__init__`` sets from the configuration, the
settings of a run's state), constants of the output classes, and the helpers that read nothing else. An attribute
chain of a parameter is an option where it or a leading part of it is listed: ``self.config.aliases`` names
``config``, and only ``model_name_record`` of ``self.model_resolver`` is an option."""
DEEP_WARNINGS = {
    "model/pydantic_v2/base_model.py:_warn_template_update": "the caller of render() (L7)",
    "parser/_intersection.py:warn_unsatisfiable": "the parser method that met the unsatisfiable schema (L1)",
    "parser/_intersection.py:warn_untaken_patterns": "the parser method that conjoined the patterns (L1)",
    "parser/jsonschema.py:_build_allof_type": "the parser method that built the allOf (L1)",
    "parser/jsonschema.py:_check_array_version_features": "the parser method that checked the array (L1)",
    "parser/jsonschema.py:_check_version_specific_features": "the parser method that checked the schema (L1)",
    "parser/protobuf.py:_effective_version": "the parser method that read the syntax (L1)",
}
"""Each ``warn()`` with a ``stacklevel`` above 2, by ``file:function``, with the frame its location names."""
TEMPORARY_DIRECTORY = "{tmp}"
"""Replaced, in traced arguments, by a temporary directory that the run may write files into."""
WARNING_FUNCTIONS = frozenset({"warn", "warn_deprecated"})
CALLER_STACKLEVEL = 2
"""The ``stacklevel`` that names the caller of the function that warns."""


DECLARED = {decl.name: decl for decl in PASS_DECLS}
"""Every declaration of a table pass, by name."""


def hook_body(hook: HookCall[Any, Any]) -> tuple[StepLike[Any, Any], ...]:
    """Return the table that the default implementation of ``hook`` runs, as ``HOOK_BODIES`` names it."""
    return getattr(_tables, HOOK_BODIES[hook.name])


def _steps(step: StepLike[Any, Any]) -> tuple[StepLike[Any, Any], ...]:
    """Return the steps nested in ``step``: a segment's or a module loop's own, and the body of a hook."""
    match step:
        case Segment(steps=inner) | ForEachModule(body=inner):
            return inner
        case HookCall(name=name) if name in HOOK_BODIES:
            return hook_body(step)
    return ()


def _walk(steps: Iterable[StepLike[Any, Any]]) -> Iterator[StepLike[Any, Any]]:
    """Yield every step of ``steps`` and every step nested in one, in table order."""
    for step in steps:
        yield step
        yield from _walk(_steps(step))


def flatten(steps: Iterable[StepLike[Any, Any]]) -> Iterator[str]:
    """Yield the name of every pass of ``steps``, with hook and module bodies expanded, in table order."""
    for step in _walk(steps):
        if isinstance(step, PassSpec):
            yield step.name


def _declared(names: Iterable[str]) -> tuple[PassDecl, ...]:
    """Return the declarations of the passes ``names`` names, without the names no declaration has."""
    return tuple(DECLARED[name] for name in names if name in DECLARED)


def segment_bodies(
    segments: Iterable[StepLike[Any, Any]],
) -> Iterator[tuple[Segment[Any, Any, Any], tuple[PassDecl, ...]]]:
    """Yield each segment with the declarations of its body: its own steps, with hook and module bodies expanded.

    ``segments`` holds segments only; the segments in the body of a hook (``_finalize_modules``) are bodies of their
    own.
    """
    for step in segments:
        segment = cast("Segment[Any, Any, Any]", step)
        own: list[str] = []
        nested: list[tuple[Segment[Any, Any, Any], tuple[PassDecl, ...]]] = []
        for inner in segment.steps:
            if isinstance(inner, HookCall) and any(isinstance(part, Segment) for part in _steps(inner)):
                nested.extend(segment_bodies(_steps(inner)))
            else:
                own.extend(flatten([inner]))
        yield segment, _declared(own)
        yield from nested


def _scoped(steps: Iterable[StepLike[Any, Any]], *, per_module: bool = False) -> Iterator[tuple[PassDecl, bool]]:
    """Yield each declared pass of ``steps`` with whether it runs per module, in table order."""
    for step in steps:
        match step:
            case PassSpec(name=name) if name in DECLARED:
                yield DECLARED[name], per_module
            case ForEachModule():
                yield from _scoped(_steps(step), per_module=True)
            case HookCall():
                yield from _scoped(_steps(step), per_module=per_module or step.per_module)
            case Segment():
                yield from _scoped(_steps(step), per_module=per_module)


def label_problems(pipeline: tuple[StepLike[Any, Any], ...] = PIPELINE) -> list[str]:
    """Return the labels of ``scripts/pass_decls.py`` that name no segment or hook, and the hooks without a body."""
    segments = {step.name for step in _walk(pipeline) if isinstance(step, Segment)}
    hooks = {step.name for step in _walk(pipeline) if isinstance(step, HookCall)}
    return [
        *(
            f"BOUNDARY_BEFORE names {name}, which is no segment of the tables"
            for name in sorted(BOUNDARY_BEFORE.keys() - segments)
        ),
        *(f"HOOK_BODIES names {name}, which is no hook of the tables" for name in sorted(HOOK_BODIES.keys() - hooks)),
        *(f"hook {name} has no body in HOOK_BODIES" for name in sorted(hooks - HOOK_BODIES.keys())),
    ]


def table_problems(pipeline: tuple[StepLike[Any, Any], ...] = PIPELINE) -> list[str]:
    """Return what is wrong with the pipeline tables, against the declarations and the rules above."""
    problems = label_problems(pipeline)
    table = list(flatten(pipeline))
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
    entered = tuple(
        BOUNDARY_BEFORE[step.name]
        for step in _walk(pipeline)
        if isinstance(step, Segment) and step.name in BOUNDARY_BEFORE
    )
    if entered != BOUNDARIES:
        problems.append(f"the segments enter {[b.name for b in entered]}, not {[b.name for b in BOUNDARIES]}")
    problems.extend(trigger_problems(pipeline))
    repairs = {decl.name for decl in PASS_DECLS if decl.kind is PassKind.REPAIR}
    problems.extend(f"repair pass {name} is new: the repair set only shrinks" for name in sorted(repairs - REPAIRS))
    problems.extend(f"REPAIRS lists {name}, which is no repair pass: remove it" for name in sorted(REPAIRS - repairs))
    return problems


def trigger_problems(
    pipeline: tuple[StepLike[Any, Any], ...] = PIPELINE,
    decls: Sequence[PassDecl] = PASS_DECLS,
    kept: Collection[str] = TRIGGERS,
) -> list[str]:
    """Return the passes whose table entry and declaration disagree on a trigger, and the triggers that read more.

    A trigger is a second predicate beside its pass, which only the test suite's idle run checks: a pass may have one
    only where ``kept`` lists it, and its declaration says why the skip cannot be the body's own first statement.
    """
    triggered = {step.name for step in _walk(pipeline) if isinstance(step, PassSpec) and step.trigger is not None}
    declared = {decl.name for decl in decls if decl.trigger is not None}
    return [
        *(f"{name} has a trigger in the tables that its declaration does not" for name in sorted(triggered - declared)),
        *(f"{name} declares a trigger that its table entry does not have" for name in sorted(declared - triggered)),
        *(
            f"the trigger of {decl.name} reads {_facts_text(extra)}, which the pass does not declare as reads"
            for decl in decls
            if decl.trigger is not None and (extra := decl.trigger.reads - decl.reads)
        ),
        *(
            f"{name} has a trigger that TRIGGERS does not list: return early from the first statement of the body "
            "instead, or list it with the reason the exact form costs more"
            for name in sorted(declared - set(kept))
        ),
        *(f"TRIGGERS lists {name}, which has no trigger: remove it" for name in sorted(set(kept) - declared)),
        *(
            f"the trigger of {decl.name} gives no reason it is kept (Trigger.kept_because)"
            for decl in decls
            if decl.trigger is not None and not decl.trigger.kept_because.strip()
        ),
    ]


def _gates(model: SourceModule) -> dict[str, ast.expr]:
    """Return the ``when`` expression of each ``PassSpec`` of a tables module, by pass name."""
    return {
        call.args[0].value: when
        for call in ast.walk(model.tree)
        if isinstance(call, ast.Call) and _callee(model, call) == "PassSpec" and isinstance(call.args[0], ast.Constant)
        for when in [*call.args[2:3], *(keyword.value for keyword in call.keywords if keyword.arg == "when")]
    }


def _gate_reads(model: SourceModule, gate: ast.expr) -> set[str]:
    """Return what a ``when`` names: the attribute chain of each parameter, the helper or class of any other name."""
    inside = set(ast.walk(gate))
    parents = {child: parent for parent in inside for child in ast.iter_child_nodes(parent)}
    reads = set()
    for reference in model.references:
        if reference.node not in inside or reference.context != "load":
            continue
        node: ast.AST = reference.node
        parent = parents.get(node)
        qualified = isinstance(parent, ast.Attribute) and parent.value is node
        if any(binding.kind == "param" for binding in model.resolve(reference.node).bindings):
            chain: list[str] = []
            while isinstance(parent, ast.Attribute) and parent.value is node:
                chain.append(parent.attr)
                node, parent = parent, parents.get(parent)
            if chain:
                reads.add(".".join(chain))
        elif qualified and isinstance(getattr(_tables, reference.name, None), ModuleType):
            reads.add(parent.attr)
        else:
            reads.add(reference.name)
    return reads


def _is_option(read: str, allowed: Collection[str]) -> bool:
    """Return whether ``read`` or a leading part of its attribute chain is an option of the run."""
    parts = read.split(".")
    return any(".".join(parts[:end]) in allowed for end in range(1, len(parts) + 1))


def when_problems(source: str | None = None, allowed: Collection[str] = CONFIGURATION) -> list[str]:
    """Return the passes whose ``when`` names a fact that is no option of the run, in ``source`` or the tables.

    A pass that waits for a produced fact decides on it in its body, or declares a ``trigger``.
    """
    return [
        f"the when of {name} names {', '.join(names)}, which no option of the run sets (CONFIGURATION): decide on it "
        "in the pass, or declare a trigger with its reason"
        for model in [_model(Path(_tables.__file__)) if source is None else SourceModule(source)]
        for name, gate in _gates(model).items()
        if (names := sorted(read for read in _gate_reads(model, gate) if not _is_option(read, allowed)))
    ]


def provisional_reads(pipeline: tuple[StepLike[Any, Any], ...] = PIPELINE) -> list[str]:
    """Return the provisional reads the explicit edges of every segment record, in table order."""
    return [read for _, body in segment_bodies(pipeline) for read in check_order(body).provisional_reads]


class OrderCheck(NamedTuple):
    """What ``check_order`` found: the order, its problems, and the provisional reads the explicit edges record."""

    order: tuple[str, ...]
    problems: tuple[str, ...]
    provisional_reads: tuple[str, ...]


def _facts_text(facts: Iterable[Fact | RenderFact]) -> str:
    return ", ".join(sorted(fact.name for fact in facts))


def check_order(decls: Sequence[PassDecl], *, computed: bool = False) -> OrderCheck:
    """Check the order of one segment's passes against the order rule; ``computed`` derives the order from it.

    Two passes conflict when one writes what the other reads or both write the same fact. Where only one of them writes
    what the other reads, a derived edge runs the reader after the writer; every other conflicting pair must be
    connected by explicit ``After`` edges, directly or through other edges. An explicit edge names another pass of the
    segment that the pass conflicts with, and replaces the derived edge of its pair. In a table segment (``computed``
    false) every edge must follow the table order; a computed segment runs its passes in topological order, the order
    of ``decls`` breaking ties, and a cycle is a problem.
    """
    successors: dict[str, set[str]] = {decl.name: set() for decl in decls}
    problems, provisional = _add_explicit_edges(decls, successors, computed=computed)
    explicit = {frozenset((earlier, later)) for earlier, laters in successors.items() for later in laters}
    unordered: list[tuple[PassDecl, PassDecl, frozenset[Fact | RenderFact]]] = []
    for index, later in enumerate(decls):
        for earlier in decls[:index]:
            if frozenset((earlier.name, later.name)) in explicit or not (shared := _conflict(earlier, later)):
                continue
            if _flows(earlier, later):
                successors[earlier.name].add(later.name)
            elif computed and _flows(later, earlier):
                successors[later.name].add(earlier.name)
            else:
                unordered.append((earlier, later, shared))
    problems.extend(
        f"{later.name} conflicts with {earlier.name} on {_facts_text(shared)} and needs an explicit edge"
        for earlier, later, shared in unordered
        if not _reaches(successors, earlier.name, later.name) and not _reaches(successors, later.name, earlier.name)
    )
    order = tuple(successors)
    if computed:
        order, cycle = _topological_order(order, successors)
        if cycle:
            problems.append(f"the edges of {', '.join(cycle)} form a cycle")
    return OrderCheck(order, tuple(problems), tuple(provisional))


def _add_explicit_edges(
    decls: Sequence[PassDecl], successors: dict[str, set[str]], *, computed: bool
) -> tuple[list[str], list[str]]:
    """Add the explicit edges of ``decls`` to ``successors``; return the problems and the provisional reads."""
    position = {decl.name: index for index, decl in enumerate(decls)}
    problems: list[str] = []
    provisional: list[str] = []
    for later in decls:
        for edge in later.after:
            if (index := position.get(edge.pass_name)) is None:
                problems.append(f"{later.name} runs after {edge.pass_name}, which is not a pass of the segment")
            elif not _conflict(earlier := decls[index], later):
                problems.append(f"{later.name} runs after {edge.pass_name}, which it does not conflict with")
            elif not computed and index > position[later.name]:
                problems.append(f"{later.name} runs after {edge.pass_name}, which the table runs later")
            else:
                successors[earlier.name].add(later.name)
                if read_first := earlier.reads & later.writes:
                    provisional.append(
                        f"{earlier.name} reads {_facts_text(read_first)} before {later.name} writes it ({edge.reason})"
                    )
    return problems, provisional


def _flows(writer: PassDecl, reader: PassDecl) -> bool:
    """Return whether ``writer`` writes what ``reader`` reads and the pair has no other overlap."""
    return bool(writer.writes & reader.reads) and not writer.reads & reader.writes and not writer.writes & reader.writes


def _topological_order(names: tuple[str, ...], successors: Mapping[str, set[str]]) -> tuple[tuple[str, ...], list[str]]:
    """Return the names in an order every edge follows, ``names`` breaking ties, and the names a cycle leaves."""
    pending = list(names)
    order: list[str] = []
    while ready := [name for name in pending if not any(name in successors[other] for other in pending)]:
        order.append(ready[0])
        pending.remove(ready[0])
    return (*order, *pending), pending


def _conflict(earlier: PassDecl, later: PassDecl) -> frozenset[Fact | RenderFact]:
    """Return the facts that one of the passes writes and the other reads or writes."""
    return (earlier.writes & (later.reads | later.writes)) | (earlier.reads & later.writes)


def _reaches(successors: Mapping[str, set[str]], start: str, goal: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        if (name := pending.pop()) == goal:
            return True
        if name not in seen:
            seen.add(name)
            pending.extend(successors[name])
    return False


def decision_order(
    decisions: Sequence[DecisionPass], decls: Mapping[str, PassDecl] = DECISION_DECLS
) -> tuple[DecisionPass, ...]:
    """Return ``decisions`` in the order the order rule computes from ``decls``, registration breaking ties."""
    by_name = {decision.spec["name"]: decision for decision in decisions}
    order = check_order([decls[decision.spec["name"]] for decision in decisions], computed=True).order
    return tuple(by_name[name] for name in order)


def decision_problems(decisions: Sequence[DecisionPass], decls: Mapping[str, PassDecl] = DECISION_DECLS) -> list[str]:
    """Return what is wrong with one decision registry, against ``decls`` and the rules of the decision phase."""
    problems: list[str] = []
    table = {decl.name for decl in PASS_DECLS}
    seen: set[str] = set()
    for decision in decisions:
        if (name := decision.spec["name"]) in seen:
            problems.append(f"decision {name} is registered twice")
        seen.add(name)
        if name in table:
            problems.append(f"decision {name} has the name of a table pass")
        if (decl := decls.get(name)) is None:
            problems.append(f"decision {name} has no declaration in DECISION_DECLS")
            continue
        if (decl.phase, decl.scope, decl.kind) != (Phase.DECIDE, Scope.GLOBAL, PassKind.TRANSFORM):
            problems.append(
                f"decision {name} declares a {decl.scope.value} {decl.kind.value} of {decl.phase.name}, "
                "not a global transform of DECIDE"
            )
        if render := sorted(fact.name for fact in decl.reads | decl.writes if isinstance(fact, RenderFact)):
            problems.append(f"decision {name} reads or writes {', '.join(render)}: decisions read and write facts")
        if not decision.spec["issue"]:
            problems.append(f"decision {name} names no issue")
    if len(seen) == len(decisions) and seen <= decls.keys():
        result = check_order([decls[decision.spec["name"]] for decision in decisions], computed=True)
        problems.extend(f"decision phase: {problem}" for problem in result.problems)
        if not result.problems and (listed := tuple(decision.spec["name"] for decision in decisions)) != result.order:
            problems.append(
                f"decision phase: the registry lists {', '.join(listed)}, not the order the order rule computes: "
                f"{', '.join(result.order)}"
            )
    return problems


def _parser_classes(cls: type[Any] = Parser) -> Iterator[type[Any]]:
    yield cls
    for subclass in cls.__subclasses__():
        yield from _parser_classes(subclass)


def registry_problems() -> list[str]:
    """Return what is wrong with the decision registries of ``Parser`` and the loaded parser classes that set one."""
    return [
        f"{cls.__qualname__}: {problem}"
        for cls in dict.fromkeys(_parser_classes())
        if cls.__module__.startswith("datamodel_code_generator.") and "_DECISION_PASSES" in vars(cls)
        for problem in decision_problems(cls._DECISION_PASSES)
    ]


_MODELS: dict[Path, SourceModule] = {}


def _model(path: Path) -> SourceModule:
    """Return the source model of a module, read once per run (named by its path below ``src/`` when it has one)."""
    if (model := _MODELS.get(path)) is None:
        parts = path.resolve().relative_to(SRC).with_suffix("").parts if path.resolve().is_relative_to(SRC) else ()
        name = ".".join(parts[:-1] if parts[-1:] == ("__init__",) else parts)
        model = _MODELS[path] = SourceModule(path.read_text(encoding="utf-8"), path.as_posix(), name)
    return model


@cache
def _spellings(model: SourceModule) -> dict[ast.AST, str]:
    """Return the name each name node of a model's tree spells."""
    return {reference.node: reference.name for reference in model.references}


def _callee(model: SourceModule, call: ast.Call) -> str | None:
    """Return the name a call calls: the attribute of a method call, or the name a plain call spells."""
    return call.func.attr if isinstance(call.func, ast.Attribute) else _spellings(model).get(call.func)


def _holding(paths: Iterable[Path], *words: str) -> Iterator[Path]:
    """Yield the modules whose text holds one of ``words``: a module without any of them cannot spell what is sought."""
    return (path for path in sorted(paths) if any(word in path.read_text(encoding="utf-8") for word in words))


def _deep_warning(model: SourceModule, call: ast.Call) -> bool:
    """Return whether ``call`` warns with a ``stacklevel`` that is a call or a number above 2."""
    return _callee(model, call) in WARNING_FUNCTIONS and any(
        keyword.arg == "stacklevel"
        and (
            isinstance(keyword.value, ast.Call)
            or (isinstance(keyword.value, ast.Constant) and keyword.value.value > CALLER_STACKLEVEL)
        )
        for keyword in call.keywords
    )


def deep_warnings(model: SourceModule) -> Iterator[tuple[str, int]]:
    """Yield the function and the line of each deep warning of a module, once for every function that holds it."""
    for function in model.scopes:
        if function.kind == "function":
            for node in ast.walk(function.node):
                if isinstance(node, ast.Call) and _deep_warning(model, node):
                    yield function.code_name, node.lineno


def warning_problems(package: Path = PACKAGE) -> list[str]:
    """Return the deep warnings of the parser, model and type modules that ``DEEP_WARNINGS`` does not list.

    The entries that no warning matches any more are problems too.
    """
    sites = {
        f"{path.relative_to(package).as_posix()}:{function}"
        for path in _holding(
            {*package.glob("parser/**/*.py"), *package.glob("model/**/*.py"), package / "types.py"}, "stacklevel"
        )
        for function, _ in deep_warnings(_model(path))
    }
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
    problems = [
        *table_problems(),
        *when_problems(),
        *registry_problems(),
        *warning_problems(),
    ]
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
