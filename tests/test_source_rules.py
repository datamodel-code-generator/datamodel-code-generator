"""Run each source rule over a small tree that holds one site it reports and one near miss it leaves alone."""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from scripts import check_source_rules
from scripts._ci_lanes import FORMATTER_ENV, WORKFLOW, WORKFLOWS, Lane, matrix_entries, read_lanes
from tests.capabilities import needs
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
                  - tox_env: pydantic20,py312-black22-parallel,py312-isort6-parallel,heki
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
        commands =
            pytest tests/main/test_payload_validation.py::test_numeric_payload_sampling

        [testenv:heki]
        base_python = python3.14
        commands =
            pytest tests/test_heki.py tests/test_combined.py::TestHeki::test_listed
        dependency_groups =
            test
            heki
    """,
    "pyproject.toml": """
        [project]
        dependencies = ["black>=19.10b0; python_version<'3.15'", "isort>=4.3.21,<9", "pydantic>=2,<3"]

        [dependency-groups]
        test = ["pytest", { include-group = "coverage" }]
        coverage = ["coverage", { include-group = "test" }]
        black22 = ["black==22.1; python_version<'3.15'"]
        isort6 = ["isort>=6,<7"]
        heki = ["heki==0.2.4"]
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
        name = "heki"
        version = "0.2.4"

        [[package]]
        name = "datamodel-code-generator"
        source = { editable = "." }
    """,
}
"""A CI matrix of six lanes: Python 3.12, 3.11 (Windows, external formatter), Pydantic 2.0, black 22, isort 6 and heki.

