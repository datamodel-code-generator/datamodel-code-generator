"""Compare generated output of the current checkout with a released version over an input x option matrix.

Usage:
  python scripts/compare_release.py --base 0.85.0 [--types pydantic_v2.BaseModel,msgspec.Struct]
      [--options 'default;--collapse-root-models'] [--extra '--input-file-type openapi'] [--import-check]
      [--show-diff N] [-j N] INPUT [INPUT ...]

Each option set is one quoted string of extra CLI arguments (`default` means none), separated by `;`.
The candidate is this checkout's `src/`, run with the interpreter running this script unless `--candidate-python`
is given. A base that is a released version (`0.85.0`) is installed once into a cached virtual environment (needs
`uv`) with the candidate's formatter, pydantic and msgspec versions pinned, so only generator changes show up as
differences; any other base is a git revision of this repository (`main~1`, a commit): its `src/` is extracted once
with `git archive` and run with the candidate's interpreter, as the candidate is (`resolve_base`). Base outputs are
cached as well, so reruns only generate the candidate side.

Generation runs in warm worker processes: each worker imports datamodel-code-generator once and generates in itself,
`RECYCLE` runs before a new worker takes its place, so runs pay neither interpreter start-up nor imports. Each run
has its own log for its output and errors, working directory, warning filters, timeout (which ends the worker, as a
crash does) and memory cap; what a run records does not depend on the runs its worker ran before it. An import check
runs the generated code, in a fresh child of the worker. POSIX only. Both sides run in an environment built from
scratch (`run_environment`), and each worker refuses to run a package imported from outside the tree its side names.
A run shows its warnings under Python's default filters with `DeprecationWarning` and `PendingDeprecationWarning`
shown too, and records each as its category, message and the module the filters match it with (`Outcome.warnings`),
so a warning whose call moves keeps its record; `Outcome.handled` are the classes of the exceptions the code of
`datamodel_code_generator.__main__` caught and did not raise again (Python 3.12+); `Outcome.messages` is what the run
printed, with the frames of tracebacks left out, so an error is its class and message. A run that refuses a single
output file for a modular one is run again into a directory, and `Outcome.first` keeps the refusal (its exit status,
messages, warnings and handled exceptions).

A run's working directory is its output directory, so what it writes to a relative path (a client's package) is
compared with the rest of its output; an output its arguments name elsewhere is not (`_outside`).

Prints one line per differing or failing run, a summary and the evidence (`Evidence`): per input group the runs
compared (generated on both sides, all output in the run's directory) and the others, the runs the candidate refuses
and the base generated, and, against a git revision, which changed code objects compared runs ran on their side
(`scripts/_change_units.py` maps the change to code objects and probes; each side's workers trace them, `_Tracer`,
Python 3.12+). Exits 1 when any run differs or fails on the candidate only (a single output file it refuses and the
base wrote included), or when the evidence compared too little: no input group, an input group that compared nothing,
or a changed code object no compared run ran, that is unmeasurable, or that no run could measure. `--evidence` writes
what each run proved (exit status and hashes) as JSON.
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import difflib
import dis
import functools
import hashlib
import importlib
import importlib.util
import inspect
import io
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping
    from types import CodeType, FrameType, ModuleType

Record = dict[str, Any]
"""A JSON object a worker job or reply carries."""
Listing = Literal["path", "system"]
"""The order a generation job lists directories in with `Path.rglob` (`Side.listing`): in path order, or the file
system's."""
DEFAULT_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
COMMON_ARGS = ("--disable-timestamp", "--formatters", "black", "isort")
WARM_IMPORTS = ("black", "isort", "pydantic", "msgspec", "graphql", "yaml")
MODULAR_OUTPUT_ERRORS = ("--output <directory>", "require an output directory")
# The exit code of a run that hit its memory cap, also reported for a run whose own handler printed the MemoryError:
# like a timeout it is not cached, since another cap may let it finish.
OUT_OF_MEMORY = 125
PROCESS_STATE_OPTIONS = frozenset({"--debug"})
"""Options that turn on state for the rest of the process (`--debug`: tracing in every parser imported after it): the
worker of such a run is closed after it, as after running out of memory, so later runs do not inherit it."""
RECYCLE = 200
"""Jobs a worker runs before a new one takes its place, which bounds the memory and the state a worker gathers."""
CACHE_FORMAT = 9
ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "datamodel_code_generator"
RELEASE = re.compile(r"\d+(?:\.\d+)+")
"""A base that is a released version, installed from the package index; any other base is a git revision."""
TRACEBACK_FRAMES = re.compile(r"^Traceback \(most recent call last\):\n(?:[ \t].*(?:\n|\Z))*", re.MULTILINE)
PASSED_VARIABLES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
    "COVERAGE_PROCESS_START",
    "COVERAGE_PROCESS_CONFIG",
)
"""The caller's variables a side's processes see: where programs, the home and temporary files are, what Windows needs
to start a Python process (its sockets fail without `SYSTEMROOT`) and coverage's measurement of subprocesses. Any other
(PYTHONPATH, PYTHONWARNINGS, the test suite's checks) could change what a side runs or how."""
HANDLED_TOOL = 3
"""The `sys.monitoring` tool id that records the exceptions `datamodel_code_generator.__main__` handles.

A worker that traces probes (`_Tracer`) holds it for its lifetime, with the global `PY_START` that finds the changed
code objects."""
LOCAL_TOOL = 4
"""The `sys.monitoring` tool id of the local events on the changed code objects (`_Tracer`)."""
IMPORT_ARGUMENTS = ("globals", "locals", "fromlist", "level")
"""The arguments of `__import__` after the name, in order."""


def _default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "datamodel-code-generator" / "compare-release"


# Worker side: runs inside the base or candidate interpreter.


def _exit_code(main: Callable[[list[str]], object], argv: list[str]) -> int:
    try:
        result = main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    except MemoryError:
        with contextlib.suppress(MemoryError):
            traceback.print_exc()
        return OUT_OF_MEMORY
    except Exception:  # ruff: ignore[blind-except] - report any generator crash as a failed run
        try:
            traceback.print_exc()
        except MemoryError:
            return OUT_OF_MEMORY
        return 1
    return int(result or 0)


def _import_modules(out: str) -> int:
    root = Path(out)
    sys.path.insert(0, out)
    files = sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    try:
        for file in files:
            module = ".".join(file.relative_to(root).with_suffix("").parts).removesuffix(".__init__")
            importlib.import_module(module)
    except Exception as exc:  # ruff: ignore[blind-except] - any import failure is the result
        sys.stderr.write(traceback.format_exception_only(type(exc), exc)[-1])
        return 1
    return 0


class _WarningRecorder:
    """Record each warning a run shows as its category, message and the module the warning filters match it with.

    Installed as the first filter, its `match` is called with the module of every warning that reaches the filters and
    matches nothing, so the real filters still decide; `show` stands in for `warnings.showwarning`.
    """

    def __init__(self) -> None:
        self.local = threading.local()
        self.records: list[list[str]] = []

    def match(self, module: str) -> bool:
        self.local.module = module
        return False

    def show(self, message: Warning | str, category: type[Warning], *_: object) -> None:
        self.records.append([
            f"{category.__module__}.{category.__qualname__}",
            str(message),
            getattr(self.local, "module", ""),
        ])


def _track(
    caught: list[BaseException],
    filename: str,
    code: CodeType,
    _: int,
    exception: BaseException,
    *,
    handled: bool = True,
) -> None:
    """Add the exception `code` handled to `caught`, or take it out when it is raised again (`handled=False`)."""
    if code.co_filename != filename:
        return
    if handled:
        caught.append(exception)
    else:
        with contextlib.suppress(ValueError):
            caught.remove(exception)


@contextlib.contextmanager
def _handled_exceptions(filename: str) -> Generator[list[BaseException], None, None]:
    """Collect, while the block runs, the exceptions the code of `filename` catches and does not raise again.

    A handler that only cleans up (`finally`, `with`, an `except` that does not match or re-raises) raises the
    exception again, which takes it out. Python 3.12+ (`sys.monitoring`); earlier the list stays empty. The tool is
    freed at the end, so the next run of a worker starts again, unless the worker's tracer holds it (`_Tracer`): its
    `PY_START` stays on.
    """
    caught: list[BaseException] = []
    held = bool(_TRACER)
    if monitoring := getattr(sys, "monitoring", None):
        if not held:
            monitoring.use_tool_id(HANDLED_TOOL, "compare_release")
        monitoring.register_callback(
            HANDLED_TOOL, monitoring.events.EXCEPTION_HANDLED, functools.partial(_track, caught, filename)
        )
        monitoring.register_callback(
            HANDLED_TOOL, monitoring.events.RERAISE, functools.partial(_track, caught, filename, handled=False)
        )
        kept = monitoring.events.PY_START if held else 0
        monitoring.set_events(HANDLED_TOOL, kept | monitoring.events.EXCEPTION_HANDLED | monitoring.events.RERAISE)
    try:
        yield caught
    finally:
        if monitoring:
            monitoring.set_events(HANDLED_TOOL, monitoring.events.PY_START if held else 0)
            if not held:
                monitoring.free_tool_id(HANDLED_TOOL)


def _first_position(code: CodeType) -> tuple[int, int]:
    """Return where a code object's first instruction with a source span starts (`_change_units.first_position`)."""
    return next(
        (
            (line, column)
            for line, last, column, end in code.co_positions()
            if line is not None
            and last is not None
            and column is not None
            and end is not None
            and (last, end) > (line, column)
        ),
        (0, 0),
    )


class _CacheInfo(Protocol):
    """The statistics of a `functools` cache (`_Cached.cache_info`)."""

    hits: int


class _Cached(Protocol):
    """A `functools` cache wrapper: what `_Tracer` clears."""

    __wrapped__: Callable[..., object]

    def cache_clear(self) -> None:
        """Forget every cached value."""

    def cache_info(self) -> _CacheInfo:
        """Return how often the cache found a value."""


_MISSING = object()
"""What `_named` returns where a run cannot tell the object a name or attribute chain names."""


def _named(frame: FrameType, code: CodeType, chain: list[str]) -> object:
    """Return the object a name and its attributes name in a running frame, without running any code.

    An attribute is read from the object's or its class's namespace (`inspect.getattr_static`), from a slot or as its
    ``__class__``; any other descriptor (a property, a method) would run code: the object is unknown (`_MISSING`).
    """
    head, *attributes = chain
    if head in code.co_varnames or head in code.co_cellvars or head in code.co_freevars:
        value = frame.f_locals.get(head, _MISSING)
    else:
        value = frame.f_globals.get(head, frame.f_builtins.get(head, _MISSING))
    for attribute in attributes:
        if value is _MISSING:
            break
        found = type(value) if attribute == "__class__" else inspect.getattr_static(value, attribute, _MISSING)
        if inspect.ismemberdescriptor(found):
            found = getattr(value, attribute, _MISSING)
        elif not isinstance(found, type) and hasattr(type(found), "__get__"):
            found = _MISSING
        value = found
    return value


def _finds(value: object, module: str, qualname: str, name: str) -> bool:
    """Whether an object (a class or an instance) finds an attribute in the class `module`/`qualname`.

    It finds it in the first class of its method resolution order whose namespace holds it.
    """
    if value is _MISSING:
        return False
    kind = value if isinstance(value, type) else type(value)
    owner = next((item for item in kind.__mro__ if name in vars(item)), None)
    return owner is not None and (owner.__module__, owner.__qualname__) == (module, qualname)


def _started_frame() -> FrameType | None:
    """Return, from a `sys.monitoring` callback, the frame of the code whose event called it."""
    frame = inspect.currentframe()
    callback = frame.f_back if frame is not None else None
    return callback.f_back if callback is not None else None


