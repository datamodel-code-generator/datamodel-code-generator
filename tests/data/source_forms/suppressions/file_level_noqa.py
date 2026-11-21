"""#7026 private-reexport: a file-level noqa on its own line covers every line; a trailing one does not.

codes: F401 unused-import
"""
# ruff: noqa: F401
# flake8: noqa

from .home import _read, _unread
x = 1  # ruff: noqa: E501

print(_read)
