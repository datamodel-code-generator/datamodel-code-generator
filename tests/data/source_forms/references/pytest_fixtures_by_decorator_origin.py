"""Decorator origins: a decorator call by the origin of what it calls, a fixture named ``test_*`` is not a test.

A ``pytest.fixture`` decorator, bare, called, imported by name or through an alias, makes a function module-level code
for the changed-test selection whatever its name; a marker, a class decorator or a decorator bound twice does not.

decorators: yes
"""

import functools

import pytest
from pytest import fixture
from pytest import mark as marks

shared = pytest.fixture

if pytest.__version__:
    either = pytest.fixture
else:
    either = marks.skip


@pytest.fixture
def test_data():
    return 1


@pytest.fixture(scope="module")
def test_session_data():
    return 2


@fixture
async def test_bare():
    return 3


@shared
def test_aliased():
    return 4


@either
def test_either():
    return 5


@marks.parametrize("value", [1, 2])
@functools.lru_cache(maxsize=1)
def test_value(value, test_data):
    assert value in {test_data, 2}


@marks.usefixtures("test_data")
class TestGroup:
    @fixture
    def test_inner(self):
        return 6

    def test_method(self, test_inner):
        assert test_inner


@(lambda function: function)
def helper():
    return 7