class _Tracer:
    """Record which probes of the changed code objects (`scripts/_change_units.py`) each job of a worker runs.

    Two `sys.monitoring` tools: the finder (`HANDLED_TOOL`, held for the worker's lifetime) has the global `PY_START`
    and disables it for every code object, after it records the first start of a unit's code object and arms the
    local events of `LOCAL_TOOL` on it: `PY_START` where a probe needs the start, `INSTRUCTION` where it needs
    instructions. Each local event records once per job and disables itself; every job arms them again. A read of a
    class attribute on a name or attribute chain (``self.X`` too; `_change_units.Readers`) checks at its instruction
    the object the chain names, and keeps reporting until that object finds the attribute in the class in the job.
    `LINE` is never used (a `LINE` callback that disables itself drops the `INSTRUCTION` event of the line's first
    instruction).

    What ran before the worker's first job (importing the CLI) every fresh process runs, so it counts for each job.
    What the first import of a module of the package ran counts for each job that imports the module
    (`_track_imports`). A `functools` cache whose computation in a job recorded a probe, or found the value of a cache
    that did, is dirty: the next job clears it, so a job that needs its value computes it and records its own probes
    (the local `PY_START` and `PY_RETURN` of a cache's function bound its computations, which run only where the
    cache misses; one that raises stores no value). A computation that runs only probes the job recorded already
    records nothing and stays clean, so a later job that finds its value lacks those probes: that only refuses, it
    never credits a probe that did not run in the job. A file probe is an `open` of the file by anything but the loader
    of a module (an audit hook). Only the generation counts: `begin` and `end` bound it.
    """

    def __init__(self, package: Path, probes: list[Record]) -> None:
        self.package = os.path.normpath(package)
        self.probes = probes
        self.codes: dict[tuple[object, ...], int] = {}
        self.events: list[int] = []
        self.offsets: list[set[int]] = []
        self.loads: dict[tuple[int, int], list[tuple[list[str], list[str]]]] = {}
        self.seen: set[tuple[object, ...]] = set()
        events = sys.monitoring.events
        self.cache_events = events.PY_START | events.PY_RETURN
        for probe in probes:
            members = (
                probe["readers"] if probe["kind"] == "readers" else [probe] * (probe["kind"] in {"start", "instr"})
            )
            for member in members:
                index = self.codes.setdefault(tuple(member["code"]), len(self.codes))
                member["index"] = index
                self.events += [0] * (len(self.codes) - len(self.events))
                self.offsets += [set() for _ in range(len(self.codes) - len(self.offsets))]
                self.offsets[index].update(member.get("offsets") or ())
                self.events[index] |= events.INSTRUCTION if member.get("offsets") else events.PY_START
                if member.get("chain"):
                    for offset in member["offsets"]:
                        self.loads.setdefault((index, offset), []).append((member["chain"], member["receiver"]))
        self.names = {(key[1], key[2]) for key in self.codes}
        self.files = {
            os.path.normpath(Path(package) / probe["file"]): probe["file"]
            for probe in probes
            if probe["kind"] == "read"
        }
        self.armed: dict[int, tuple[CodeType, int]] = {}
        self.caches: dict[int, _Cached] = {}
        self.computing: list[tuple[FrameType | None, _Cached, int, int]] = []
        self.dirty: list[_Cached] = []
        self.log: list[tuple[object, ...]] = []
        self.startup: set[tuple[object, ...]] = set()
        self.started = False
        self.active = True
        self.requested: set[str] = set()
        self.windows: list[tuple[int, set[str]]] = []
        self.imports: dict[str, tuple[set[tuple[object, ...]], set[str]]] = {}
        self.closures: dict[str, set[tuple[object, ...]]] = {}
        monitoring = sys.monitoring
        monitoring.use_tool_id(HANDLED_TOOL, "compare_release")
        monitoring.use_tool_id(LOCAL_TOOL, "compare_release probes")
        monitoring.register_callback(HANDLED_TOOL, monitoring.events.PY_START, self._find)
        monitoring.register_callback(LOCAL_TOOL, monitoring.events.PY_START, self._start)
        monitoring.register_callback(LOCAL_TOOL, monitoring.events.INSTRUCTION, self._instruction)
        monitoring.register_callback(LOCAL_TOOL, monitoring.events.PY_RETURN, self._computed)
        monitoring.set_events(HANDLED_TOOL, monitoring.events.PY_START)
        if self.files:
            sys.addaudithook(self._audit)

    def _record(self, event: tuple[object, ...]) -> None:
        if self.active:
            self.log.append(event)

    def _key(self, code: CodeType) -> tuple[object, ...] | None:
        filename = os.path.normpath(code.co_filename)
        if not filename.startswith(self.package + os.sep):
            return None
        path = Path(filename).relative_to(self.package).as_posix()
        return (path, code.co_qualname, code.co_firstlineno, *_first_position(code))

    def _find(self, code: CodeType, _: int) -> object:
        """Arm the local events of a unit's code object at its first start, and record that start."""
        if (code.co_qualname, code.co_firstlineno) in self.names and id(code) not in self.armed:
            key = self._key(code)
            if key is not None and (index := self.codes.get(key)) is not None:
                self.armed[id(code)] = (code, index)
                self._record(("start", index))
                sys.monitoring.set_local_events(LOCAL_TOOL, code, self._local(code, index))
        return sys.monitoring.DISABLE

    def _local(self, code: CodeType, index: int) -> int:
        return self.events[index] | (self.cache_events if id(code) in self.caches else 0)

    def _hits(self) -> int:
        """Return how often the dirty caches of the running job have found a value."""
        return sum(wrapper.cache_info().hits for wrapper in self.dirty)

    def _start(self, code: CodeType, _: int) -> object:
        """Record a start; a cache's function keeps reporting starts.

        A start of a cache's function begins a computation of the cache (`_computed` ends it).
        """
        if (wrapper := self.caches.get(id(code))) is not None:
            self.computing.append((_started_frame(), wrapper, len(self.log), self._hits()))
        if (armed := self.armed.get(id(code))) is not None:
            self._record(("start", armed[1]))
        return None if wrapper is not None else sys.monitoring.DISABLE

    def _computed(self, _: CodeType, __: int, ___: object) -> object:
        """End a computation of a cache: it is dirty where it recorded a probe or found a dirty cache's value.

        What a computation inside it ran, it ran inside it too. The computations above it ended with an exception.
        """
        frame = _started_frame()
        found = next((at for at in range(len(self.computing) - 1, -1, -1) if self.computing[at][0] is frame), None)
        if found is None:
            return None
        _, wrapper, start, hits = self.computing[found]
        del self.computing[found:]
        if self.active and self.started and (len(self.log) > start or self._hits() > hits):
            self.dirty.append(wrapper)
        return None

    def _instruction(self, code: CodeType, offset: int) -> object:
        """Record an instruction a probe names; the others of the code object only disable themselves.

        An instruction that reads a class attribute keeps reporting while the object it reads it on has not found the
        attribute in the class in the job.
        """
        if (armed := self.armed.get(id(code))) is not None and offset in self.offsets[armed[1]]:
            if (event := ("offset", armed[1], offset)) not in self.seen:
                self.seen.add(event)
                self._record(event)
            checks = self.loads.get((armed[1], offset))
            if checks and self._loaded(_started_frame(), code, (armed[1], offset), checks):
                return None
        return sys.monitoring.DISABLE

    def _loaded(
        self,
        frame: FrameType | None,
        code: CodeType,
        where: tuple[int, int],
        checks: list[tuple[list[str], list[str]]],
    ) -> bool:
        """Record the class attributes an instruction reads on the objects its chains name; return whether some wait."""
        waiting = False
        for chain, receiver in checks:
            event = ("load", *where, *receiver)
            if event in self.seen:
                continue
            if frame is not None and _finds(_named(frame, code, chain), *receiver):
                self.seen.add(event)
                self._record(event)
            else:
                waiting = True
        return waiting

    def _audit(self, event: str, args: tuple[object, ...]) -> None:
        """Record an `open` of a file a probe reads, unless the import system opens it to load a module.

        The loader opens a module's file itself (its caller is import machinery); code that a module's import runs
        and reads a file reads it as any other code does.
        """
        if event != "open" or not self.active or not args or not isinstance(args[0], (str, os.PathLike)):
            return
        path = self.files.get(os.path.normpath(os.fspath(args[0])))
        caller = _started_frame()
        if path is not None and (caller is None or "importlib._bootstrap" not in caller.f_code.co_filename):
            self._record(("read", path))

    def watch_caches(self, module: ModuleType) -> None:
        """Watch the `functools` caches a module of the package defines (at module level and in its classes)."""
        spaces = [vars(module), *(vars(value) for value in vars(module).values() if isinstance(value, type))]
        for space in spaces:
            for value in list(space.values()):
                wrapped = getattr(value, "__wrapped__", None)
                code = getattr(wrapped, "__code__", None)
                if hasattr(value, "cache_clear") and code is not None and id(code) not in self.caches:
                    self.caches[id(code)] = value
                    index = self.armed.get(id(code), (None, -1))[1]
                    events = self.events[index] if index >= 0 else 0
                    sys.monitoring.set_local_events(LOCAL_TOOL, code, events | self.cache_events)

    def begin(self) -> None:
        """Start a job: clear the dirty caches and make every probe report again."""
        if not self.started:
            self.startup, self.started = set(self.log), True
        for wrapper in self.dirty:
            wrapper.cache_clear()
        self.dirty, self.computing, self.log, self.requested, self.seen = [], [], [], set(), set()
        for code, index in self.armed.values():
            sys.monitoring.set_local_events(LOCAL_TOOL, code, 0)
            sys.monitoring.set_local_events(LOCAL_TOOL, code, self._local(code, index))
        for key in self.caches:
            if key not in self.armed:
                code = self.caches[key].__wrapped__.__code__
                sys.monitoring.set_local_events(LOCAL_TOOL, code, 0)
                sys.monitoring.set_local_events(LOCAL_TOOL, code, self.cache_events)
        self.active = True

    def end(self) -> None:
        """End a job's generation: what runs next (the comparator's own checks) does not count."""
        self.active = False

    def request(self, names: list[str]) -> None:
        """Count the modules a job imports, and those the import running now imports."""
        self.requested.update(names)
        if self.windows:
            self.windows[-1][1].update(names)

    @contextlib.contextmanager
    def first_import(self, name: str) -> Generator[None, None, None]:
        """Record what the first import of the module `name` runs; probes that ran already report again."""
        self.windows.append((len(self.log), set()))
        self.seen = set()
        for code, index in self.armed.values():
            sys.monitoring.set_local_events(LOCAL_TOOL, code, 0)
            sys.monitoring.set_local_events(LOCAL_TOOL, code, self._local(code, index))
        try:
            yield
        finally:
            start, requested = self.windows.pop()
            if (module := sys.modules.get(name)) is not None and name not in self.imports:
                self.imports[name] = (set(self.log[start:]), requested)
                self.closures.clear()
                self.watch_caches(module)
            if self.windows:
                self.windows[-1][1].update(requested)

    def _closure(self, name: str) -> set[tuple[object, ...]]:
        """Return what the first imports of a module and of every module its first import asked for ran.

        It is kept until another module's first import is recorded.
        """
        if (found := self.closures.get(name)) is not None:
            return found
        found, pending, seen = set(), [name], set()
        while pending:
            if (module := pending.pop()) not in seen and module in self.imports:
                seen.add(module)
                ran, requested = self.imports[module]
                found |= ran
                pending += requested
        self.closures[name] = found
        return found

    def ran(self) -> list[int]:
        """Return the probes the job ran since `begin`, with what the CLI's and its modules' first imports ran."""
        events = self.startup | set(self.log)
        for name in self.requested:
            events |= self._closure(name)
        return [index for index, probe in enumerate(self.probes) if _satisfied(probe, events)]

    def stop(self) -> None:
        """Stop tracing before the worker exits: a callback must not run while the interpreter shuts down."""
        self.active = False
        monitoring = sys.monitoring
        monitoring.set_events(HANDLED_TOOL, 0)
        for code, _ in self.armed.values():
            monitoring.set_local_events(LOCAL_TOOL, code, 0)
        monitoring.free_tool_id(LOCAL_TOOL)
        monitoring.free_tool_id(HANDLED_TOOL)


