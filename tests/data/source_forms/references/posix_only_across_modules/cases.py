"""Tests of the script: which of them reach the fork."""

from scripts import posix_only_across_modules
from scripts.posix_only_across_modules import script
from scripts.posix_only_across_modules.script import main as entry

from tests.capabilities import needs


def test_through_the_module():
    script.main(["x"])


def test_through_a_from_import():
    entry(["x"])


def test_through_a_class():
    script.Runner().start()


def test_passed_on(pool):
    pool.submit(script.run, ["x"])


def test_portable_function():
    script.portable(["x"])


def test_package_only():
    return posix_only_across_modules


@needs("posix")
def test_marked():
    script.main(["x"])
