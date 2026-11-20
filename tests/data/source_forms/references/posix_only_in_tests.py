"""G13 posix-only-call: a collected test that reaches what exists on POSIX only needs a capability that Windows lacks.

The use is found by origin in the test, in the helpers it calls, in a fixture it asks for and in a nested function.
``posix`` and ``symlinks`` (``platform!=win32``) and ``linux`` (``platform==linux``) exclude Windows; a capability of
another fact (``type-statements``, a Python version) and one the table lacks do not. A platform test around the use, a
parameter that shadows the module, a string and a function no test reaches are no sites.

The guard of an import decides the import, not the code that reads the module's name later: ``resource`` bound under a
platform test and ``fcntl`` bound under an ``ImportError`` handler are still read by ``peak`` and ``lock``, and a test
that reaches them is a site, unless the read stands under a test of the name itself (``if fcntl is not None:``). A test
reaches the methods it calls on ``self`` (its class's and those it inherits) and the fixtures it is given: by parameter,
``usefixtures`` (decorator or ``pytestmark``) and ``autouse``.
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


if sys.platform != "win32":
    import resource

try:
    import fcntl
except ImportError:
    fcntl = None


def peak():
    return resource.getrusage(resource.RUSAGE_SELF)


def lock_when_present(handle):
    if fcntl is not None:
        fcntl.flock(handle, fcntl.LOCK_EX)


def lock(handle):
    fcntl.flock(handle, fcntl.LOCK_EX)


def test_guarded_import_does_not_guard_its_readers():
    peak()


def test_read_under_a_test_of_the_name():
    lock_when_present(None)


def test_read_of_a_name_that_may_be_none():
    lock(None)


class TestSelf:
    def _spawn(self):
        return os.fork()

    def _portable(self):
        return 1

    def test_method_called_on_self(self):
        self._spawn()

    def test_portable_method_called_on_self(self):
        self._portable()


class TestInherits(TestSelf):
    def test_inherited_method_called_on_self(self):
        self._spawn()


@pytest.mark.usefixtures("pipe_dir")
def test_usefixtures_decorator():
    pass


@needs("posix")
@pytest.mark.usefixtures("pipe_dir")
def test_usefixtures_marked():
    pass


class TestUsefixturesMark:
    pytestmark = pytest.mark.usefixtures("pipe_dir")

    def test_pytestmark(self):
        pass


class TestAutouse:
    @pytest.fixture(autouse=True)
    def _pipe(self, tmp_path):
        os.mkfifo(tmp_path / "pipe")

    def test_runs_after_the_fixture(self):
        pass