def _satisfied(probe: Record, events: set[tuple[object, ...]]) -> bool:
    """Whether a probe ran: by its kind, from the events a job recorded (`_Tracer`)."""
    match probe["kind"]:
        case "start":
            return ("start", probe["index"]) in events
        case "instr":
            return any(("offset", probe["index"], offset) in events for offset in probe["offsets"])
        case "read":
            return ("read", probe["file"]) in events
        case "readers":
            return any(_read(member, events) for member in probe["readers"])
    return False


def _read(reader: Record, events: set[tuple[object, ...]]) -> bool:
    """Whether a reader of a binding ran (`_change_units.Readers`), with its object's check where it has one."""
    index, offsets = reader["index"], reader["offsets"]
    if reader.get("chain"):
        return any(("load", index, offset, *reader["receiver"]) in events for offset in offsets)
    return any(("offset", index, offset) in events for offset in offsets)


def _track_imports(tracer: _Tracer) -> None:
    """Route the imports of this worker (`import` statements, `importlib.import_module`) through `tracer`.

    It learns each module of the package a job imports, and records what the first import of each runs: each module
    the import loads is imported apart first, in the order the import system loads them (a package before its
    submodules, then the submodules an import names), so a module's record holds its own import and what that asked
    for, never a sibling's. The hooks' frames are named as import machinery, so warnings skip them as they skip
    importlib's (a warning's module must not change because the worker traces its imports).
    """
    package = "datamodel_code_generator"
    original_import, original_module = builtins.__import__, importlib.import_module

    def track(absolute: str, items: list[str]) -> list[str]:
        """Count the modules an import of `absolute` (and of its `items`) asks for; return those not imported yet."""
        if absolute != package and not absolute.startswith(f"{package}."):
            return []
        parts = absolute.split(".")
        names = [".".join(parts[:end]) for end in range(1, len(parts) + 1)]
        loaded = vars(sys.modules[absolute]) if absolute in sys.modules else {}
        submodules = [f"{absolute}.{item}" for item in items if item != "*"]
        tracer.request([*names, *submodules])
        fresh = [name for name in names if name not in sys.modules]
        return fresh + [
            name for name in submodules if name not in sys.modules and name.rpartition(".")[2] not in loaded
        ]

    def load(fresh: list[str], importer: Callable[[str], object]) -> None:
        """Import each module of `fresh` in a first-import record of its own.

        A name an import names that is no module (an attribute of its package) is left to the import that names it.
        """
        for module in fresh:
            with tracer.first_import(module):
                try:
                    importer(module)
                except ModuleNotFoundError as error:
                    if error.name != module:
                        raise

    def import_(name: str, *args: object, **kwargs: object) -> object:
        given = dict(zip(IMPORT_ARGUMENTS, args, strict=False)) | kwargs
        absolute, level, items = name, given.get("level", 0), given.get("fromlist") or ()
        if isinstance(level, int) and level:
            namespace = given.get("globals")
            package = str((namespace.get("__package__") if isinstance(namespace, dict) else None) or "")
            base = package.rsplit(".", level - 1)[0]
            absolute = f"{base}.{name}" if name else base
        names = [item for item in items if isinstance(item, str)] if isinstance(items, (list, tuple)) else []
        load(track(absolute, names), original_import)
        return original_import(name, *args, **kwargs)

    def import_module(name: str, package: str | None = None) -> ModuleType:
        absolute = name
        if name.startswith("."):
            with contextlib.suppress(ImportError, ValueError):
                absolute = importlib.util.resolve_name(name, package)
        load(track(absolute, []), original_module)
        return original_module(name, package)

    for hook in (load, import_, import_module):
        hook.__code__ = hook.__code__.replace(co_filename="<compare_release importlib._bootstrap hook>")
    builtins.__import__ = import_
    importlib.import_module = import_module


def _outside(args: list[str], root: str) -> list[str]:
    """Return the outputs a run's arguments name outside its directory `root`.

    They are the paths of the package's output fields (`OUTPUT_PATH_FIELDS`: the model output, the model metadata, a
    target's package) as its own parser reads them, relative to the run's working directory; none for a package
    without that table. Both modules are the ones the CLI imported. Such an output is written where the other side
    writes it too, so it cannot be compared.
    """
    config, arguments = (
        sys.modules.get(f"datamodel_code_generator.{name}") for name in ("_config_fields", "arguments")
    )
    fields, arg_parser = getattr(config, "OUTPUT_PATH_FIELDS", ()), getattr(arguments, "arg_parser", None)
    if not fields or arg_parser is None:
        return []
    parsed = None
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
        contextlib.suppress(SystemExit),
    ):
        parsed = arg_parser.parse_known_args(args)[0]
    paths = [
        Path(os.path.realpath(Path(str(value)).expanduser()))
        for name in fields
        if (value := getattr(parsed, name, None))
    ]
    return sorted(str(path) for path in paths if not path.is_relative_to(os.path.realpath(root)))


_UNSEEN = "\0"
FREE_TOOLS = (5, 2, 1, 0)
"""The `sys.monitoring` tool ids a recorder may take, the first free one: the tracer holds 3 and 4."""
SUB_LINE = sys.version_info >= (3, 14)
"""Whether a recorder keys what ran finer than a line (`_Recorder`): ``BRANCH_LEFT`` and ``BRANCH_RIGHT`` (3.14+)."""
BRANCHES = frozenset({"POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE", "POP_JUMP_IF_NONE", "POP_JUMP_IF_NOT_NONE", "FOR_ITER"})
"""The instructions whose two ways `sys.monitoring` reports (``BRANCH_LEFT``, ``BRANCH_RIGHT``)."""
NO_FALL_THROUGH = frozenset({
    "JUMP_FORWARD",
    "JUMP_BACKWARD",
    "JUMP_BACKWARD_NO_INTERRUPT",
    "RETURN_VALUE",
    "RAISE_VARARGS",
    "RERAISE",
})
"""The instructions after which the next instruction runs only where something jumps to it."""


def instruction_place(code: CodeType, offset: int) -> str | None:
    """Return the sub-line key of a code object's instruction at `offset`: its source span and its name.

    None for an instruction without a whole span. The spans and names are those of the running interpreter's
    compiler: a catalog's sub-line keys hold for the version that recorded them.
    """
    raw, positions = _units(code)
    if offset // 2 >= len(positions) or None in (position := positions[offset // 2]):
        return None
    line, last, column, end = position
    return f"{line}:{column}-{last}:{end} {dis.opname[raw[offset]]}"


@functools.cache
def _units(code: CodeType) -> tuple[bytes, list[tuple[int | None, int | None, int | None, int | None]]]:
    """Return a code object's bytecode and the position of each of its code units, read once."""
    return code.co_code, list(code.co_positions())


def start_place(code: CodeType) -> str:
    """Return the sub-line key of a code object's start: its first position (`_first_position`) and its qualname.

    The qualname tells it from the code object around it, which may start at the same position.
    """
    return "{}:{} {}".format(*_first_position(code), code.co_qualname)


@functools.cache
def _flow(code: CodeType) -> tuple[list[dis.Instruction], dict[int, set[str]], frozenset[int], int]:
    """Return a code object's instructions, the names of those that jump to each, its handlers and where it starts.

    It starts at its last ``RESUME`` at the start of its body (the low bits of the argument 0), where ``PY_START``
    reports it: a generator's runs its setup before (`dis` documents the argument).
    """
    instructions = list(dis.get_instructions(code))
    jumpers: dict[int, set[str]] = {}
    for item in instructions:
        if (target := getattr(item, "jump_target", None)) is not None:
            jumpers.setdefault(target, set()).add(item.opname)
    handlers = frozenset(entry.target for entry in getattr(dis.Bytecode(code), "exception_entries", ()))
    starts = [index for index, item in enumerate(instructions) if item.opname == "RESUME" and not (item.arg or 0) & 3]
    return instructions, jumpers, handlers, starts[-1] if starts else -1


def entry_places(code: CodeType, offset: int) -> list[tuple[str, str]] | None:
    """Return the sub-line keys of which a run that ran the instruction at `offset` recorded one (`_Recorder`).

    The instruction runs where a run enters the straight stretch of instructions that ends at it, at its first
    instruction or at a branch's destination inside it: a ``start`` key (the code object's first position) for the
    stretch that starts the code object, else the ``arm`` key of each instruction of the stretch. A branch's left way
    is the instruction after it, or after its ``NOT_TAKEN``. None where a way into the stretch reports no event:
    something falls through or jumps into it, an exception does, or a generator resumes into it after a ``yield``
    (a run that stopped at the yield never runs on); or where nothing in it has a place.
    """
    instructions, jumpers, handlers, start = _flow(code)
    at = next((index for index, item in enumerate(instructions) if item.offset == offset), -1)
    stretch: list[int] = []
    for index in range(at, start - 1, -1):
        item = instructions[index]
        stretch.append(item.offset)
        entered = jumpers.get(item.offset, set())
        resumed = item.opname == "RESUME" and index != start
        if item.offset in handlers or entered - BRANCHES or resumed:
            return None
        if index == start:
            return None if entered else [("start", start_place(code))]
        previous = instructions[index - 1].opname
        left = previous in BRANCHES or (
            previous == "NOT_TAKEN" and index > 1 and instructions[index - 2].opname in BRANCHES
        )
        if left or previous in NO_FALL_THROUGH or entered:
            if not left and (previous not in NO_FALL_THROUGH or not entered):
                return None
            found = [("arm", place) for kept in stretch if (place := instruction_place(code, kept)) is not None]
            return found or None
    return None


