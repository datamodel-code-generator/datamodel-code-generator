"""A10 raw-skip: skip markers, calls and exceptions are found by where their names come from.

An alias of ``pytest.mark``, of the ``skipif`` marker or of ``pytest.skip`` is a skip; a parameter, a local or a class
member named ``skip`` or ``skipif`` is not, and neither is a string that spells one.
"""

import sys
import unittest as ut

import pytest as pt
from pytest import importorskip
from pytest import mark as marks

when = marks.skipif
REFERENCE = "pytest.mark.skipif"


@when(sys.platform == "win32", reason="POSIX only")
def test_aliased():
    importorskip("grpc_tools")
    pt.skip("an alias of the module")


class TestCase(ut.TestCase):
    skip = staticmethod(lambda reason: reason)

    @ut.skipIf(True, "never")
    def test_method(self):
        self.skip("a member")


def test_parameter(skip, mark):
    skip("a parameter")
    mark.skipif


def test_local():
    skipif = lambda condition: condition
    skipif(True)
