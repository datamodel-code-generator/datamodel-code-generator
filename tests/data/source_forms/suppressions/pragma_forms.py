"""A3: no branch, spacing, and conditional cover pragmas (covdefaults' version forms)."""

import sys

if sys.version_info >= (3, 12):  # pragma: no branch
    VALUE = 1
if sys.version_info < (3, 12):  # pragma: <3.12 cover
    OTHER = 2
if sys.version_info >= (3, 14):  # pragma:   no   cover
    THIRD = 3
LAST = 4  # pragma: no py311 cover