class _Recorder:
    """Record what the runs of a worker run: lines, code objects that start, files opened (`Runner.lines`).

    `scripts/_cell_catalog.py` builds its catalog from these records. Each `sys.monitoring` event (``LINE``,
    ``PY_START``) records once and disables itself; a fork inherits what is disabled, so in a fork of a worker that
    imported the CLI under the recorder a job records only what it ran beyond that import (`settle` returns it as the
    worker's startup). The worker then imports every other module of the package (`modules`, `keep`), so no job pays
    the imports: what importing a module ran (lines and code objects by the module they are in, files by the module
    that opened them) counts for each job that uses the module: runs its code or imports it, and for the modules
    importing it imported. An ``open`` of a package file counts unless the import system opens it to load a module.
    Only what runs between `begin` and `end` counts. It never runs beside the tracer: a ``LINE`` callback that
    disables itself drops the ``INSTRUCTION`` event of the line's first instruction. Where the interpreter reports
    branches (`SUB_LINE`), it also keys what ran finer than a line: the first position of each code object that
    started (``start``) and the instruction each branch taken led to (``arm``, `instruction_place`), which tell apart
    two code objects or two arms of one line (`entry_places` reads them back).
    """

    def __init__(self, package: Path) -> None:
        """Start recording, for the package below `package`, on the first free tool id."""
        self.package = os.path.normpath(package) + os.sep
        self.paths: dict[str, str | None] = {}
        self.lines: dict[str, set[int]] = {}
        self.codes: dict[str, set[str]] = {}
        self.files: dict[str, set[str]] = {}
        self.starts: dict[str, set[str]] = {}
        self.arms: dict[str, set[str]] = {}
        self.requested: set[str] = set()
        self.names: dict[str, str] = {}
        self.requires: dict[str, set[str]] = {}
        self.imported: tuple[Mapping[str, Iterable[int | str]], ...] = ({}, {}, {}, {}, {})
        self.active = True
        monitoring = sys.monitoring
        self.disable = monitoring.DISABLE
        events = monitoring.events
        self.tool = next(tool for tool in FREE_TOOLS if monitoring.get_tool(tool) is None)
        monitoring.use_tool_id(self.tool, "compare_release lines")
        monitoring.register_callback(self.tool, events.LINE, self._line)
        monitoring.register_callback(self.tool, events.PY_START, self._start)
        wanted = events.LINE | events.PY_START
        for name in ("BRANCH_LEFT", "BRANCH_RIGHT") if SUB_LINE else ():
            monitoring.register_callback(self.tool, getattr(events, name), self._branch)
            wanted |= getattr(events, name)
        monitoring.set_events(self.tool, wanted)
        sys.addaudithook(self._audit)

    def _path(self, filename: str) -> str | None:
        if (path := self.paths.get(filename, _UNSEEN)) is _UNSEEN:
            normal = os.path.normpath(filename)
            path = Path(normal[len(self.package) :]).as_posix() if normal.startswith(self.package) else None
            self.paths[filename] = path
        return path

    def _line(self, code: CodeType, line: int) -> object:
        if not self.active:
            return None
        if (path := self._path(code.co_filename)) is not None:
            self.lines.setdefault(path, set()).add(line)
        return self.disable

    def _start(self, code: CodeType, _: int) -> object:
        if not self.active:
            return None
        if (path := self._path(code.co_filename)) is not None:
            self.codes.setdefault(path, set()).add(code.co_qualname)
            if SUB_LINE:
                self.starts.setdefault(path, set()).add(start_place(code))
        return self.disable

    def _branch(self, code: CodeType, _: int, destination: int) -> object:
        if not self.active:
            return None
        if (path := self._path(code.co_filename)) is not None and (place := instruction_place(code, destination)):
            self.arms.setdefault(path, set()).add(place)
        return self.disable

    def _audit(self, event: str, args: tuple[object, ...]) -> None:
        if event != "open" or not self.active or not args or not isinstance(args[0], (str, os.PathLike)):
            return
        if (path := self._path(os.fspath(args[0]))) is None:
            return
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        if caller is not None and "importlib._bootstrap" in caller.f_code.co_filename:
            return
        opener = None
        while caller is not None and (opener := self._path(caller.f_code.co_filename)) is None:
            caller = caller.f_back
        self.files.setdefault(opener or "", set()).add(path)

    def _request(self, importer: object, name: str, items: object) -> None:
        """Count the modules of the package an import of `name` (and of its `items`) asks for, by its importer."""
        if name != PACKAGE and not name.startswith(f"{PACKAGE}."):
            return
        parts = name.split(".")
        names = {".".join(parts[:end]) for end in range(1, len(parts) + 1)}
        if isinstance(items, (list, tuple)):
            names.update(f"{name}.{item}" for item in items if isinstance(item, str))
        self.requested.update(names)
        if isinstance(importer, str):
            self.requires.setdefault(importer, set()).update(names)

    def _hook_imports(self) -> None:
        """Route `import` statements and `importlib.import_module` through `_request`."""
        original_import, original_module = builtins.__import__, importlib.import_module

        def import_(name: str, *args: object, **kwargs: object) -> object:
            given = dict(zip(IMPORT_ARGUMENTS, args, strict=False)) | kwargs
            namespace, level = given.get("globals"), given.get("level", 0)
            importer = namespace.get("__name__") if isinstance(namespace, dict) else None
            absolute = name
            if isinstance(level, int) and level and isinstance(namespace, dict):
                anchor = str(namespace.get("__package__") or "").rsplit(".", level - 1)[0]
                absolute = f"{anchor}.{name}" if name else anchor
            self._request(importer, absolute, given.get("fromlist"))
            return original_import(name, *args, **kwargs)

        def import_module(name: str, package: str | None = None) -> ModuleType:
            with contextlib.suppress(ImportError, ValueError):
                self._request(None, importlib.util.resolve_name(name, package), ())
            return original_module(name, package)

        for hook in (import_, import_module):
            hook.__code__ = hook.__code__.replace(co_filename="<compare_release lines importlib._bootstrap hook>")
        builtins.__import__ = import_
        importlib.import_module = import_module

    def settle(self) -> Record:
        """End the record of the CLI's import and return it; what runs next is the import of the other modules."""
        startup = self.keys()
        self.begin()
        self.requires = {}
        self._hook_imports()
        root = Path(self.package)
        for path in sorted(root.rglob("*.py")):
            parts = path.relative_to(root).with_suffix("").parts
            self.names[".".join((PACKAGE, *parts)).removesuffix(".__init__")] = path.relative_to(root).as_posix()
        return startup

    @property
    def modules(self) -> list[str]:
        """The modules of the package a worker imports before its first job: all but `_runtime`, which is copied."""
        return [name for name, path in self.names.items() if "_runtime" not in Path(path).parts]

    def keep(self) -> None:
        """Keep what importing the modules ran, by module (`keys`)."""
        self.imported = (self.lines, self.codes, self.starts, self.arms, self.files)

    def begin(self) -> None:
        """Start a job's record."""
        self.lines, self.codes, self.files, self.starts, self.arms = {}, {}, {}, {}, {}
        self.requested, self.active = set(), True

    def end(self) -> None:
        """End a job's record: what runs next (the comparator's own checks) does not count."""
        self.active = False

    def _used(self) -> set[str]:
        """Return the paths of the modules the record used and of those their imports imported."""
        pending = [name for name, path in self.names.items() if path in self.lines or path in self.codes]
        pending += self.requested
        seen: set[str] = set()
        while pending:
            if (name := pending.pop()) not in seen:
                seen.add(name)
                pending += self.requires.get(name, ())
        return {self.names[name] for name in seen if name in self.names}

    def keys(self) -> Record:
        """Return what the record holds, as JSON, with what importing each module it used ran."""
        kept: list[Mapping[str, Iterable[int | str]]] = [self.lines, self.codes, self.starts, self.arms]
        found = [{path: set(items) for path, items in record.items()} for record in kept]
        files = set().union(*self.files.values())
        used = self._used()
        for record, imported in zip(found, self.imported, strict=False):
            for path in used:
                record.setdefault(path, set()).update(imported.get(path, ()))
        files.update(*(self.imported[-1].get(path, ()) for path in used))
        names = ("line", "code", "start", "arm") if SUB_LINE else ("line", "code")
        keys: Record = {
            name: {path: sorted(items) for path, items in sorted(record.items()) if items}
            for name, record in zip(names, found, strict=False)
        }
        return keys | {"file": sorted(files)} | ({"python": "{}.{}".format(*sys.version_info[:2])} if SUB_LINE else {})


_TRACER: list[_Tracer] = []
"""The tracer of this worker, where its runner gave probes and the interpreter has `sys.monitoring`."""
_RECORDER: list[_Recorder] = []
"""The recorder of this worker, where its runner asked for `lines` (`Runner.lines`)."""


def _generate_recorded(job: Record, main: Callable[[list[str]], object]) -> int:
    """Run one generation and write what it showed, handled, ran and wrote outside to `job["record"]` (`Outcome`).

    Warnings show under Python's default filters with deprecations shown too; entering the filters again per run
    resets the registries of warnings already shown, and emptying the cache of `re` makes a pattern an earlier run
    compiled warn again. The memory cap holds while the generator runs, so memory runs
    out in the run, which reports it, and not in the recording around it. The record starts empty, like the log, so
    a run that dies or runs out of memory leaves no record of an earlier run: its exit status says what happened.
    """
    record = Path(job["record"])
    record.write_text("", encoding="utf-8")
    re.purge()
    recorder = _WarningRecorder()
    main_file = sys.modules["datamodel_code_generator.__main__"].__file__ or ""
    for tracer in (*_TRACER, *_RECORDER):
        tracer.begin()
    with _handled_exceptions(main_file) as caught, warnings.catch_warnings():
        warnings.simplefilter("default", PendingDeprecationWarning)
        warnings.simplefilter("default", DeprecationWarning)
        warnings.filters.insert(0, ("always", None, Warning, recorder, 0))
        warnings.showwarning = recorder.show
        with _memory_cap(job.get("memory_mb", 0)) as over_cap, _listing(job["listing"]):
            code = OUT_OF_MEMORY if over_cap else _exit_code(main, job["args"])
        if over_cap:
            sys.stderr.write("MemoryError\n")  # what a run out of memory prints last
        for tracer in (*_TRACER, *_RECORDER):
            tracer.end()
        handled = [f"{type(exception).__module__}.{type(exception).__qualname__}" for exception in caught]
    facts = {"warnings": recorder.records, "handled": handled, "outside": _outside(job["args"], job["out"])}
    facts["ran"] = next((tracer.ran() for tracer in _TRACER), None)
    facts["keys"] = next((recorder.keys() for recorder in _RECORDER), None)
    partial = record.with_name(record.name + ".tmp")
    partial.write_text(json.dumps(facts), encoding="utf-8")
    partial.replace(record)
    return code


@contextlib.contextmanager
def _listing(listing: Listing) -> Generator[None, None, None]:
    """Make `Path.rglob` return its paths in path order while the block runs, where `listing` asks for it.

    Every release lists a directory input with `Path.rglob`, in the order of the file system, and releases up to
    0.103.0 keep that order for files of one name (#7552), so the numbered names of colliding models differ between
    machines. Sorted, as `datamodel_code_generator._paths.files_under` sorts them since, a release reads the same files
    in the same order everywhere. It replaces the `Path.rglob` the run finds, so one the package installed when it was
    imported lists beneath it.
    """
    if listing != "path":
        yield
        return
    rglob = Path.rglob

    def in_path_order(self: Path, pattern: str, **kwargs: bool | None) -> Generator[Path, None, None]:
        yield from sorted(rglob(self, pattern, **kwargs))

    Path.rglob = in_path_order
    try:
        yield
    finally:
        Path.rglob = rglob


@contextlib.contextmanager
def _memory_cap(limit_mb: int) -> Generator[bool, None, None]:
    """Cap the data memory of this process (`RLIMIT_DATA`) at `limit_mb` while the block runs (0: no cap).

    The cap counts the whole worker, which recycling bounds; where the platform refuses it (macOS), the run goes
    uncapped. The block gets whether the worker already holds more than the cap (`VmData`, the number the kernel checks
    a new mapping against): its allocations then fail wherever the freed memory of its heap does not cover them, so
    the run would end in a MemoryError, a SystemError of some C call or a crash, by chance. Such a run is stopped
    where it would start.
    """
    if not limit_mb:
        yield False
        return
    import resource  # ruff: ignore[import-outside-top-level] - POSIX only, like the fork of an import check

    limits = resource.getrlimit(resource.RLIMIT_DATA)
    data_kb = _data_kb()  # read before the cap: under it, even reading a file can run out of memory
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_DATA, (limit_mb << 20, limits[1]))
    held = resource.getrlimit(resource.RLIMIT_DATA)[0] == limit_mb << 20
    try:
        yield held and data_kb > limit_mb << 10
    finally:
        resource.setrlimit(resource.RLIMIT_DATA, limits)


def _data_kb() -> int:
    """Return the data memory this process holds in kB, as `VmData` shows it (0 where there is no `/proc`)."""
    status = Path("/proc/self/status")
    text = status.read_text(encoding="ascii") if status.exists() else ""
    return next((int(line.split()[1]) for line in text.splitlines() if line.startswith("VmData:")), 0)


