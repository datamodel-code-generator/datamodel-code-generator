import pytest

from tests.capabilities import needs


def test_asks_for_the_fixture(pipe):
    return pipe


def test_asks_for_a_fixture_that_asks(pipe_user):
    return pipe_user


@pytest.mark.usefixtures("pipe")
def test_usefixtures():
    pass


@needs("posix")
def test_marked(pipe):
    return pipe


def test_portable_fixture(portable):
    return portable


def test_without_fixtures():
    pass
