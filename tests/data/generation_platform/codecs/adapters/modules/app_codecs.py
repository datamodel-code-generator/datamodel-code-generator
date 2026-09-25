"""A top-level module whose import alias collides with the `app.codecs` module's alias."""

from app.codecs import version_two

__all__ = ["version_two"]
