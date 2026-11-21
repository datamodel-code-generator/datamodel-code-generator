from __future__ import annotations

import importlib
import json

import httpx2
import yaml
from pydantic import BaseModel

from datamodel_code_generator import Error

from . import errors
from .. import types
from .client import codecs


def load_dynamically():
    importlib.import_module("pydantic")
    importlib.import_module("datamodel_code_generator.parser")