The package requires black, isort and Pydantic, so every lane has them; only the heki lane's group installs heki.
"""
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
                "python-3.11": (("python>=3.11",), "every lane runs Python 3.11 or newer: the skip never fires"),
                "empty": ((), "no clause holds everywhere"),
                "pydantic-2.1": (("pydantic>=2.1",), "only the pinned 2.0.3 subset lane lacks it"),
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
                "heki": (("heki>=0",), "the test runs heki, which only the heki lane installs"),
            }
        """,
        "tests/test_heki.py": """
            import pytest

            from tests.capabilities import needs

            @needs("heki")
            def test_in_the_listed_module():
                pass

            @pytest.mark.parametrize("value", [pytest.param(1, marks=needs("heki")), 2])
            def test_param_in_the_listed_module(value):
                pass
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

            CASES = [pytest.param(1, marks=needs("windows")), pytest.param(2)]

            @needs("python-3.12")
            @pytest.mark.parametrize("value", CASES)
            def test_referenced_cases(value):
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

            class TestHeki:
                @needs("heki")
                def test_listed(self):
                    pass

                @needs("heki")
                def test_unlisted(self):
                    pass

            @pytest.mark.parametrize("value", [pytest.param(1, marks=needs("heki")), 2])
            def test_heki_param_of_an_unlisted_test(value):
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
    "posix-only-call": {
        "tests/capabilities.py": """
            CAPABILITIES = {
                "posix": (("platform!=win32",), "the test forks"),
                "linux": (("platform==linux",), "the test needs a Linux facility"),
                "formatter": (("formatter==builtin|platform!=win32",), "builtin, or any POSIX"),
                "python-3.12": (("python>=3.12",), "type statements"),
            }
        """,
        "scripts/worker.py": """
            import os
            import sys
            from signal import SIGKILL

            if sys.platform != "win32":
                import resource

            try:
                import fcntl
            except ImportError:
                fcntl = None


            def spawn():
                return os.fork()


            def run(argv):
                return spawn() if argv else 0


            def main(argv):
                return run(argv)


            def portable(argv):
                return len(argv)


            def stop(pid):
                os.kill(pid, SIGKILL)
        """,
        "scripts/eager.py": """
            import fcntl
            import os as operating_system

            LOCK = fcntl.LOCK_EX
            PIPE = operating_system.mkfifo


            class Holder:
                limit = __import__("resource")
        """,
        "tests/test_worker.py": """
            import os

            import pytest

            from scripts import worker
            from scripts.worker import main as entry
            from tests.capabilities import needs


            def test_main_forks():
                worker.main(["x"])


            def test_entry_forks():
                entry(["x"])


            @pytest.fixture
            def pipe(tmp_path):
                os.mkfifo(tmp_path / "pipe")


            def test_fixture(pipe):
                pass


            def test_kill_signal():
                worker.stop(1)


            def test_portable():
                worker.portable(["x"])


            @needs("posix")
            def test_marked():
                worker.main(["x"])


            @needs("formatter", "linux")
            def test_linux_only():
                worker.main(["x"])


            @needs("formatter")
            def test_formatter_is_no_platform():
                worker.main(["x"])


            @needs("python-3.12")
            def test_python_is_no_platform():
                os.fork()


            class TestMarks:
                pytestmark = needs("posix")

                def test_inherits(self):
                    os.fork()


            class TestPlain:
                def test_forks(self):
                    os.fork()
        """,
        "scripts/limits.py": """
            import importlib
            import sys

            if sys.platform != "win32":
                import resource

            try:
                import fcntl
            except ImportError:
                fcntl = None

            memory = None if sys.platform == "win32" else importlib.import_module("resource")
            lock_module = sys.platform != "win32" and importlib.import_module("fcntl")
            later = importlib.import_module("fcntl") if sys.argv else None


            def peak():
                return resource.getrusage(resource.RUSAGE_SELF)


            def lock(handle):
                fcntl.flock(handle, fcntl.LOCK_EX)


            def lock_if_present(handle):
                if fcntl is not None:
                    fcntl.flock(handle, fcntl.LOCK_EX)
        """,
        "tests/conftest.py": """
            import os

            import pytest


            @pytest.fixture
            def fifo(tmp_path):
                os.mkfifo(tmp_path / "fifo")


            @pytest.fixture
            def through(fifo):
                pass
        """,
        "tests/sub/conftest.py": """
            import os

            import pytest


            @pytest.fixture(autouse=True)
            def everywhere(tmp_path):
                os.mkfifo(tmp_path / "everywhere")
        """,
        "tests/sub/test_everywhere.py": """
            from tests.capabilities import needs


            def test_autouse_of_the_folder():
                pass


            @needs("posix")
            def test_marked():
                pass
        """,
        "tests/test_reach.py": """
            import os
            from unittest import TestCase

            import pytest

            from scripts import limits
            from tests.capabilities import needs


            def test_guarded_import_is_no_guard_of_its_readers():
                limits.peak()


            def test_handler_is_no_guard_of_its_readers(tmp_path):
                limits.lock(tmp_path)


            def test_read_under_a_test_of_the_name(tmp_path):
                limits.lock_if_present(tmp_path)


            def test_conftest_fixture(fifo):
                pass


            def test_conftest_fixture_through_another(through):
                pass


            @pytest.mark.usefixtures("fifo")
            def test_usefixtures():
                pass


            @needs("posix")
            @pytest.mark.usefixtures("fifo")
            def test_usefixtures_marked():
                pass


            @pytest.fixture
            def local_fifo(tmp_path):
                os.mkfifo(tmp_path / "local")


            @pytest.mark.usefixtures("local_fifo")
            def test_usefixtures_of_the_module():
                pass


            class TestSelf:
                def _spawn(self):
                    return os.fork()

                def _portable(self):
                    return 1

                def test_self_method(self):
                    self._spawn()

                def test_portable_self_method(self):
                    self._portable()


            class TestAutouse:
                @pytest.fixture(autouse=True)
                def _pipe(self, tmp_path):
                    os.mkfifo(tmp_path / "pipe")

                def test_after_the_autouse_fixture(self):
                    pass


            class TestShadow(TestSelf):
                pass


            class TestShadow(TestShadow):
                def test_inherited_spawn_through_a_rebound_name(self):
                    self._spawn()


            class TestUnittest(TestCase):
                def test_base_of_another_module(self):
                    self.assertTrue(True)


            @pytest.fixture
            def base():
                return 1


            @pytest.fixture
            def left(base):
                return base


            @pytest.fixture
            def right(base):
                return base


            def test_two_fixtures_share_a_portable_one(left, right):
                pass
        """,
        "scripts/optional.py": """
            import importlib
            import sys

            fcntl = importlib.import_module("fcntl") if sys.platform != "win32" else None
            other = None


            def lock(handle):
                fcntl.flock(handle, fcntl.LOCK_EX)


            def free():
                return other
        """,
        "tests/test_round_two.py": """
            import os
            import sys
            from typing import TYPE_CHECKING

            import pytest

            from scripts import optional, worker

            if TYPE_CHECKING:
                import fcntl


            def test_typing_only_import(handle: "fcntl.X"):
                optional.free()


            def test_guarded_helper_call():
                if sys.platform != "win32":
                    worker.spawn()


            def test_optional_module_read(handle):
                optional.lock(handle)


            @pytest.fixture
            def test_provider():
                return os.fork()
        """,
        "tests/override/conftest.py": """
            import pytest


            @pytest.fixture(autouse=True)
            def setup_env():
                yield
        """,
        "tests/override/test_override.py": """
            import os

            import pytest


            @pytest.fixture
            def setup_env():
                os.fork()


            def test_overridden_autouse_fixture_runs():
                pass
        """,
    },
    "portable-names": {
        "scripts/names.py": """
            import os.path
            from pathlib import Path


            def build(root, name):
                fine = root / "ok.txt"
                colon = root / "has:colon.txt"
                device = root / "con.txt"
                question = Path("a", "b?.json")
                trailing = os.path.join("x", "trail.")
                reserved = root.joinpath("sub", "AUX")
                fragment = root / f"{name}:x.toml"
                template = root / f"{name}.toml"
                drive = Path("C:/windows/x")
                pipe = open("name|pipe")
                pattern = root.glob("*.py")
                beside = "has:colon.txt"
        """,
        "tests/test_names.py": """
            from pathlib import Path

            import pytest

            CASES = {"ok": {"fine": "x", "bad:name": "y"}, "other": {"quote\\"": "z"}}
            UNUSED = {"not:passed": 1}


            def write(root, files):
                for name, text in files.items():
                    (root / f"{name}.toml").write_text(text)


            def count(files):
                for key in files:
                    print(key)


            def test_run(tmp_path: Path):
                write(tmp_path, CASES["ok"])
                write(tmp_path, files={"also:bad": "x", "fine": "y"})
                count({"not:a:file": 1})


            KEY = "var:key"
            FILES = [("list:bad.json", "{}"), ("fine.json", "{}")]
            NAME = "const:bad.json"


            def test_names_through_variables(tmp_path: Path):
                local = "local:bad.json"
                (tmp_path / local).write_text("{}")
                (tmp_path / NAME).write_text("{}")
                for entry, text in FILES:
                    (tmp_path / entry).write_text(text)
                mapping = {"item:bad.json": "{}", "fine.json": "{}"}
                for key, text in mapping.items():
                    (tmp_path / key).write_text(text)
                write(tmp_path, {KEY: "x"})
                write(tmp_path, mapping)


            @pytest.mark.parametrize("name", ["param:bad.json", "nul.json", "fine.json"])
            def test_parametrized_name(tmp_path: Path, name: str):
                (tmp_path / name).write_text("{}")


            @pytest.mark.parametrize(("name", "text"), [pytest.param("pair:bad.json", "x"), ("fine.json", "y")])
            def test_parametrized_pairs(tmp_path: Path, name: str, text: str):
                (tmp_path / name).write_text(text)


            def test_unknown_name(tmp_path: Path, anything: str):
                (tmp_path / anything).write_text("{}")


            CASE = ("tuple:bad.json", "x")
            ARGNAMES = ("name", "text")


            @pytest.mark.slow
            @pytest.mark.timeout(5)
            @pytest.mark.parametrize(["name", "text"], [CASE, ("list:bad.json", "y")])
            def test_decorators_and_a_name_as_the_item(tmp_path: Path, name: str, text: str, other: str):
                (tmp_path / name).write_text(text)
                (tmp_path / other).write_text(text)


            @pytest.mark.parametrize(ARGNAMES, [("named:bad.json", "z")])
            def test_names_of_a_name(tmp_path: Path, name: str, text: str):
                (tmp_path / name).write_text(text)


            def test_other_ways_to_name_a_file(tmp_path: Path, flag: bool):
                (tmp_path / ("cond:bad.json" if flag else "fine.json")).write_text("{}")
                mapping = {"key:bad.json": "{}"}
                for key in mapping:
                    (tmp_path / key).write_text("{}")
                for key in sorted(mapping):
                    (tmp_path / key).write_text("{}")
                with open(tmp_path / "log.txt", "w") as handle:
                    (tmp_path / handle.name).write_text("{}")
                    (tmp_path / handle).write_text("{}")
                for first, second, third in [("one:bad.json", "ok.json")]:
                    (tmp_path / third).write_text("{}")
                for left, right in ["lonely:bad.json"]:
                    (tmp_path / right).write_text("{}")
                for name, text in fetch().items():
                    (tmp_path / name).write_text(text)


            def write_with(root, files, /, options, *, mode="w"):
                for name, text in files.items():
                    (root / name).write_text(text)
                return options, mode


            def test_the_argument_that_names_files(tmp_path: Path, rows: list):
                write_with(tmp_path, {"slot:bad.json": "{}"}, {"option:fine": 1})
                write_with(tmp_path, {"fine.json": "{}"}, options={"keyword:fine": 1}, mode="a")
                write_with(*rows)
                with open(file=tmp_path / "file:bad.txt", mode="w"):
                    pass
                with open(*rows):
                    pass
                name, text = ("pair:bad.json", "{}")
                (tmp_path / name).write_text(text)
                whole, = fetch()
                (tmp_path / whole).write_text("{}")
                names = ["rebound.json"]
                for names in names:
                    (tmp_path / names).write_text("{}")
                pairs = {"cycle:fine": "{}"}
                for pairs in pairs.items():
                    (tmp_path / pairs).write_text("{}")
                left = [right]
                right = [left]
                for left in right:
                    (tmp_path / left).write_text("{}")
                for *_, last in [("ok", "skip:fine", "star:bad.json")]:
                    (tmp_path / last).write_text("{}")
                (tmp_path / "COM\u00b9.txt").write_text("{}")


            @pytest.mark.parametrize("name", [pytest.param("single:bad.json", id="one"), "fine.json"])
            def test_one_name_in_a_param(tmp_path: Path, name: str):
                (tmp_path / name).write_text("{}")


            @pytest.mark.parametrize(["name"], [("listed:bad.json",)])
            def test_one_name_in_a_list(tmp_path: Path, name: str):
                (tmp_path / name).write_text("{}")
        """,
        "tests/test_writers.py": """
            class Writer:
                def write(self, files):
                    for name in files:
                        (self.root / name).write_text("")


            def nested(root):
                def inner(files):
                    for name in files:
                        (root / name).write_text("")

                return inner


            def test_method_writers(tmp_path):
                Writer().write({"method:fine": 1})
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


SHARD_AXES = frozenset({"shard", "shard_total"})
"""The matrix keys of the workflows that number a shard of one lane's tests."""


def _beyond_include(jobs: dict[str, dict[str, dict[str, dict[str, object]]]]) -> list[str]:
    """Return ``job: key`` for every key but ``include``, a lone ``tox_env`` axis and the shard axes of a tox matrix.

    Neither reader expands other axes, several axes or excludes; a matrix of no tox environment holds no lane. The shard
    axes (``SHARD_AXES``) split the tests of each lane of the ``tox_env`` axis and change no lane, so they are skipped.
    """
    matrices = {name: job.get("strategy", {}).get("matrix", {}) for name, job in jobs.items()}
    return [
        f"{name}: {key}"
        for name, matrix in matrices.items()
        if "tox_env" in matrix or any("tox_env" in entry for entry in matrix.get("include", []))
        for key in matrix
        if key not in {"include", *SHARD_AXES} and (key != "tox_env" or len(set(matrix) - SHARD_AXES) > 1)
    ]


def test_a_matrix_key_but_include_is_found() -> None:
    """A plain axis or an ``exclude`` would drop lanes silently from both readers, so it is reported.

    The shard axes only split the tests of each lane, so they are not.
    """
    text = """
jobs:
  axes:
    strategy:
      matrix:
        os: [windows-latest, macos-latest]
        tox_env: [py312-parallel]
  sharded:
    strategy:
      matrix:
        tox_env: [py312-parallel]
        shard: [1, 2]
        shard_total: [2]
  mixed:
    strategy:
      matrix:
        include:
          - tox_env: py312-parallel
        exclude:
          - tox_env: py311-parallel
  plain:
    runs-on: ubuntu-latest
"""
    assert_output(f"{_beyond_include(yaml.load(text, Loader=yaml.BaseLoader)['jobs'])}\n")


def test_a_job_that_lists_test_paths_selects_them() -> None:
    """A job whose steps list test paths one per line runs a fixed list: its entries are ``listed``, others ``all``."""
    text = """
jobs:
  subset:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - tox_env: py312-black22-parallel
    steps:
    - run: |
        tox run -e "$TOX_ENV" -- \\
          tests/test_format.py \\
          tests/main/openapi
  suite:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        include:
          - tox_env: py312-parallel
            exclude_tests: tests/main/test_payload_validation.py
"""
    assert_output("".join(f"{entry['job']}: {entry['selection']}\n" for entry in matrix_entries(text)))


def test_joined_entries_keep_their_own_exclusions() -> None:
    """A node one entry leaves out is not run because another entry selects a path that covers it.

    A node runs where some test at or below it runs: leaving out one parameter still runs the test's others, and
    selecting one parameter runs its test and module, not the module's other tests, unless the entry also leaves
    that parameter out.
    """
    first = Lane("lane", {}, ("tests/a.py",), ("tests/a.py::test_x", "tests/a.py::test_w[one]"))
    second = Lane("lane", {}, ("tests/b.py", "tests/a.py::test_y[one]", "tests/d.py::test_v[one]"), ())
    third = Lane("lane", {}, ("tests/e.py::test_t[one]", "tests/e.py::test_s[one]"), ("tests/e.py::test_t[one]",))
    lane = first.joined(second).joined(third)
    nodes = [
        "tests/a.py::test_x",
        "tests/a.py::test_z",
        "tests/b.py::test_x",
        "tests/a.py::test_y[one]",
        "tests/c.py",
        "tests/a.py::test_w",
        "tests/a.py::test_w[one]",
        "tests/d.py::test_v",
        "tests/d.py",
        "tests/d.py::test_u",
        "tests/e.py::test_t",
        "tests/e.py::test_s",
        "tests/e.py",
    ]

    assert_output("".join(f"{node}: {lane.runs(node)}\n" for node in nodes))


def test_the_line_reader_reads_the_matrix_a_yaml_parser_reads() -> None:
    """The matrix entries of every workflow read line by line equal those of a YAML parse, runner included.

    Neither reader expands other matrix axes or excludes, so no matrix has a key but ``include``, a lone ``tox_env``
    axis and the shard axes.
    """
    workflows = sorted((Path(__file__).parents[1] / WORKFLOWS).glob("*.y*ml"))
    texts = {workflow.name: workflow.read_text(encoding="utf-8") for workflow in workflows}
    every = {f"{workflow}:{name}": job for workflow, text in texts.items() for name, job in _jobs(text).items()}
    matrices = {name: job.get("strategy", {}).get("matrix", {}) for name, job in every.items()}
    parsed = sorted(
        (name, entry["tox_env"], entry.get("os", every[name]["runs-on"]))
        for name, matrix in matrices.items()
        for entry in [*matrix.get("include", []), *({"tox_env": env} for env in matrix.get("tox_env", []))]
    )
    read = sorted(
        (f"{workflow}:{entry['job']}", entry["tox_env"], entry.get("os", entry["runs-on"]))
        for workflow, text in texts.items()
        for entry in matrix_entries(text)
    )

    same_variable = FORMATTER_ENV == TEST_DEFAULT_FORMATTER_ENV

    assert_output(
        f"missed by the line reader: {sorted(set(parsed) - set(read))}\n"
        f"added by the line reader: {sorted(set(read) - set(parsed))}\n"
        f"entries equal: {parsed == read}\n"
        f"matrix keys but include: {_beyond_include(every)}\n"
        f"formatter variable equal to the suite's: {same_variable}\n"
    )


def _jobs(text: str) -> dict[str, dict[str, dict[str, dict[str, object]]]]:
    """Return the jobs of a workflow as a YAML parser reads them."""
    return yaml.load(text, Loader=yaml.BaseLoader).get("jobs", {})


def test_a_full_suite_command_with_the_tox_default_target_is_no_subset_lane(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``pytest {posargs:tests}`` runs the whole suite, so the lane lacking a capability is no subset lane."""
    files = {
        **LANES,
        "tox.ini": LANES["tox.ini"].replace(
            "pytest tests/main/test_payload_validation.py::test_numeric_payload_sampling", "pytest {posargs:tests}"
        ),
    }
    test = 'from tests.capabilities import needs\n\n\n@needs("pydantic-2.1")\ndef test_it():\n    pass\n'
    _write(tmp_path, {**files, "tests/test_it.py": test})
    (tmp_path / "tests/capabilities.py").write_text(
        'CAPABILITIES = {"pydantic-2.1": (("pydantic>=2.1",), "the pinned lane lacks it")}\n', encoding="utf-8"
    )

    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", "dead-capability", "--rule", "never-run"))


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


FLOOR_TREE = {
    "pyproject.toml": """
        [project]
        requires-python = ">=3.11"
    """,
    "tests/test_ok.py": """
        def test_old_grammar(value):
            match value:
                case [first, *rest]:
                    return first, rest
            try:
                pass
            except* ValueError:
                pass
    """,
    "tests/data/newer.py": """
        type Alias = int


        def generic[T](value: T) -> T:
            return value
    """,
    "tests/data/garbage.py": "def broken(:\n",
    "tests/data/expected/stored.py": "type Alias = int\n",
}


@pytest.mark.parametrize("requires", ['">=3.11"', '">=3.12"', '">=3.14"'], ids=["3.11", "3.12", "3.14"])
@needs("type-statements")
def test_floor_syntax_follows_requires_python(
    requires: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``floor-syntax`` parses with the oldest Python of ``requires-python``: newer syntax is a site, older is not.

    A stored ``.py`` file and one this Python cannot parse hold no finding (so a file of the newest syntax is a site
    only on a Python that reads it: `floor_syntax_newest_forms` of the source forms states that one).
    """
    _write(tmp_path, {**FLOOR_TREE, "pyproject.toml": f"[project]\nrequires-python = {requires}\n"})
    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", "floor-syntax"))


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("plain.txt", id="plain"),
        pytest.param("has space.txt", id="space"),
        pytest.param("has:colon.txt", id="colon"),
        pytest.param('quote".txt', id="quote"),
        pytest.param("star*.txt", id="star"),
        pytest.param("what?.txt", id="question"),
        pytest.param("pipe|.txt", id="pipe"),
        pytest.param("less<.txt", id="less"),
        pytest.param("back\\\\slash.txt", id="backslash"),
        pytest.param("control\\x01.txt", id="control"),
        pytest.param("trailing.", id="trailing-dot"),
        pytest.param("trailing ", id="trailing-space"),
        pytest.param("con", id="device"),
        pytest.param("CON.txt", id="device-extension"),
        pytest.param("nul.tar.gz", id="device-double-extension"),
        pytest.param("aux.", id="device-dot"),
        pytest.param("com1.py", id="device-com1"),
        pytest.param("lpt9", id="device-lpt9"),
        pytest.param("com0.py", id="not-a-device-com0"),
        pytest.param("console.py", id="not-a-device-console"),
        pytest.param("a.con", id="not-a-device-suffix"),
        pytest.param("[param-id].txt", id="brackets"),
    ],
)
def test_unportable_name(name: str) -> None:
    """Windows refuses these characters, a trailing dot or space and the names of devices, with or without extension."""
    assert_output(f"{check_source_rules.unportable_name(name)}\n")


@pytest.mark.parametrize(
    "fragment",
    [
        pytest.param("", id="empty"),
        pytest.param(".toml", id="extension"),
        pytest.param(":x", id="colon"),
        pytest.param("con", id="device"),
        pytest.param("trailing.", id="trailing-dot"),
        pytest.param("a?b", id="question"),
    ],
)
def test_unportable_fragment(fragment: str) -> None:
    """A fragment of an f-string is checked for characters only: its neighbours may complete the name."""
    assert_output(f"{check_source_rules.unportable_name(fragment, whole=False)}\n")


def test_paths_that_fold_onto_each_other() -> None:
    """Two paths that differ in case or in Unicode composition fold onto the first; a folder counts like a file."""
    paths = [
        "tests/a/File.txt",
        "tests/a/file.txt",
        "tests/A/other.txt",
        "tests/b/café.txt",
        "tests/b/café.txt",
        "tests/c/Ünï.txt",
        "tests/c/ünï.txt",
        "tests/d/same.txt",
        "scripts/has:colon.py",
        "scripts/con.py",
        "scripts/fine.py",
    ]
    assert_output("\n".join(f"{path}: {reason}" for path, reason in check_source_rules.unportable_paths(paths)) + "\n")


@needs("linux")
def test_the_file_names_of_a_tree_are_checked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``portable-names`` reads every file below ``scripts`` and ``tests``, stored output and data included.

    A tree like this one cannot be written where the names are refused, or where case folds (Windows, macOS): it is
    written on Linux only.
    """
    files = ["tests/data/expected/has:colon.txt", "tests/data/Same.txt", "tests/data/same.txt", "scripts/aux.py"]
    _write(tmp_path, dict.fromkeys(["tests/data/expected/fine.txt", "scripts/fine.py", *files], ""))

    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", "portable-names"))


@needs("type-statements")
@pytest.mark.parametrize("pyproject", [None, "[project]\nname = 'x'\n"], ids=["no-pyproject", "no-requires-python"])
def test_floor_syntax_without_requires_python_uses_this_checkouts(
    pyproject: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tree that names no oldest Python is read with the one of the checkout the script belongs to (3.11)."""
    files = {**FLOOR_TREE, **({"pyproject.toml": pyproject} if pyproject else {})}
    _write(tmp_path, {name: text for name, text in files.items() if pyproject or name != "pyproject.toml"})

    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", "floor-syntax"))


def test_an_unreadable_capability_table_excludes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``posix-only-call`` counts a test whose capability the table cannot state: nothing is known to exclude it."""
    _write(
        tmp_path,
        {
            "tests/capabilities.py": 'CAPABILITIES = ["posix"]\n',
            "tests/test_fork.py": (
                "import os\n\nfrom tests.capabilities import needs\n\n\n"
                '@needs("posix")\ndef test_fork():\n    os.fork()\n'
            ),
        },
    )

    assert_output(_run(capsys, "--root", str(tmp_path), "--rule", "posix-only-call"))
