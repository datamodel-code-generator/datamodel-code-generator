"""Two modules that import a name from each other: the chain is a cycle."""

from .other import second as first
