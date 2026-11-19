"""G13 posix-only-call: a collected test that reaches what exists on POSIX only needs a capability that Windows lacks.

The use is found by origin in the test, in the helpers it calls, in a fixture it asks for and in a nested function.
``posix`` and ``symlinks`` (``platform!=win32``) and ``linux`` (``platform==linux``) exclude Windows; a capability of
another fact (``type-statements``, a Python version) and one the table lacks do not. A platform test around the use, a
parameter that shadows the module, a string and a function no test reaches are no sites.
"""

import os
import sys

import pytest

from tests.capabilities import needs


def make_pipe(path):
    os.mkfifo(path)


def through_a_helper(path):
    make_pipe(path)


@pytest.fixture
def pipe_dir(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    return tmp_path


def test_unmarked():
    through_a_helper("pipe")


@needs("posix")
def test_posix():
    through_a_helper("pipe")


@needs("linux")
def test_linux():
    through_a_helper("pipe")


@needs("symlinks", "type-statements")
def test_one_capability_excludes_windows():
    os.mkfifo("pipe")


@needs("type-statements")
def test_python_version_is_no_platform():
    os.mkfifo("pipe")


@needs("gpu")
def test_unknown_capability():
    os.mkfifo("pipe")


def test_fixture(pipe_dir):
    return pipe_dir


def test_from_import():
    from os import fork

    fork()


def test_nested():
    def test_inner():
        os.fork()

    test_inner()


def test_guarded():
    if sys.platform != "win32":
        os.fork()


def test_string():
    return "os.fork"


def test_shadowed(os):
    os.fork()


def unused_helper():
    os.fork()


class TestMarked:
    pytestmark = needs("posix")

    def test_method(self):
        os.fork()


class TestPlain:
    def test_method(self):
        os.fork()
