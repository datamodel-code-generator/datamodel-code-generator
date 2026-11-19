"""Run each source rule over a small tree that holds one site it reports and one near miss it leaves alone."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from scripts import check_source_rules
from scripts._ci_lanes import FORMATTER_ENV, WORKFLOW, matrix_entries, read_lanes
from tests.conftest import TEST_DEFAULT_FORMATTER_ENV, assert_output

PACKAGE = "src/datamodel_code_generator"
GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
LANES: dict[str, str] = {
    ".github/workflows/test.yaml": """
        name: Test
        jobs:
          test-shard:
            strategy:
              matrix:
                include:
                  - tox_env: py312-parallel
                    os: ubuntu-24.04
                    shard: 1
                  - tox_env: py312-parallel
                    os: ubuntu-24.04
                    shard: 2
                  - tox_env: "py311-nocov-parallel"  # Windows
                    os: windows-latest
            runs-on: ${{ matrix.os }}
          test-coverage:
            strategy:
              matrix:
                include:
                  - tox_env: pydantic20,py312-black22-parallel,py312-isort6-parallel
                    name: pinned
            runs-on: ubuntu-24.04
          check:
            runs-on: ubuntu-latest
            steps:
              - name: no matrix
    """,
    "tox.ini": """
        [tox]
        env_list = py312-parallel

        [testenv]
        set_env =
            COVERAGE_FILE = .coverage
        dependency_groups =
            test
            black22: black22
            isort6: isort6

        [testenv:py{311,312}{,-nocov}-parallel]
        set_env =
            {[testenv]set_env}
            !nocov: DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER = builtin

        [testenv:py312-{black22,black24,isort6}-parallel]

        [testenv:pydantic{20,25}]
        base_python =
            pydantic20: python3.11
            pydantic25: python3.12
        deps =
            pydantic20: pydantic==2.0.3
    """,
    "pyproject.toml": """
        [dependency-groups]
        test = ["pytest", { include-group = "coverage" }]
        coverage = ["coverage", { include-group = "test" }]
        black22 = ["black==22.1; python_version<'3.15'"]
        isort6 = ["isort>=6,<7"]
    """,
    "uv.lock": """
        version = 1

        [[package]]
        name = "black"
        version = "22.1.0"

        [[package]]
        name = "black"
        version = "26.10.1"

        [[package]]
        name = "isort"
        version = "6.1.0"

        [[package]]
        name = "isort"
        version = "7.0.0"

        [[package]]
        name = "pydantic"
        version = "2.14.1"

        [[package]]
        name = "datamodel-code-generator"
        source = { editable = "." }
    """,
}
"""A CI matrix of five lanes: Python 3.12, 3.11 (Windows, external formatter), Pydantic 2.0, black 22 and isort 6."""
TREES: dict[str, dict[str, str]] = {
    "hash-split": {
        f"{PACKAGE}/_json_pointer.py": """
            def document_part(key):
                return key.partition("#")[0]
        """,
        f"{PACKAGE}/reader.py": """
            def document(ref):
                return ref.rsplit("#", 1)[0]

            def keyword(ref):
                return ref.split(sep="#", maxsplit=1)[0], ref.rsplit(maxsplit=1, sep="#")

            def unbound(ref):
                return str.split(ref, "#")[0], str.partition(ref, "#")

            def path_parts(ref):
                return ref.startswith("#"), ref.split("/"), ref.split(sep="/"), str.split(ref, "/"), ref.find("/")
        """,
    },
    "doc-citations": {
        f"{PACKAGE}/__init__.py": "",
        f"{PACKAGE}/parser/base.py": """
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                from collections.abc import Sequence

            LIMIT: int = 3

            def build():
                pass

            class Parser:
                kind = "json"

                def parse(self):
                    self.results, (self.extra, *self.rest) = [], (0, 1)
                    [self.first, self.second] = 1, 2
                    self.counter: int = 0
                    for self.cursor in range(3):
                        pass
                    with open("x") as self.handle:
                        pass
        """,
        f"{PACKAGE}/model/pydantic_v2/base_model.py": """
            class DataModel:
                pass
        """,
        "docs/architecture.md": """
            # Architecture

            Resolved: `parser/base.py:build`, `parser/base.py:Parser.parse`, `base.py:LIMIT`, `Parser.results`,
            `Parser.rest`, `Parser.extra`, `Parser.second`, `Parser.cursor`, `Parser.handle`,
            `pydantic_v2/base_model.py:DataModel`, `parser/base.py:Sequence`, `parser/base.py:build.inner`,
            `parser/base.py:Parser.counter`, `Parser.kind`.
            Not checked: `scripts/build.py:main`, `tests/test_a.py:test_b`, `Enum.value`, `parser/base.py:has_`.
            Broken: `parser/base.py:gone`, `parser/base.py:Parser.gone`, `Parser.missing`, `gone.py:build`.
        """,
    },
    "flaky-test-workarounds": {
        "tests/test_retries.py": """
            import flaky
            import pytest
            import pytest_rerunfailures
            from flaky import flaky as retry
            from pytest_rerunfailures import gather

            pytestmark = pytest.mark.flaky(reruns=3)

            @flaky(max_runs=3)
            def test_marked():
                pytest.main(["--reruns", "2", "--reruns-delay=1", "-p", "rerunfailures", "--only-rerun=OSError"])
                return ["--reruns 5", "-p rerunfailures", "-prerunfailures"]
        """,
        "tests/test_near_miss.py": """
            import asyncio
            import time as clock
            from time import monotonic

            NOTE = "--reruns is not passed here"
            OPTIONS = ["--lf", "--maxfail=1", "reruns", "flaky_data"]

            def test_waits(backend):
                backend.sleep(1)
                clock.monotonic()
                monotonic()
                asyncio.sleep(0)

            def sleep(seconds):
                return seconds

            def test_local():
                sleep(1)
        """,
        "tests/test_names.py": """
            import pytest
            from pytest import mark as marks

            def test_not_markers(flaky, other):
                flaky(max_runs=3)
                other.flaky
                other.mark.flaky
                pytest.mark.slow
                marks.slow

            def test_local_name():
                flaky = 1
                return flaky

            def test_bound_later():
                from flaky import flaky as retry
                from pytest import mark as pm
                retry()
                pm.flaky
                marks.flaky
        """,
        "tests/test_shadowed.py": """
            import time
            from time import sleep

            def test_parameter(sleep, time):
                sleep(1)
                time.sleep(1)

            def test_assigned():
                sleep = print
                sleep(1)

            def test_defined():
                def sleep(seconds):
                    return seconds
                sleep(1)

            def test_nested(sleep):
                def inner():
                    sleep(1)
                return lambda: sleep(1)

            def test_call_root(build):
                build().sleep(1)

            def test_not_shadowed(other):
                sleep(1)
                time.sleep(1)

            def test_local_import():
                from time import sleep as nap
                import time as clock
                nap(1)
                clock.sleep(1)
        """,
        "tests/test_sleeps.py": """
            import time
            import time as clock
            from time import sleep, sleep as nap

            def test_sleeps():
                time.sleep(1)
                clock.sleep(1)
                sleep(1)
                nap(1)
        """,
        "tests/test_listed.py": """
            import time

            def test_listed():
                time.sleep(1)
                time.sleep(1)
        """,
        "tests/test_unlisted.py": "",
        "tests/data/expected/source_rules/sleeps.py": """
            import time
            time.sleep(1)
        """,
        "scripts/test_sleep_allowlist.toml": """
            ["tests/test_sleeps.py"]
            count = 2
            reason = "two waits"

            ["tests/test_listed.py"]
            count = 2
            reason = "exact"

            ["tests/test_unlisted.py"]
            count = 1
            reason = "gone"
        """,
    },
    "ellipsis-statement": {
        f"{PACKAGE}/hosts.py": """
            from typing import Protocol

            class Host(Protocol):
                def stub(self) -> int: ...

                def documented(self) -> int:
                    \"\"\"Return the count.\"\"\"

            MISSING = ...
        """,
    },
    "private-reexport": {
        f"{PACKAGE}/home.py": """
            _x = _y = _z = _w = _q = public = __version__ = 1
        """,
        f"{PACKAGE}/reexports.py": """
            from .home import _x  # noqa: F401  # kept export
            from .home import (
                public as _alias,  # noqa: E501, F401
                _q,  # noqa
            )
            from .home import _z
            from .home import _w  # noqa: E501
            from .home import public  # noqa: F401
            from .home import __version__  # noqa: F401
            import os as _os  # NOQA:F401

            print(_z)
        """,
        f"{PACKAGE}/ignores.py": """
            from .home import _x  # ruff: ignore[unused-import]
            from .home import _y  # type: ignore  #ruff: ignore[E501, F401]
            from .home import _z  # ruff: ignore[import-private-name]
            from .home import _w  # see ruff: ignore[unused-import]
            # ruff: ignore[unused-import]

            # the export
            from .home import (
                _q,
                public as _public,
            )
            from .home import (  # noqa: F401
                _x as _first,
                _y as _second,
            )
            from .home import (
                _z as _closing,
            )  # noqa: F401
            from .home import (
                # ruff: ignore[F401]
                _w as _inner,
                _q as _after,
            )
            value = 1  # ruff: ignore[unused-import]
            from .home import _x as _next
            # ruff: ignore[unused-import]
        """,
        f"{PACKAGE}/ranges.py": """
            from .home import _x  # ruff: disable[unused-import]
            # ruff: disable[unused-import]

            from .home import _y
                # ruff: enable[unused-import]
            # ruff: enable[import-private-name]
            from .home import _z
            # ruff: enable[F401]
            from .home import _w as _spelled
            # ruff: enable[unused-import]
            from .home import _w

            if _w:
                # ruff: disable[F401]
                from .home import _q
            from .home import public as _public
            # ruff: disable[unused-import]
            from .home import __version__ as _version
        """,
        f"{PACKAGE}/lists.py": """
            from .home import _x, _y, _z, _w, _q
            # ruff: disable[F401, E501]
            from .home import _x as _first
            # ruff: enable[F401]
            from .home import _y as _second
            # ruff: enable[E501, F401]
            from .home import _z as _third
            # ruff: enable[F401, E501]
            from .home import _w as _fourth
        """,
        f"{PACKAGE}/used.py": """
            from .home import _x  # noqa: F401
            from .home import _y  # noqa: F401
            from .home import _z  # noqa: F401
            from .home import _w  # noqa: F401

            print(_x, [_y for _y in range(3)])

            def reads():
                from .home import _q  # noqa: F401

                return _q

            def shadows(_z):
                return _z

            def deferred():
                return _w
        """,
        f"{PACKAGE}/exempt.py": """
            \"\"\"A module whose unused imports ruff never reports.\"\"\"

            from .home import _x
            #ruff:noqa:E501,F401
        """,
        f"{PACKAGE}/flake8.py": """
            # flake8: noqa
            from .home import _x
        """,
        f"{PACKAGE}/other_rules.py": """
            # ruff: noqa: E402
            from .home import _x
            x = 1  # ruff: noqa
            from .home import _y
        """,
    },
    "globals-registry": {
        f"{PACKAGE}/lazy.py": """
            VALUE = 1

            def __getattr__(name):
                return globals()[name]

            def __dir__():
                return sorted(globals().keys())

            def legacy():
                return globals().get("VALUE")
        """,
        f"{PACKAGE}/registry.py": """
            ENTRIES = [value for value in globals().values() if isinstance(value, int)]
            NAMES = [*globals()]
            KEYS = {name for name in globals()}
            for name in globals():
                pass
        """,
        "scripts/decls.py": """
            TABLE = tuple(globals ().items())
        """,
    },
    "unused-private-global": {
        f"{PACKAGE}/__init__.py": "",
        f"{PACKAGE}/constants.py": """
            _UNUSED = 1
            _READ = 2
            _ELSEWHERE = 3
            _TYPED: int = 4
            __version__ = "1"
            PUBLIC = _READ, _FIRST, _KEPT

            def shadow(_SHADOWED):
                _LOCAL = 1
                return _SHADOWED, _LOCAL, [_TARGET for _TARGET in range(3)]

            def bindings(data):
                import os as _IMP

                try:
                    pass
                except ValueError as _ERR:
                    print(_ERR)
                match data:
                    case {"key": _KEY, **_EXTRA}:
                        print(_KEY, _EXTRA)
                    case [_HEAD, *_TAIL]:
                        print(_HEAD, _TAIL)
                    case _CAPTURED:
                        print(_CAPTURED)
                return _IMP

            def declared():
                global _DECLARED
                _DECLARED = 1
                return _DECLARED

            def comprehensions(rows):
                return {key: value for row in rows for key, value in row if _OUTER}, [_ITEM for _ITEM in rows]

            @_DECO
            def decorated(value: _ANN = _DEFAULT, *, other=_LDEF) -> _RET:
                return lambda item=_LDEF: item

            class Child(_BASE, metaclass=_META):
                pass

            class Holder:
                _KEPT = 1

                def read(self):
                    return _KEPT, _SECOND
            _SHARED = 5
            _IMPORTED = _ALIASED = _RELATIVE = _ATTRIBUTE = _CHAINED = _PATCHED = _PACKED = 6
            _STORED = _FOREIGN = _CALLED = 7
            _SHADOWED = _LOCAL = _TARGET = _KEPT = 10
            _FIRST, (_SECOND, *_REST) = 11, (12, 13)
            [_THIRD, _FOURTH] = 14, 15
            _IMP = _ERR = _KEY = _EXTRA = _HEAD = _TAIL = _CAPTURED = 16
            _DECLARED = _OUTER = _DECO = _ANN = _DEFAULT = _RET = _BASE = _META = _LDEF = _ITEM = 17
        """,
        f"{PACKAGE}/other.py": """
            from datamodel_code_generator.constants import _IMPORTED
            from . import constants as consts
            import datamodel_code_generator.constants
            import datamodel_code_generator.constants as aliased

            _SHARED = 8
            print(_SHARED, _IMPORTED, consts._ATTRIBUTE, datamodel_code_generator.constants._CHAINED, aliased._ALIASED)
            consts._STORED = 9
            print(datetime._FOREIGN, make()._CALLED, "plain words")
        """,
        f"{PACKAGE}/sub/__init__.py": """
            from ..constants import _RELATIVE
        """,
        "tests/test_constants.py": """
            from . import _UNUSED

            TARGET = "datamodel_code_generator.constants._PATCHED"
            LAZY = "datamodel_code_generator.constants:_PACKED"

            def test_elsewhere():
                assert "_ELSEWHERE", "other._SHARED"
        """,
        "tests/data/sample.py": """
            from datamodel_code_generator.constants import _TYPED
        """,
        "scripts/tool.py": """
            print("nothing private")
        """,
    },
    "redundant-local-import": {
        f"{PACKAGE}/__init__.py": """
            class Error(Exception):
                pass
        """,
        f"{PACKAGE}/app.py": """
            import sys
            from sys import version_info
            from typing import TYPE_CHECKING

            import datamodel_code_generator.sub.inner
            from datamodel_code_generator.helpers import helper

            if TYPE_CHECKING:
                from datamodel_code_generator.typing_only import Hint

            if sys.argv[1:] == ["--fast"]:
                from datamodel_code_generator.fast import run
            else:
                from datamodel_code_generator.slow import walk

            if sys.version_info >= (3, 12):
                from datamodel_code_generator.versioned import feature

            if version_info < (3, 12):
                from datamodel_code_generator.legacy import fallback

            def fast_paths():
                from datamodel_code_generator.fast import run
                from datamodel_code_generator.slow import walk

                return run, walk

            def versioned():
                from datamodel_code_generator.versioned import feature
                from datamodel_code_generator.legacy import fallback

                return feature, fallback

            def loaded():
                from datamodel_code_generator.helpers import helper
                from datamodel_code_generator import config
                from datamodel_code_generator import config, helpers

                return helper, config, helpers

            def deferred():
                from datamodel_code_generator.heavy import compute
                from datamodel_code_generator.typing_only import Hint
                from datamodel_code_generator import Error
                from datamodel_code_generator import config, heavy
                import json

                return compute, Hint, Error, config, heavy, json
        """,
        f"{PACKAGE}/helpers.py": """
            def helper():
                from . import config

                return config
        """,
        f"{PACKAGE}/config.py": "",
        f"{PACKAGE}/heavy.py": "",
        f"{PACKAGE}/fast.py": "",
        f"{PACKAGE}/slow.py": "",
        f"{PACKAGE}/versioned.py": "",
        f"{PACKAGE}/legacy.py": "",
        f"{PACKAGE}/typing_only.py": "",
        f"{PACKAGE}/sub/__init__.py": """
            from .. import config
        """,
        f"{PACKAGE}/sub/inner.py": """
            from . import sibling

            def read():
                from ..helpers import helper
                from . import sibling

                return helper, sibling
        """,
        f"{PACKAGE}/sub/sibling.py": """
            from . import inner

            def back():
                from . import inner

                return inner
        """,
    },
    "clone-bodies": {
        f"{PACKAGE}/first.py": """
            def total(items: list[int]) -> int:
                \"\"\"Add the items.\"\"\"
                result = 0
                for item in items:
                    result += item * 2
                return result

            def tiny(value):
                return value.name
        """,
        f"{PACKAGE}/second.py": """
            def summed(values):
                count = 0
                for value in values:
                    count += value * 2
                return count

            def keyed(values):
                return sorted(values, key=lambda value: value.name)

            def scaled(values):
                count = 0
                for value in values:
                    count += value * 3
                return count

            def tiny(other):
                return other.name
        """,
        f"{PACKAGE}/_types/stubs.py": """
            def summed(values):
                count = 0
                for value in values:
                    count += value * 2
                return count
        """,
        f"{PACKAGE}/_compiled_templates/render.py": """
            def summed(values):
                count = 0
                for value in values:
                    count += value * 2
                return count
        """,
        f"{PACKAGE}/fourth.py": """
            def quadruple(values):
                count = 0
                for value in values:
                    count += value * 4
                return count
        """,
        f"{PACKAGE}/third.py": """
            def ordered(items):
                return sorted(items, key=lambda item: item.name)

            def quadrupled(values):
                count = 0
                for value in values:
                    count += value * 4
                return count

            def again(values):
                count = 0
                for value in values:
                    count += value * 4
                return count

            def tripled(values):
                count = 0
                for value in values:
                    count += value * 5
                return count
        """,
    },
    "raw-skip": {
        "tests/test_skips.py": """
            import sys
            import unittest
            from unittest import skipIf

            import pytest
            from pytest import mark as marks
            from pytest import skip

            pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")
            SKIP_OLD = marks.skipif

            @pytest.mark.skip(reason="broken")
            def test_marked():
                pytest.skip("not here")
                pytest.importorskip("grpc_tools")

            @SKIP_OLD(sys.version_info < (3, 12), reason="type statements")
            def test_aliased():
                skip("aliased")

            class TestCase(unittest.TestCase):
                @unittest.skipUnless(sys.platform == "linux", "Linux only")
                @skipIf(True, "never")
                def test_method(self):
                    raise unittest.SkipTest("unittest")
        """,
        "tests/test_near_miss.py": """
            import pytest

            from tests.capabilities import needs

            REFERENCE = "pytest.mark.skipif"

            @needs("posix")
            def test_capability():
                pass

            def skip(reason):
                return reason

            def test_local(mark):
                skip("a local function")
                mark.skipif
                pytest.mark.xfail
        """,
        "tests/capabilities.py": """
            import pytest

            def needs(*names):
                return pytest.mark.skipif(not names, reason="the one home of skips")
        """,
        "tests/data/python/inputs.py": """
            import pytest

            pytestmark = pytest.mark.skip(reason="test data, not a test")
        """,
        f"{PACKAGE}/runner.py": """
            import pytest

            pytest.skip("the package holds no tests")
        """,
    },
    "dead-capability": {
        **LANES,
        "tests/capabilities.py": """
            CAPABILITIES = {
                "posix": (("platform!=win32",), "the test forks"),
                "builtin-or-black-24": (("formatter==builtin|black>=24",), "black before 24 formats differently"),
                "black-22": (("black==22",), "black 22 output"),
                "black-19": (("black==19",), "no lane installs black 19"),
                "windows-3.12": (("platform==win32", "python>=3.12"), "the Windows lane runs Python 3.11"),
                "pydantic-1": (("pydantic<2",), "the floor is Pydantic 2"),
                "malformed": (("platform>=win32", "node==20"), "not a version, not a fact"),
            }
        """,
    },
    "never-run": {
        **LANES,
        "tests/capabilities.py": """
            CAPABILITIES = {
                "posix": (("platform!=win32",), "the test forks"),
                "windows": (("platform==win32",), "the test reads the registry"),
                "python-3.12": (("python>=3.12",), "type statements"),
            }
        """,
        "tests/test_combined.py": """
            import pytest

            from tests.capabilities import needs
            from tests.capabilities import needs as requires

            WINDOWS = needs("windows")

            @needs("windows", "python-3.12")
            def test_windows_on_3_12():
                pass

            @WINDOWS
            @requires("python-3.12")
            def test_aliased():
                pass

            @needs("windows")
            @pytest.mark.parametrize(
                "value",
                [pytest.param(1, marks=needs("python-3.12")), pytest.param(2, marks=[needs("posix")]), 3],
            )
            def test_parameters(value):
                pass

            @needs("gpu")
            def test_unknown():
                pass

            @needs("windows")
            def test_windows():
                def test_inner():
                    pass

            @needs("posix", "python-3.12")
            def test_posix_on_3_12():
                pass

            def needs_nothing(needs):
                return needs("windows")

            @needs(*["windows"], "posix")
            def test_spread():
                pass

            LOOP = AGAIN
            AGAIN = LOOP

            @LOOP
            @pytest.mark.slow
            @pytest.mark.parametrize("value", [pytest.param(4, marks=pytest.mark.slow), pytest.param(5)])
            def test_names_bound_to_each_other(value):
                pass
        """,
        "tests/test_module_marks.py": """
            from tests.capabilities import needs

            pytestmark = [needs("posix")]

            @needs("windows")
            def test_windows_in_a_posix_module():
                pass

            class TestWindows:
                pytestmark = needs("windows")

                def test_method(self):
                    pass

            def test_plain():
                pass
        """,
        "tests/test_classes.py": """
            from tests.capabilities import needs

            @needs("windows")
            class TestDecorated:
                @needs("python-3.12")
                def test_method(self):
                    pass

                def test_windows(self):
                    pass
        """,
    },
}
LOST_BASE = {
    f"{PACKAGE}/__init__.py": "",
    f"{PACKAGE}/reference.py": """
        __all__ = ["Resolver", "helper"]

        class Resolver:
            def context(self):
                return self.context

            def kept(self):
                return 1

            def _private(self):
                return 2

            def __enter__(self):
                return self

        class Loader:
            pass

        def helper():
            return Resolver().kept()

        def removed():
            return 3
    """,
    f"{PACKAGE}/_hidden.py": """
        def internal():
            return 4

        def context():
            return 5
    """,
    f"{PACKAGE}/user.py": """
        from datamodel_code_generator._hidden import internal
        from datamodel_code_generator.reference import Loader, Resolver, helper, removed

        def use():
            resolver = Resolver()
            return resolver.context(), resolver.kept(), resolver._private(), helper(), removed(), internal(), Loader()
    """,
}
LOST_HEAD = {
    f"{PACKAGE}/reference.py": LOST_BASE[f"{PACKAGE}/reference.py"].replace(
        """
        def removed():
            return 3
