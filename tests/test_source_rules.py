"""Run each source rule over a small tree that holds one site it reports and one near miss it leaves alone."""

from __future__ import annotations

import subprocess
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from scripts import check_source_rules
from tests.conftest import assert_output

if TYPE_CHECKING:
    from pathlib import Path

PACKAGE = "src/datamodel_code_generator"
GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
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
