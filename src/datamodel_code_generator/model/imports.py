"""Import definitions for model modules.

Provides pre-defined Import objects for dataclasses, TypedDict, msgspec, etc.
"""

from __future__ import annotations

from datamodel_code_generator._requirements import backport_path, form_path
from datamodel_code_generator.imports import Import

IMPORT_DATACLASS = Import.from_full_path("dataclasses.dataclass")
IMPORT_FIELD = Import.from_full_path("dataclasses.field")
IMPORT_CLASSVAR = Import.from_full_path("typing.ClassVar")
IMPORT_TYPED_DICT = Import.from_full_path(form_path("python.typed_dict_closed"))
IMPORT_TYPED_DICT_BACKPORT = Import.from_full_path(backport_path("python.typed_dict_closed"))
IMPORT_REQUIRED = Import.from_full_path(form_path("python.typed_dict_required"))
IMPORT_REQUIRED_BACKPORT = Import.from_full_path(backport_path("python.typed_dict_required"))
IMPORT_NOT_REQUIRED = Import.from_full_path(form_path("python.typed_dict_not_required"))
IMPORT_NOT_REQUIRED_BACKPORT = Import.from_full_path(backport_path("python.typed_dict_not_required"))
IMPORT_READ_ONLY = Import.from_full_path(form_path("python.typed_dict_read_only"))
IMPORT_READ_ONLY_BACKPORT = Import.from_full_path(backport_path("python.typed_dict_read_only"))
IMPORT_MSGSPEC_STRUCT = Import.from_full_path("msgspec.Struct")
IMPORT_MSGSPEC_FIELD = Import.from_full_path("msgspec.field")
IMPORT_MSGSPEC_META = Import.from_full_path("msgspec.Meta")
IMPORT_MSGSPEC_CONVERT = Import.from_full_path("msgspec.convert")
IMPORT_MSGSPEC_UNSET = Import.from_full_path("msgspec.UNSET")
IMPORT_MSGSPEC_UNSETTYPE = Import.from_full_path("msgspec.UnsetType")