""",
        "",
    ),
    f"{PACKAGE}/user.py": """
        from datamodel_code_generator.reference import Resolver, helper

        LAZY = "reference:Loader"

        def use():
            resolver = Resolver()
            return resolver.kept(), helper()
    """,
}


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(text).lstrip("\n"), encoding="utf-8")


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> str:
    status = check_source_rules.main(list(args))
    return f"exit: {status}\n{capsys.readouterr().out}"


@pytest.mark.parametrize("rule", list(TREES))
def test_rule_reports_its_site_and_not_the_near_miss(
    rule: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each rule reports the site its fixture holds, with its count and fix, and leaves the near miss alone."""
    _write(tmp_path, TREES[rule])

    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", rule))


def test_a_tree_without_the_architecture_page_cites_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``doc-citations`` reports nothing in a tree without the architecture page, and its citations once it is there."""
    page = "docs/architecture.md"
    _write(tmp_path, {rel: text for rel, text in TREES["doc-citations"].items() if rel != page})
    without_page = _run(capsys, "--root", str(tmp_path), "--rule", "doc-citations")
    _write(tmp_path, {page: TREES["doc-citations"][page]})

    assert_output(
        f"{without_page}\n--- with the page\n{_run(capsys, '--root', str(tmp_path), '--rule', 'doc-citations')}"
    )


def test_counts_are_printed_as_ratchet_keys(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--count`` prints one key per rule that ran, with the count of each file."""
    _write(tmp_path, {**TREES["hash-split"], **TREES["ellipsis-statement"], "docs/architecture.md": ""})

    assert_output(_run(capsys, "--root", str(tmp_path), "--count"))


