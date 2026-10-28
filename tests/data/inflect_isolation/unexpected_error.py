"""Fail after real private submodules have initialized."""
import sys
from failure_state import ERRORS
# inflect 7.5.0 (uv.lock) provides this submodule inside the private package.
from .compat.py38 import Annotated

raise ERRORS[sys.argv[1]]
