from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel
import argparse


class Child(BaseModel):
    value: Optional[int] = None


class Defaults(BaseModel):
    implicit: Optional[str] = None
    schemaNull: Optional[str] = None
    equalOverride: Optional[str] = None
    scopedOverride: Optional[int] = 7
    nullable: Optional[str] = None
    nullable_items: Optional[List[Optional[str]]] = None
    nullable_container: Optional[List[str]] = None
    child: Optional[Child] = None
    external: Optional[argparse.HelpFormatter._Section] = None


