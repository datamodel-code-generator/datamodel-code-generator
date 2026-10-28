"""Independent native control for Python-regex root validation."""
from pydantic import ConfigDict, Field, RootModel


class NativeLookaround(RootModel[str]):
    model_config = ConfigDict(regex_engine="python-re")
    root: str = Field(pattern=r"^(?=a)")