def test_lost_last_caller_compares_with_the_base(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A public method the package stopped calling is reported against the base; without a base the rule waits."""
    _write(tmp_path, LOST_BASE)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "base"], cwd=tmp_path, check=True)
    _write(tmp_path, LOST_HEAD)

    with_base = _run(capsys, "--root", str(tmp_path), "--base", "HEAD", "--rule", "lost-last-caller")
    without_base = _run(capsys, "--root", str(tmp_path), "--rule", "lost-last-caller", "--rule", "ellipsis-statement")

    assert_output(f"{with_base}\n--- without --base\n{without_base}")


def test_lanes_are_read_from_the_matrix_and_tox(tmp_path: Path) -> None:
    """Each matrix entry runs its tox environments on its runner, shards once; facts come from tox, groups and the lock.

    An entry whose runner and environment name no platform and no Python is a problem, never a lane with guessed facts.
    """
    odd = """
          odd:
            runs-on: self-hosted
            strategy:
              matrix:
                include:
                  - tox_env: lint
    """
    _write(tmp_path, {**LANES, WORKFLOW: LANES[WORKFLOW].rstrip() + odd})
    lanes, problems = read_lanes(tmp_path)

    assert_output("".join(f"{lane.name}: {lane.facts}\n" for lane in lanes) + f"problems: {problems}\n")


def test_the_line_reader_reads_the_matrix_a_yaml_parser_reads() -> None:
    """The workflow's matrix entries read line by line equal those of a YAML parse, runner included."""
    root = Path(__file__).parents[1]
    text = (root / WORKFLOW).read_text(encoding="utf-8")
    jobs = yaml.load(text, Loader=yaml.BaseLoader)["jobs"]
    parsed = sorted(
        (name, entry["tox_env"], entry.get("os", job["runs-on"]))
        for name, job in jobs.items()
        for entry in job.get("strategy", {}).get("matrix", {}).get("include", [])
    )
    read = sorted((entry["job"], entry["tox_env"], entry.get("os", entry["runs-on"])) for entry in matrix_entries(text))
    same_variable = FORMATTER_ENV == TEST_DEFAULT_FORMATTER_ENV

    assert_output(
        f"missed by the line reader: {sorted(set(parsed) - set(read))}\n"
        f"added by the line reader: {sorted(set(read) - set(parsed))}\n"
        f"entries equal: {parsed == read}\n"
        f"formatter variable equal to the suite's: {same_variable}\n"
    )


@pytest.mark.parametrize(
    "table",
    [
        None,
        'CAPABILITIES = dict(posix=(("platform!=win32",), "the test forks"))\n',
        'CAPABILITIES = ["posix"]\n',
        'CAPABILITIES = {"posix": ("platform!=win32",)}\n',
    ],
    ids=["no-table", "not-a-literal", "not-a-dict", "entry-shape"],
)
def test_a_table_that_cannot_be_read_holds_no_capability(
    table: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a readable table every capability is unknown, so each test that needs one never runs.

    A table that is not a literal ``{name: ((clause, ...), reason)}`` is reported.
    """
    test = 'from tests.capabilities import needs\n\n\n@needs("posix")\ndef test_posix():\n    pass\n'
    _write(tmp_path, {**LANES, "tests/test_posix.py": test})
    if table is not None:
        (tmp_path / "tests/capabilities.py").write_text(table, encoding="utf-8")

    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", "dead-capability", "--rule", "never-run"))
