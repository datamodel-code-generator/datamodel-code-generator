"""A normal renamed reexport keeps the original import identity."""
from pydantic import BaseModel

from tests.data.python.input_model.nested_reuse_exports import Inner as Renamed


class AliasRoot(BaseModel):
    child: Renamed
