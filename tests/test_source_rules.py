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

            def path_parts(ref):
                return ref.startswith("#"), ref.split("/"), ref.find(sep="#")
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
                    self.results = []
                    self.counter: int = 0
        """,
        f"{PACKAGE}/model/pydantic_v2/base_model.py": """
            class DataModel:
                pass
        """,
        "docs/architecture.md": """
            # Architecture

            Resolved: `parser/base.py:build`, `parser/base.py:Parser.parse`, `base.py:LIMIT`, `Parser.results`,
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
            TABLE = tuple(globals().items())
        """,
    },
    "unused-private-global": {
        f"{PACKAGE}/constants.py": """
            _UNUSED = 1
            _READ = 2
            _ELSEWHERE = 3
            _TYPED: int = 4
            __version__ = "1"
            PUBLIC = _READ
        """,
        "tests/test_constants.py": """
            def test_elsewhere():
                assert "_ELSEWHERE"
        """,
        "tests/data/sample.py": """
            _TYPED = 4
        """,
    },
    "redundant-local-import": {
        f"{PACKAGE}/__init__.py": """
            class Error(Exception):
                pass
        """,
        f"{PACKAGE}/app.py": """
            from typing import TYPE_CHECKING

            import datamodel_code_generator.sub.inner
            from datamodel_code_generator.helpers import helper

            if TYPE_CHECKING:
                from datamodel_code_generator.typing_only import Hint

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
        """,
    },
    "pyright-outside-runtime": {
        f"{PACKAGE}/binding.py": """
            value = int("1")  # pyright: ignore[reportArgumentType]
            text = "# pyright: ignore"  # a string, not a comment
        """,
        f"{PACKAGE}/_runtime/client.py": """
            value = int("1")  # pyright: ignore[reportArgumentType]
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
