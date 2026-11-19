"""A module that imports ``loader`` at module level, so ``loader`` imports it locally to close the cycle."""
from . import loader

NAME = loader
