"""Contacts.

import pendulum
from ulid import ULID
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, EmailStr, RootModel
from ulid import ULID


class EmailStrModel(RootModel[EmailStr]):
    root: EmailStr


class Identifier(RootModel[ULID]):
    root: ULID


class Contact(BaseModel):
    id: Identifier
    mail: EmailStrModel
    born: date | None = None
