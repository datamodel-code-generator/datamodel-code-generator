"""Simulate an inflect update that needs another typeguard API."""
from typeguard import typechecked

# inflect 7.5.0 (uv.lock) provides this submodule inside the private package.
from .compat.py38 import Annotated


@typechecked(collection_check_strategy="ALL")
def marker():
    return Annotated


from typeguard import future_api