@contextlib.contextmanager
def _job_scope(job: Record) -> Generator[None, None, None]:
    """Run a job with its output and errors in its log, in its directory and under its timeout.

    All of it is restored at the end, so the worker runs its next job as it ran this one. The timeout is `SIGALRM` at
    its default action: a run that hangs, in C code too, ends the process, and the driver reads the signal as the
    run's exit status.
    """
    saved = [os.dup(1), os.dup(2)]
    log = os.open(job["log"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    cwd = Path.cwd()
    os.chdir(job["cwd"])
    signal.alarm(job["timeout"])
    try:
        yield
    finally:
        signal.alarm(0)
        sys.stdout.flush()
        sys.stderr.flush()
        os.chdir(cwd)
        for fd, copy in enumerate(saved, start=1):
            os.dup2(copy, fd)
            os.close(copy)


def _run_job(job: Record, main: Callable[[list[str]], object]) -> int:
    """Run one job and return its exit code: a generation in this worker, an import check in a fresh child.

    An import check runs the generated code, which has to leave nothing behind, so it gets a child of its own; so
    does a recorded generation (`lines`), so that what it records depends on no job before it. The child enables
    the recorder's events again: code that ran while the worker warmed up (a function an import called) has them
    disabled in every fork, and a cell that runs it again must show it.
    """
    if job["kind"] == "generate" and not _RECORDER:
        with _job_scope(job):
            return _generate_recorded(job, main)
    if (pid := os.fork()) == 0:  # pragma: no cover - child process
        code = 1
        try:
            if _RECORDER:
                sys.monitoring.restart_events()  # events of code the warm-up ran are disabled
            with _job_scope(job):
                code = _generate_recorded(job, main) if job["kind"] == "generate" else _import_modules(job["out"])
        finally:
            os._exit(code)
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def _worker() -> None:
    """Serve jobs: the first line on stdin holds the probes to trace and whether to record lines (`Worker`).

    Each later line is a job. The tracer or the recorder is installed before the package is imported, so the import
    of the CLI is measured too; a recorder reports what that import ran as the worker's `startup`.
    """
    sys.argv[0] = "datamodel-codegen"
    asked = json.loads(sys.stdin.readline() or "{}")
    probes = asked.get("probes")
    if (probes is not None or asked.get("lines")) and hasattr(sys, "monitoring"):
        spec = importlib.util.find_spec("datamodel_code_generator")
        package = Path(spec.origin if spec and spec.origin else "").parent
        if probes is not None:
            _TRACER.append(tracer := _Tracer(package, probes))
            _track_imports(tracer)
        else:
            _RECORDER.append(_Recorder(package))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from datamodel_code_generator.__main__ import main  # ruff: ignore[import-outside-top-level] - imported once per worker

        startup = next((recorder.settle() for recorder in _RECORDER), None)
        for name in (*WARM_IMPORTS, *(module for recorder in _RECORDER for module in recorder.modules)):
            with contextlib.suppress(ImportError):
                importlib.import_module(name)
        for recorder in _RECORDER:
            recorder.keep()
    # Importing the CLI installs its own SIGINT handler; a worker stops on SIGINT, the driver by closing its stdin.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    package = sys.modules["datamodel_code_generator"]
    version = ""
    tracers = [*_TRACER, *_RECORDER]
    was_active = [tracer.active for tracer in tracers]
    for tracer in tracers:
        tracer.active = False  # the lookup is the comparator's own: no generation is credited with its code
    with contextlib.suppress(Exception):  # a stand-in package or a tree without a version file reports none
        version = package.get_version()
    for tracer, active in zip(tracers, was_active, strict=True):
        tracer.active = active
    ready = {"package": package.__file__, "measured": bool(_TRACER), "startup": startup, "version": version}
    sys.stdout.write(json.dumps(ready) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        code = _run_job(json.loads(line), main)
        sys.stdout.write(json.dumps({"rc": code}) + "\n")
        sys.stdout.flush()
    for tracer in _TRACER:
        tracer.stop()


# Driver side.


def run_environment(source: Path | None) -> dict[str, str]:
    """Return the environment of a side's processes, built from scratch: `source` is the tree it imports, if any.

    Only `PASSED_VARIABLES` come from the caller; the hash seed is the same on each side, so the order of a set
    cannot differ between them.
    """
    env = {name: os.environ[name] for name in PASSED_VARIABLES if name in os.environ}
    env |= {"PYTHONWARNINGS": "ignore", "PYTHONHASHSEED": "0", "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES"}
    return env | ({"PYTHONPATH": str(source)} if source else {})


class Worker:
    """A warm worker process for one interpreter; `jobs` counts the jobs it was given."""

    def __init__(
        self,
        python: Path,
        env: dict[str, str],
        package: Path | None,
        probes: list[Record] | None = None,
        *,
        lines: bool = False,
    ) -> None:
        """Start the worker with Python's default warning filters, which its runs show warnings under.

        It refuses a datamodel-code-generator imported from outside `package` (any where None). Both paths are
        normalized, not resolved: a side's package path and its `package` come from the same unresolved path. It
        traces `probes` (`Runner.probes`) from before it imports the package, where the interpreter can (`measured`),
        or records what its runs run (`lines`, `Runner.lines`; `startup` is what importing the CLI ran).
        """
        self.process = subprocess.Popen(
            [str(python), str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={name: value for name, value in env.items() if name != "PYTHONWARNINGS"},
        )
        stdin = self.process.stdin
        assert stdin is not None
        stdin.write(json.dumps({"probes": probes, "lines": lines}) + "\n")
        stdin.flush()
        self.jobs = 0
        if (ready := self._reply()) is None:
            self.close()
            msg = f"worker exited with {self.process.returncode}"
            raise RuntimeError(msg)
        self.package = Path(os.path.normpath(Path(ready["package"]).absolute())).parent
        self.measured = bool(ready.get("measured"))
        self.startup: Record | None = ready.get("startup")
        self.version = str(ready.get("version") or "")
        if package is not None and not self.package.is_relative_to(os.path.normpath(package.absolute())):
            self.close()
            msg = f"{python} imports datamodel_code_generator from {self.package}, not from below {package}"
            raise SystemExit(msg)

    def _reply(self) -> Record | None:
        stdout = self.process.stdout
        assert stdout is not None
        return json.loads(reply) if (reply := stdout.readline()) else None

    def run(self, job: Record) -> int:
        """Run one job and return its exit code; a job that ended the worker returns the worker's exit status.

        Such a job hit its timeout or crashed the interpreter, as it would have ended a process of its own.
        """
        stdin = self.process.stdin
        assert stdin is not None
        stdin.write(json.dumps(job) + "\n")
        stdin.flush()
        self.jobs += 1
        return self.process.wait() if (reply := self._reply()) is None else reply["rc"]

    def close(self) -> None:
        """Stop the worker."""
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait()
        if self.process.stdout:
            self.process.stdout.close()


@dataclass(frozen=True)
class Side:
    """One interpreter to generate with, and the directory its datamodel-code-generator must come from.

    `package` is None for an interpreter whose own installation runs (`--base-python`). `listing` is the order its runs
    list a directory input in (`_listing`): path order, so that a release reads one on every machine as on any other, or
    the file system's (`system`), which a head side keeps: it runs as it is.
    """

    name: str
    python: Path
    package: Path | None
    listing: Listing


FACTS = ("code", "tree", "warnings", "handled", "messages")
"""What `identity_changes` compares, in the order it reports them."""


@dataclass
class Outcome:
    """Result of generating one run on one side.

    `code` is the exit status, `messages` what the run printed without the frames of tracebacks, `warnings` the
    warnings it showed as records (category, message, module) and `handled` the classes of the exceptions
    `datamodel_code_generator.__main__` caught; paths of the run's directory, output and package read `<tmp>`,
    `<output>` and `<package>`. `first` holds these facts of a refused single-file attempt before a modular run.
    `tree` is every file the run wrote below its directory, which is its working directory too, so an output written
    to a relative path (a client's package) is compared, with those paths and the side's version lines normalised
    (`_tree`); `outside` the outputs its arguments name elsewhere
    (`_outside`). `ran` are the probes of its runner (`Runner.probes`, by index) the run ran, over both attempts; None
    where none were asked for or the interpreter cannot tell. `keys` are what each attempt ran where its runner
    records lines (`Runner.lines`, `scripts/_cell_catalog.py`).
    """

    ok: bool
    error: str
    tree: dict[str, str]
    import_error: str
    cacheable: bool = True
    code: int = 0
    messages: str = ""
    warnings: list[list[str]] = field(default_factory=list)
    handled: list[str] = field(default_factory=list)
    first: Record = field(default_factory=dict)
    outside: list[str] = field(default_factory=list)
    ran: list[int] | None = None
    keys: list[Record] | None = None

    def stopped(self) -> bool:
        """Whether the timeout or the memory cap stopped the run: another cap may let it finish."""
        return self.code in {-signal.SIGALRM, OUT_OF_MEMORY}


def _fact_changes(base: Record, candidate: Record) -> list[tuple[str, str]]:
    """Return what differs between the `FACTS` of two attempts as (kind, detail); a fact one lacks is None."""
    changes = []
    for kind in FACTS:
        old, new = base.get(kind), candidate.get(kind)
        if old == new:
            continue
        match kind:
            case "code":
                changes.append(("exit", f"exit status {old} -> {new}"))
            case "tree":
                old, new = old or {}, new or {}
                files = [name for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)]
                changes.append(("output", f"output differs: {', '.join(files)}"))
            case "warnings" | "handled":
                before, after = Counter(map(json.dumps, old or [])), Counter(map(json.dumps, new or []))
                removed, added = sorted((before - after).elements()), sorted((after - before).elements())
                moved = f"{kind} differ: {', '.join(removed) or 'none'} -> {', '.join(added) or 'none'}"
                detail = moved if removed or added else f"{kind} in another order"
                changes.append((kind, detail))
            case _:
                before, after = (old or "").splitlines(), (new or "").splitlines()
                index = next(
                    (index for index, (one, other) in enumerate(zip(before, after, strict=False)) if one != other),
                    min(len(before), len(after)),
                )
                changes.append((kind, f"messages differ: {before[index : index + 1]} -> {after[index : index + 1]}"))
    return changes


def identity_changes(base: Outcome, candidate: Outcome) -> list[tuple[str, str]]:
    """Return what differs between two runs of one cell as (kind, detail).

    The kinds are `exit`, `output`, `warnings`, `handled`, `messages` and `first attempt` (a fact of the refused
    single-file attempt before a modular run).
    """
    first = [("first attempt", f"first attempt {detail}") for _, detail in _fact_changes(base.first, candidate.first)]
    return _fact_changes(base.__dict__, candidate.__dict__) + first


def _probe(python: Path) -> tuple[str, str]:
    """Return the interpreter's `major.minor` version and its installed datamodel-code-generator version."""
    code = (
        "import sys\n"
        "from importlib.metadata import version\n"
        "print(f'{sys.version_info[0]}.{sys.version_info[1]}', version('datamodel-code-generator'))\n"
    )
    python_version, dcg_version = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True, env=run_environment(None), check=True
    ).stdout.split()
    return python_version, dcg_version


def _is_generator_requirement(line: str) -> bool:
    """Return whether a `pip freeze` line installs datamodel-code-generator itself, e.g. this checkout."""
    if line.startswith("-e "):
        source = Path(line.removeprefix("-e ").removeprefix("file://"))
        return any((source / sub / "datamodel_code_generator").is_dir() for sub in ("src", "."))
    return line.lower().replace("_", "-").startswith(("datamodel-code-generator==", "datamodel-code-generator @"))


def _candidate_packages(candidate: Path) -> tuple[str, list[str]]:
    """Return the candidate's `major.minor` Python version and its packages but the generator, as `pip freeze` pins."""
    python_version, _ = _probe(candidate)
    freeze = subprocess.run(
        ["uv", "pip", "freeze", "--python", str(candidate)], capture_output=True, text=True, check=True
    ).stdout
    return python_version, sorted(line for line in freeze.splitlines() if line and not _is_generator_requirement(line))


def _environment_name(base: str, python_version: str, pins: list[str]) -> str:
    key = hashlib.sha256(json.dumps([base, python_version, pins]).encode()).hexdigest()[:12]
    return f"{base}-py{python_version}-{key}"


def built(root: Path, build: Callable[[], object]) -> Path:
    """Return `root` once `build` has filled it, built once under a lock: another run may be building the same.

    A root without its `.complete` mark (a build that stopped half way) is removed and built again.
    """
    root.parent.mkdir(parents=True, exist_ok=True)
    import fcntl  # ruff: ignore[import-outside-top-level] - POSIX only; keeps the module importable elsewhere

    with (root.parent / f"{root.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (root / ".complete").exists():
            shutil.rmtree(root, ignore_errors=True)
            build()
            (root / ".complete").touch()
    return root


def _base_python(version: str, candidate: Path, cache_dir: Path) -> Path:
    """Install the release next to the candidate's other packages, pinned to the same versions."""
    python_version, pins = _candidate_packages(candidate)
    root = cache_dir / "venvs" / _environment_name(version, python_version, pins)
    return built(root, lambda: _install_base(version, python_version, pins, root)) / "bin" / "python"


def _install_base(version: str, python_version: str, pins: list[str], root: Path) -> None:
    """Create the release environment with the candidate's package versions pinned."""
    print(f"installing datamodel-code-generator {version} into {root}", file=sys.stderr, flush=True)
    subprocess.run(["uv", "venv", "-q", "--python", python_version, str(root)], check=True)
    requirements = root / "pins.txt"
    requirements.write_text("\n".join(pins) + "\n", encoding="utf-8")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--python",
            str(root / "bin" / "python"),
            "-r",
            str(requirements),
            f"datamodel-code-generator=={version}",
        ],
        check=True,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `repo` without the caller's repository settings (a hook's GIT_DIR) or an interactive prompt.

    A command that fails prints nothing on stdout.
    """
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=False)


def commit_of(spec: str, repo: Path = ROOT) -> str:
    """Return the commit a git revision of `repo` names, or an empty string for a spec that names none."""
    return git(repo, "rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}").stdout.strip()


def archive_version(revision: str, repo: Path = ROOT) -> str:
    """Return the version the build hook writes for a revision (`git archive` leaves the version file out)."""
    result = git(repo, "describe", "--tags", "--long", "--match", "[0-9]*", "--exclude", "*+*", revision)
    if (described := result.stdout.strip()).count("-") < 2:  # ruff: ignore[magic-value-comparison]
        return f"0.0.0+g{revision[:9]}"
    tag, distance, commit = described.rsplit("-", 2)
    if distance == "0":
        return tag
    major_minor, _, patch = tag.rpartition(".")
    return f"{major_minor}.{int(patch) + 1}.dev{distance}+{commit}"


def _extract(revision: str, repo: Path, paths: tuple[str, ...], root: Path) -> None:
    root.mkdir(parents=True)
    archive = root / "archive.tar"
    git(repo, "archive", "--output", str(archive), revision, *paths).check_returncode()
    subprocess.run(["tar", "-x", "-f", str(archive), "-C", str(root)], check=True)
    archive.unlink()
    version = f"__version__ = {archive_version(revision, repo)!r}\n"
    (root / "src" / "datamodel_code_generator" / "_version.py").write_text(version, encoding="utf-8")


def source_tree(revision: str, cache_dir: Path, repo: Path = ROOT, paths: tuple[str, ...] = ("src",)) -> Path:
    """Return the root of a commit's `paths` (a base runs `src`), extracted once below `cache_dir/trees`.

    The version file is not in the repository (the build hook writes it), so it is written here.
    """
    root = cache_dir / "trees" / "-".join((revision, *paths))
    return built(root, lambda: _extract(revision, repo, paths, root))


@dataclass(frozen=True)
class Base:
    """The side a run is compared with: its interpreter, the source tree it imports, and its outputs' cache name.

    `package` is the directory its datamodel-code-generator must come from: the tree of a revision, the environment
    of a release, None for an interpreter that has the base installed (`--base-python`).
    """

    python: Path
    source: Path | None
    cache_name: str
    revision: str | None = None
    package: Path | None = None

    def env(self) -> dict[str, str]:
        """Return the environment of the base's processes: a revision's tree on PYTHONPATH, nothing of the caller's."""
        return run_environment(self.source)


def installed_base(python: Path) -> Base:
    """Return the base of an interpreter that has the base version installed already (`--base-python`)."""
    identity = json.dumps([str(python), *_probe(python)])
    return Base(python, None, f"python-{hashlib.sha256(identity.encode()).hexdigest()[:12]}")


def resolve_base(spec: str, candidate: Path, cache_dir: Path, repo: Path = ROOT) -> Base:
    """Resolve the base a run is compared with, a released version or a git revision of `repo`.

    A release is installed beside the candidate's packages (`_base_python`); a revision's `src/` runs with the
    candidate's interpreter. Either way only generator changes differ.
    """
    if RELEASE.fullmatch(spec):
        python = _base_python(spec, candidate, cache_dir)
        return Base(python, None, python.parents[1].name, package=python.parents[1])
    if not (revision := commit_of(spec, repo)):
        msg = f"{spec} is neither a released version nor a revision of {repo}"
        raise SystemExit(msg)
    source = source_tree(revision, cache_dir, repo) / "src"
    name = _environment_name(revision, *_candidate_packages(candidate))
    return Base(candidate, source, name, revision, source)


def _tree(path: Path, paths: tuple[Path, Path, Path] | None = None, version: str = "") -> dict[str, str]:
    """Return every file below a run's directory by its relative path, outside hidden and cache directories.

    With `paths` (the run's output, directory and package: `_relative`) the text of each file reads the same on each
    run and side, and with a `version` its header line reads the same on each side (`_versioned`).
    """
    tree = {
        file.relative_to(path).as_posix(): file.read_text(encoding="utf-8", errors="surrogateescape")
        for file in _hashed_files(path)
    }
    return {name: _versioned(_relative(text, *paths) if paths else text, version) for name, text in tree.items()}


def _versioned(text: str, version: str) -> str:
    """Return `text` with the version line of the package's headers and of `--version` as `<version>`.

    Only the lines the package writes change: `#   version:   <version>` (`--enable-version-header`) and
    `datamodel-codegen <version>` (`--version`). A replacement of every occurrence would also change a schema literal
    that equals the version, and it could never hide a difference the lines do not show.
    """
    if not version:
        return text
    for line in (r"#   version:   ", r"datamodel-codegen "):
        text = re.sub(rf"(?m)^{line}{re.escape(version)}$", f"{line}<version>", text)
    return text


def _relative(text: str, out: Path, tmp: Path, package: Path) -> str:
    """Return `text` with the paths of a run's output, directory and package as `<output>`, `<tmp>`, `<package>`.

    So they read the same on each run and side. The resolved form of each goes first (macOS resolves `/var` to
    `/private/var`, which ends in the other form).
    """
    for path, name in ((out, "<output>"), (tmp, "<tmp>"), (package, "<package>")):
        text = text.replace(os.path.realpath(path), name).replace(str(path), name)
    return text


def _last_line(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else "error"


def ran_out_of_memory(last_line: str) -> bool:
    """Tell from the last log line of a failed run under a memory cap whether the cap stopped it.

    Besides the MemoryError, an allocation that fails inside C code can end the run with the SystemError CPython raises
    for an error returned without an exception set (11-10: such a run was cached, and the next gate run read it back).
    """
    return (
        last_line.partition(":")[0] == "MemoryError" or last_line == "SystemError: error return without exception set"
    )


def _attempt(rc: int, log: Path, record: Path, paths: tuple[Path, Path, Path], version: str = "") -> Record:
    """Return what one attempt of a run showed (`Outcome`); `paths` are its output, directory and package."""
    printed = _versioned(TRACEBACK_FRAMES.sub("", log.read_text(encoding="utf-8", errors="replace")), version)
    recorded = json.loads(text) if record.exists() and (text := record.read_text(encoding="utf-8")) else {}
    return {
        "code": rc,
        "messages": _relative(printed, *paths),
        "warnings": [
            [category, _relative(message, *paths), module] for category, message, module in recorded.get("warnings", [])
        ],
        "handled": recorded.get("handled", []),
        "outside": [_relative(path, *paths) for path in recorded.get("outside", [])],
        "ran": recorded.get("ran"),
        "keys": recorded.get("keys"),
    }


class Runner:
    """Runs one side of the matrix through per-thread warm workers, each replaced after `recycle` jobs."""

    def __init__(
        self,
        side: Side,
        env: dict[str, str],
        *,
        import_check: bool,
        timeout: int,
        memory_mb: int = 0,
    ) -> None:
        """Prepare a runner; workers start lazily. `memory_mb` caps the data memory of a worker's run (0: no cap).

        `probes` are the probes of this side's changed code objects (`changed_units`) each outcome reports as run or
        not (`Outcome.ran`); None asks for none. `measured` turns false once a worker cannot trace them. `lines` makes
        the workers record what each run ran instead (`Outcome.keys`; each run in a fork of its worker).
        """
        self.side = side
        self.env = env
        self.import_check = import_check
        self.timeout = timeout
        self.memory_mb = memory_mb
        self.recycle = RECYCLE
        self.probes: list[Record] | None = None
        self.lines = False
        self.measured = True
        self.workers: dict[int, Worker] = {}
        self.lock = threading.Lock()

    def _worker(self, *, fresh: bool = False) -> Worker:
        """Return this thread's worker: a new one in place of one that ran `recycle` jobs or has ended.

        A `fresh` job (one that changes process-wide state, as `--debug` does by wrapping the classes imported after
        it) also gets a new worker in place of one that ran any job: a run in a used process would not behave like one
        in a process of its own.
        """
        worker = self.workers.get(thread := threading.get_ident())
        if (
            worker is None
            or worker.jobs >= self.recycle
            or worker.process.poll() is not None
            or (fresh and worker.jobs)
        ):
            if worker is not None:
                worker.close()
            worker = Worker(self.side.python, self.env, self.side.package, self.probes, lines=self.lines)
            with self.lock:
                self.workers[thread] = worker
                self.measured = self.measured and (worker.measured or self.probes is None)
        return worker

    def generate(self, args: list[str], tmp: Path) -> Outcome:
        """Generate one run into a fresh directory under `tmp`, which is its working directory too."""
        out = tmp / self.side.name
        out.mkdir()
        log = tmp / f"{self.side.name}.log"
        record = tmp / f"{self.side.name}-record.json"
        job = {
            "kind": "generate",
            "cwd": str(out),
            "out": str(out),
            "log": str(log),
            "record": str(record),
            "timeout": self.timeout,
            "memory_mb": self.memory_mb,
            "listing": self.side.listing,
            "args": [*args, "--output", str(out / "model.py")],
        }
        fresh = bool(PROCESS_STATE_OPTIONS.intersection(args))
        rc = (worker := self._worker(fresh=fresh)).run(job)
        first: Record = {}
        if rc != 0 and any(text in log.read_text(encoding="utf-8", errors="replace") for text in MODULAR_OUTPUT_ERRORS):
            first = _attempt(rc, log, record, (out, tmp, worker.package), worker.version)
            job["args"] = [*args, "--output", str(out / "pkg")]
            rc = (worker := self._worker(fresh=fresh)).run(job)
        if rc > 0 and self.memory_mb and ran_out_of_memory(_last_line(log)):
            rc = OUT_OF_MEMORY
        if rc == OUT_OF_MEMORY or fresh:
            worker.close()
        paths = (out, tmp, worker.package)
        facts = _attempt(rc, log, record, paths, worker.version) | {"first": first}
        if (earlier := first.pop("ran", None)) is not None:
            facts["ran"] = sorted({*earlier, *(facts["ran"] or [])})
        attempts = [first.pop("keys", None), facts.pop("keys")]
        facts["keys"] = [keys for keys in attempts if keys is not None] or None
        if rc != 0:
            return Outcome(
                ok=False,
                error=self._error(rc, log),
                tree=_tree(out, paths, worker.version),
                import_error="",
                cacheable=rc > 0 and rc != OUT_OF_MEMORY,
                **facts,
            )
        import_error = ""
        if self.import_check:
            import_log = tmp / f"{self.side.name}-import.log"
            job = {"kind": "import", "cwd": str(tmp), "log": str(import_log), "timeout": self.timeout, "out": str(out)}
            if (rc := self._worker().run(job)) != 0:
                import_error = self._error(rc, import_log)
        tree = _tree(out, paths, worker.version)
        return Outcome(ok=True, error="", tree=tree, import_error=import_error, cacheable=rc >= 0, **facts)

    def _error(self, rc: int, log: Path) -> str:
        if rc == -signal.SIGALRM:
            return f"timed out after {self.timeout}s"
        if rc == OUT_OF_MEMORY and self.memory_mb:
            return f"ran out of its {self.memory_mb} MB"
        return _last_line(log)

    def close(self) -> None:
        """Stop all workers."""
        for worker in self.workers.values():
            worker.close()


def _hashed_files(path: Path, *, hidden: bool = False) -> list[Path]:
    """Return a file itself, or a directory's files outside cache dirs and, unless `hidden`, outside hidden dirs.

    An input's cache key reads the files under hidden directories too (`hidden`): a directory input reads their text
    files (`is_hidden_binary`), so adding, removing or changing one must change the key. A run's output tree does not:
    a formatter leaves its cache there (`.ruff_cache`), which differs on every run.
    """
    if not path.is_dir():
        return [path]
    return [
        file
        for file in sorted(path.rglob("*"))
        if file.is_file()
        and not any(
            part == "__pycache__" or (not hidden and part.startswith(".")) for part in file.relative_to(path).parts[:-1]
        )
    ]


@functools.cache
def _content_hash(path: Path) -> str | None:
    """Hash a file, or every file under a directory with its relative path; None when something is unreadable."""
    digest = hashlib.sha256()
    try:
        for file in _hashed_files(path, hidden=True):
            digest.update(file.relative_to(path).as_posix().encode() + b"\0" if file != path else b"")
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    except OSError:
        return None
    return digest.hexdigest()


LOCAL_REFERENCE = re.compile(r"""\$ref["']?\s*:\s*["']?([^"'#\s,}]+)""")


@functools.cache
def _referenced_files(path: Path) -> frozenset[Path]:
    """Return the local files a schema file or directory references through `$ref`, followed transitively."""
    found: set[Path] = set()
    pending = list(_hashed_files(path))
    while pending:
        file = pending.pop()
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for target in LOCAL_REFERENCE.findall(text):
            if "://" in target:
                continue
            referenced = (file.parent / target).resolve()
            if referenced not in found and referenced.is_file():
                found.add(referenced)
                pending.append(referenced)
    return frozenset(found)


def _path_arguments(args: list[str]) -> list[Path]:
    """Return existing absolute paths among the arguments, including `--option=path` values."""
    values = [arg.partition("=")[2] if arg.startswith("--") else arg for arg in args]
    return [Path(value) for value in values if value and Path(value).is_absolute() and Path(value).exists()]


class OutputCache:
    """Base outputs keyed by the input, the files it can reference, and the arguments.

    The key covers every file under the input's directory, the local files the input references through `$ref`
    (transitively, also outside that directory), and every file or directory named by an absolute path argument,
    such as `--aliases` or `--custom-template-dir`. A base lists a directory input in path order (`Side.listing`),
    so its outcome depends on the files the key covers and not on the order a file system lists them in. Runs whose
    files cannot all be read are not cached.
    """

    def __init__(self, root: Path | None, measured: str = "") -> None:
        """Use `root` as the cache directory, or disable caching when it is None.

        `measured` keys what a traced base ran (`Outcome.ran`): the digest of its probes, the interpreter that
        measured and these scripts (empty for an untraced base).
        """
        self.root = root
        self.measured = measured
        self.hits = 0

    def _path(self, schema: Path, args: list[str], *, import_check: bool) -> Path | None:
        if self.root is None:
            return None
        schema = schema.resolve()
        paths = [schema.parent, *sorted(_referenced_files(schema)), *_path_arguments(args)]
        files = [(str(path), _content_hash(path)) for path in paths]
        if any(digest is None for _, digest in files):
            return None
        key = json.dumps([CACHE_FORMAT, str(schema), files, args, import_check, self.measured])
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, schema: Path, args: list[str], *, import_check: bool) -> Outcome | None:
        """Return a cached outcome, if any."""
        path = self._path(schema, args, import_check=import_check)
        if path is None or not path.exists():
            return None
        self.hits += 1
        return Outcome(**json.loads(path.read_text(encoding="utf-8")))

    def put(self, schema: Path, args: list[str], outcome: Outcome, *, import_check: bool) -> None:
        """Store an outcome."""
        path = self._path(schema, args, import_check=import_check)
        if path is None or not outcome.cacheable:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(outcome.__dict__), encoding="utf-8")
        tmp.replace(path)


CHANGE_UNITS = Path(__file__).with_name("_change_units.py")
"""The script that maps a change to the code objects a comparison must run (run by the measuring interpreter)."""


@dataclass
class Units:
    """The changed code objects of both sides and their probes, as `scripts/_change_units.py` maps them.

    `probes` are each side's probes in one list (a probe's ``unit`` is its unit's index), which the side's workers
    trace and its outcomes name by index (`Outcome.ran`).
    """

    units: list[Record] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)
    readers: list[str] = field(default_factory=list)
    probes: dict[str, list[Record]] = field(default_factory=dict)
    docs: list[str] = field(default_factory=list)

    @classmethod
    def mapped(cls, mapped: Record) -> Units:
        """Return the units of a `_change_units` reply, with each side's probes numbered."""
        found = cls(
            mapped["units"],
            mapped["unreferenced"],
            mapped["annotation_readers"],
            {"base": [], "head": []},
            mapped["docstring_readers"],
        )
        for index, unit in enumerate(found.units):
            unit["index"] = index
            for probe in unit["probes"]:
                found.probes[unit["side"]].append(probe | {"unit": index})
        return found

    def key(self, side: str) -> str:
        """Return the digest of a side's probes, what a cached outcome of that side ran depends on."""
        return _digest(json.dumps(self.probes.get(side, []), sort_keys=True))


def changed_units(
    python: Path, repo: Path, base: Base, candidate: str | None = None, head: Path | None = None
) -> Units | None:
    """Return the code objects a comparison of a candidate with a base revision must run (`scripts/_change_units.py`).

    `python` is the interpreter that measures both sides (a revision's base runs on the candidate's interpreter);
    `candidate` a revision of `repo` or None for its working tree, `head` the `src` the candidate runs. None for a
    base that is no revision: a release's change is a whole cycle.
    """
    if base.revision is None or base.source is None:
        return None
    request = {
        "repo": str(repo),
        "base": base.revision,
        "candidate": candidate,
        "roots": {"base": str(base.source), "head": str(head or repo / "src")},
    }
    mapped = subprocess.run(
        [str(python), str(CHANGE_UNITS)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        env=run_environment(None),
        check=False,
    )
    if mapped.returncode:
        msg = f"mapping the change to code objects failed:\n{mapped.stderr}"
        raise SystemExit(msg)
    return Units.mapped(json.loads(mapped.stdout))


def tested_lines(data: Path) -> dict[str, set[int]]:
    """Return the lines of the package a coverage.py data file (the test suite's) records as run, by path below it."""
    from coverage import CoverageData  # ruff: ignore[import-outside-top-level] - only the driver reads test coverage

    coverage = CoverageData(basename=str(data))
    coverage.read()
    found: dict[str, set[int]] = {}
    marker = f"/{PACKAGE}/"
    for name in coverage.measured_files():
        posix = Path(name).as_posix()
        if (index := posix.rfind(marker)) >= 0:
            found.setdefault(posix[index + len(marker) :], set()).update(coverage.lines(name) or ())
    return found


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8", "surrogateescape"), digest_size=16).hexdigest()


OUTSIDE_PREFIX = "<outside>/"
"""The prefix of the name an output written outside the run's directory has among a cell's files (`cell_facts`)."""

Facts = tuple[int, tuple[tuple[str, str], ...], str, str]
"""What one side of a run proved (`cell_facts`): exit status, each output file's hash, its warnings' and others'
hash."""


def cell_facts(outcome: Outcome) -> Facts:
    """Return what a cell's run proved: its exit status, each file's hash, its warnings' hash, its other facts' hash.

    The files are the run's output files and, under `OUTSIDE_PREFIX`, the outputs its arguments wrote elsewhere
    (`Outcome.outside`: their paths, as they are the only thing known of them), so a difference in either is output.
    The other facts are the exceptions it handled, its messages, the single output file's refusal and the error of
    its import check (empty where it passed or `--import-check` is off, which holds for every cell of a run).
    """
    written = outcome.tree | {f"{OUTSIDE_PREFIX}{path}": "" for path in outcome.outside}
    files = tuple((sys.intern(name), _digest(text)) for name, text in sorted(written.items()))
    other = json.dumps([outcome.handled, outcome.messages, outcome.first, outcome.import_error], sort_keys=True)
    return outcome.code, files, _digest(json.dumps(outcome.warnings)), _digest(other)


def refused_on_head(base: Outcome, candidate: Outcome) -> str:
    """Return what the candidate refuses that the base generated: the run, or the single output file it wrote.

    Empty where the candidate refuses nothing more than the base.
    """
    if base.ok and not candidate.ok:
        return candidate.error
    if base.ok and candidate.first and not base.first:
        refusal = candidate.first["messages"].strip().splitlines() or ["error"]
        return f"the single output file: {refusal[-1]}"
    return ""


GROUP_COUNTS = ("compared", "failed on both sides", "failed on one side", "wrote outside their directory")
"""How `Evidence` counts the runs of an input group: only the first are compared."""
ALWAYS_COUNTED = (True, True, False, False)
"""Which of `GROUP_COUNTS` a group prints when they are 0."""
SIDE_NAMES = {"base": "base", "head": "head"}


def lines_of(where: str) -> set[int]:
    """Return the lines a probe's source span (`L:C-L:C,...`) covers."""
    found: set[int] = set()
    for span in where.split(","):
        start, _, end = span.partition("-")
        if start.partition(":")[0].isdigit() and end.partition(":")[0].isdigit():
            found.update(range(int(start.partition(":")[0]), int(end.partition(":")[0]) + 1))
    return found


@dataclass
class Evidence:
    """What the runs of a comparison compared: per input group and per run, and the changed code objects they ran.

    An input group is the directory of its inputs, whether they are files or directories (modular inputs), the output
    model type and the option set. A run is compared where both sides generated and wrote all their output in the
    run's directory (`Outcome.outside`): a run that failed on one side or both, or wrote elsewhere, compared nothing,
    and the probes its sides ran do not count. Per run it keeps what the run proved (`cell_facts`: exit status and
    hashes), so its size grows with neither the outputs nor the change; per side the probes compared runs ran.
    """

    units: Units | None
    groups: dict[str, list[int]] = field(default_factory=dict)
    cells: dict[str, tuple[str, bool, Facts, Facts]] = field(default_factory=dict)
    ran: dict[str, set[int]] = field(default_factory=lambda: {"base": set(), "head": set()})
    measured: dict[str, bool] = field(default_factory=lambda: {"base": False, "head": False})
    refused: list[str] = field(default_factory=list)
    outside: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def group(path: Path, directory: str, model_type: str, options: str) -> str:
        """Return the name of the input group of a run of `path`, an input in `directory`."""
        return sys.intern(f"{directory} {'directories' if path.is_dir() else 'files'} | {model_type} | {options}")

    def add(self, group: str, name: str, base: Outcome, candidate: Outcome) -> None:
        """Count the run `name` of `group` with what each side showed."""
        outside = sorted({
            *base.outside,
            *candidate.outside,
            *base.first.get("outside", ()),
            *candidate.first.get("outside", ()),
        })
        if base.ok and candidate.ok:
            kind = GROUP_COUNTS.index("wrote outside their directory") if outside else 0
        else:
            kind = GROUP_COUNTS.index(
                "failed on both sides" if not base.ok and not candidate.ok else "failed on one side"
            )
        refused = refused_on_head(base, candidate)
        before, after = cell_facts(base), cell_facts(candidate)
        with self.lock:
            self.groups.setdefault(group, [0] * len(GROUP_COUNTS))[kind] += 1
            self.cells[name] = (group, not kind, before, before if after == before else after)
            if refused:
                self.refused.append(f"{name}: {refused}")
            if outside:
                self.outside.append(f"{name}: {', '.join(outside)}")
            for side, outcome in (("base", base), ("head", candidate)):
                if not kind and outcome.ran is not None:
                    self.measured[side] = True
                    self.ran[side].update(outcome.ran)

    def _missing(self, unit: Record) -> list[Record]:
        probes = self.units.probes[unit["side"]] if self.units else []
        return [
            probe
            for index, probe in enumerate(probes)
            if probe["unit"] == unit["index"] and index not in self.ran[unit["side"]]
        ]

    def group_problems(self) -> list[str]:
        """Return why the input groups are incomplete: some compared nothing, or none ran."""
        empty = sum(not counts[0] for counts in self.groups.values())
        problems = [f"input groups that compared nothing: {empty}"] if empty else []
        return [*problems, *([] if self.groups else ["no input group"])]

    def report(self, tests: dict[str, set[int]] | None) -> list[str]:
        """Print the evidence and return why it is incomplete.

        It is where no input group ran (a selection that matched nothing), where an input group compared nothing, or
        where a changed code object ran in no compared run (it ran in a test of `tests` only, or not at all), cannot
        be measured or is unmeasurable.
        """
        for name, counts in sorted(self.groups.items()):
            nothing = "" if counts[0] else ": compared nothing"
            text = ", ".join(
                f"{count} {kind}"
                for count, kind, shown in zip(counts, GROUP_COUNTS, ALWAYS_COUNTED, strict=True)
                if shown or count
            )
            print(f"evidence: input group {name}: {text}{nothing}")
        problems = self.group_problems()
        if not self.groups:
            print("evidence: input groups: none, the selection matched no input")
        problems += self._report_units(tests)
        for refusal in sorted(self.refused):
            print(f"evidence: refused on head only: {refusal}")
        for written in sorted(self.outside):
            print(f"evidence: not compared, an output outside the run's directory: {written}")
        print(f"evidence: {'incomplete: ' + ', '.join(problems) if problems else 'complete'}")
        return problems

    def _report_units(self, tests: dict[str, set[int]] | None) -> list[str]:
        """Print what became of each changed code object and return the problems."""
        units = self.units.units if self.units else []
        for path in self.units.unreferenced if self.units else []:
            print(f"evidence: removed, unreferenced on base: {path}")
        for site in self.units.readers if self.units else []:
            print(f"evidence: function annotations count, read at {site}")
        for site in self.units.docs if self.units else []:
            print(f"evidence: function docstrings count, read at {site}")
        if not units:
            return []
        unmeasured = [side for side in ("head", "base") if any(unit["side"] == side for unit in units)]
        unmeasured = [side for side in unmeasured if not self.measured[side]]
        if unmeasured:
            print(f"evidence: changed code objects: {len(units)}, not measured: no compared run reported which ran")
            return [f"changed code objects not measured: {len(units)}"]
        missing = {unit["index"]: self._missing(unit) for unit in units}
        unmeasurable = [
            unit for unit in units if any(probe["kind"] == "unmeasurable" for probe in missing[unit["index"]])
        ]
        tested = [
            unit
            for unit in units
            if unit["side"] == "head"
            and missing[unit["index"]]
            and unit not in unmeasurable
            and all(
                lines_of(probe["where"]) & (tests or {}).get(unit["path"], set()) for probe in missing[unit["index"]]
            )
        ]
        unrun = [unit for unit in units if missing[unit["index"]] and unit not in tested and unit not in unmeasurable]
        counts = {side: sum(unit["side"] == side for unit in units) for side in ("head", "base")}
        run = sum(not missing[unit["index"]] for unit in units)
        print(
            f"evidence: changed code objects: head {counts['head']}, base {counts['base']}; run by a compared cell "
            f"{run}/{len(units)}; on lines a test ran {len(tested)}; not run {len(unrun)}/{len(units)}; unmeasurable "
            f"{len(unmeasurable)}"
        )
        for label, found in (("on lines a test ran", tested), ("not run", unrun), ("unmeasurable", unmeasurable)):
            for unit in found:
                probes = "; ".join(f"{probe['kind']} {probe['where']}" for probe in missing[unit["index"]])
                print(f"evidence: {label}: {unit['side']} {unit['path']} {unit['qualname']}: {probes}")
        for unit in units:
            if unit["note"] == "run at import only":
                print(f"evidence: run at import only: {unit['side']} {unit['path']} {unit['qualname']}")
        not_run = len(tested) + len(unrun) + len(unmeasurable)
        return [f"changed code objects no compared cell ran: {not_run}"] if not_run else []

    def write(self, path: Path) -> None:
        """Write what each run proved, and whether a compared run ran each changed code object, as JSON.

        Each changed code object also lists the source spans of its probes that no compared run ran (`missing`).
        """

        def side(facts: Facts) -> dict[str, object]:
            code, files, warned, other = facts
            return {"exit": code, "files": dict(files), "warnings": warned, "facts": other}

        cells = [
            {"cell": name, "group": group, "compared": compared, "base": side(base), "candidate": side(candidate)}
            for name, (group, compared, base, candidate) in sorted(self.cells.items())
        ]
        units = [
            {key: unit[key] for key in ("side", "path", "qualname")}
            | {"ran": not (missing := self._missing(unit)), "missing": [probe["where"] for probe in missing]}
            for unit in (self.units.units if self.units else [])
        ]
        path.write_text(json.dumps({"cells": cells, "units": units}) + "\n", encoding="utf-8")


def add_evidence_args(parser: argparse.ArgumentParser) -> None:
    """Add `--test-coverage`, which tells changed code on lines a test ran, and `--evidence` (`Evidence.write`)."""
    parser.add_argument(
        "--test-coverage",
        type=Path,
        help="coverage.py data file of the test suite: changed code objects no compared run ran whose probes all lie "
        "on lines it ran are 'on lines a test ran' (line coverage: it does not tell which part of a line ran; default: "
        "'not run')",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="write what each run proved (exit status, a hash of each output file, of its warnings and of its other "
        "facts, per side) and whether a compared run ran each changed code object (the spans of its probes none ran) "
        "to this JSON file",
    )


def measured_key(units: Units | None, python: Path) -> str:
    """Return what keys a traced base's cached outcomes: its probes, the interpreter that measured, these scripts."""
    if units is None:
        return ""
    version = subprocess.run(
        [str(python), "-c", "import sys; print(sys.version)"], capture_output=True, text=True, check=True
    ).stdout
    scripts = b"".join(
        path.read_bytes() for path in (Path(__file__), CHANGE_UNITS, CHANGE_UNITS.with_name("_source_model.py"))
    )
    return _digest(units.key("base") + version + hashlib.sha256(scripts).hexdigest())


def _diff(base: dict[str, str], cand: dict[str, str], limit: int) -> str:
    lines: list[str] = []
    for key in sorted(set(base) | set(cand)):
        lines += difflib.unified_diff(
            base.get(key, "").splitlines(), cand.get(key, "").splitlines(), f"base/{key}", f"cand/{key}", lineterm=""
        )
    return "\n" + "\n".join(lines[:limit])


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    base = parser.add_mutually_exclusive_group(required=True)
    base.add_argument("--base", help="released version (0.85.0) or git revision of this repository to compare with")
    base.add_argument("--base-python", type=Path, help="interpreter with the base version already installed")
    parser.add_argument("--candidate-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--types", default=",".join(DEFAULT_TYPES), help="comma-separated --output-model-type values")
    parser.add_argument("--options", default="default", help="option sets separated by ';' ('default' = none)")
    parser.add_argument("--extra", default="", help="arguments added to every run, e.g. '--input-file-type openapi'")
    parser.add_argument("--import-check", action="store_true", help="also import every generated module")
    parser.add_argument("--show-diff", type=int, default=0, help="print up to N diff lines per differing run")
    parser.add_argument("-j", type=int, default=os.cpu_count() or 1, help="parallel runs (default: CPU count)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds before one generation or import fails")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
    parser.add_argument("--no-cache", action="store_true", help="regenerate base outputs instead of reusing them")
    add_evidence_args(parser)
    parser.add_argument("inputs", nargs="+", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the comparison."""
    ns = _parse_args(argv)
    _content_hash.cache_clear()
    _referenced_files.cache_clear()
    candidate_python = ns.candidate_python.absolute()
    resolved = (
        installed_base(ns.base_python.absolute())
        if ns.base_python is not None
        else resolve_base(ns.base, candidate_python, ns.cache_dir.absolute())
    )
    cache_root = None if ns.no_cache else ns.cache_dir / "outputs" / resolved.cache_name
    side = Side("base", resolved.python, resolved.package, "path")
    base = Runner(side, resolved.env(), import_check=ns.import_check, timeout=ns.timeout)
    side = Side("cand", candidate_python, ROOT / "src", "system")
    cand = Runner(side, run_environment(ROOT / "src"), import_check=ns.import_check, timeout=ns.timeout)
    units = changed_units(candidate_python, ROOT, resolved)
    if units is not None:
        base.probes, cand.probes = units.probes["base"], units.probes["head"]
    evidence = Evidence(units)
    cache = OutputCache(cache_root, measured_key(units, resolved.python))
    extra = shlex.split(ns.extra)
    runs = [
        (schema.absolute(), model_type, [] if opts == "default" else shlex.split(opts), opts)
        for schema in ns.inputs
        for model_type in ns.types.split(",")
        for opts in (o.strip() for o in ns.options.split(";"))
    ]
    counts = dict.fromkeys(("identical", "differ", "fail-both", "fail-base-only", "fail-candidate-only"), 0)

    def one(run: tuple[Path, str, list[str], str]) -> tuple[str, str, Outcome, Outcome]:
        schema, model_type, options, label = run
        args = ["--input", str(schema), "--output-model-type", model_type, *COMMON_ARGS, *extra, *options]
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            b = cache.get(schema, args, import_check=ns.import_check)
            if b is None:
                b = base.generate(args, tmp)
                cache.put(schema, args, b, import_check=ns.import_check)
            c = cand.generate(args, tmp)
        name = f"{schema.name} | {model_type} | {label}"
        if not b.ok and not c.ok:
            return "fail-both", f"{name}: {c.error}", b, c
        if refused := refused_on_head(b, c):
            return "fail-candidate-only", f"{name}: {refused}", b, c
        if not b.ok:
            return "fail-base-only", f"{name}: {b.error}", b, c
        note = ""
        if c.import_error and not b.import_error:
            note = f" [import fails on candidate only: {c.import_error}]"
        elif b.import_error and not c.import_error:
            note = f" [import fixed: {b.import_error}]"
        elif c.import_error:
            note = f" [import fails on both sides: {c.import_error}]"
        if b.tree == c.tree:
            if c.import_error and not b.import_error:
                return "fail-candidate-only", f"{name}:{note}", b, c
            return "identical", (f"{name}:{note}" if note else ""), b, c
        return "differ", f"{name}{note}{_diff(b.tree, c.tree, ns.show_diff) if ns.show_diff else ''}", b, c

    def count(run: tuple[Path, str, list[str], str], result: tuple[str, str, Outcome, Outcome]) -> None:
        (schema, model_type, _, label), (status, line, b, c) = run, result
        counts[status] += 1
        if line:
            print(f"{status}: {line}", flush=True)
        directory = (
            schema.parent.relative_to(ROOT) if schema.parent.is_relative_to(ROOT) else schema.parent
        ).as_posix()
        options = label.replace(f"{ROOT}{os.sep}", "")
        group = Evidence.group(schema, directory, model_type, options)
        evidence.add(group, f"{directory}/{schema.name} | {model_type} | {options}", b, c)

    started = time.monotonic()
    pool = ThreadPoolExecutor(ns.j)
    try:
        for run, result in zip(runs, pool.map(one, runs), strict=True):
            count(run, result)
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    finally:
        base.close()
        cand.close()
    print("summary:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"cached base outputs: {cache.hits} of {len(runs)}")
    incomplete = evidence.report(tested_lines(ns.test_coverage) if ns.test_coverage else None)
    if ns.evidence:
        evidence.write(ns.evidence)
    print(f"elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr)
    return 1 if counts["differ"] or counts["fail-candidate-only"] or incomplete else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        _worker()
    else:
        sys.exit(main())
