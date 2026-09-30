"""Import definitions for Pydantic v2 types.

Provides pre-defined Import objects for Pydantic v2 types (ConfigDict, AwareDatetime, etc.).
"""

from __future__ import annotations

from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import _pydantic_imports

IMPORT_BASE_MODEL = Import.from_full_path("pydantic.BaseModel")
IMPORT_CONFIG_DICT = Import.from_full_path("pydantic.ConfigDict")
IMPORT_ALIAS_CHOICES = Import.from_full_path("pydantic.AliasChoices")
IMPORT_ALIAS_GENERATOR_TO_CAMEL = Import.from_full_path("pydantic.alias_generators.to_camel")
IMPORT_ALIAS_GENERATOR_TO_PASCAL = Import.from_full_path("pydantic.alias_generators.to_pascal")
IMPORT_ALIAS_GENERATOR_TO_SNAKE = Import.from_full_path("pydantic.alias_generators.to_snake")
IMPORT_AWARE_DATETIME = Import.from_full_path("pydantic.AwareDatetime")
IMPORT_NAIVE_DATETIME = Import.from_full_path("pydantic.NaiveDatetime")
IMPORT_PAST_DATETIME = Import.from_full_path("pydantic.PastDatetime")
IMPORT_FUTURE_DATETIME = Import.from_full_path("pydantic.FutureDatetime")
IMPORT_PAST_DATE = Import.from_full_path("pydantic.PastDate")
IMPORT_FUTURE_DATE = Import.from_full_path("pydantic.FutureDate")
IMPORT_BASE64BYTES = Import.from_full_path("pydantic.Base64Bytes")
IMPORT_BASE64STR = Import.from_full_path("pydantic.Base64Str")
IMPORT_SERIALIZE_AS_ANY = Import.from_full_path("pydantic.SerializeAsAny")
IMPORT_MISSING = Import.from_full_path("pydantic.experimental.missing_sentinel.MISSING")
IMPORT_PYDANTIC_DATACLASS = Import.from_full_path("pydantic.dataclasses.dataclass")
IMPORT_ROOT_MODEL = Import.from_full_path("pydantic.RootModel")
IMPORT_FIELD_VALIDATOR = Import.from_full_path("pydantic.field_validator")
IMPORT_MODEL_VALIDATOR = Import.from_full_path("pydantic.model_validator")
IMPORT_TYPE_ADAPTER = Import.from_full_path("pydantic.TypeAdapter")
IMPORT_VALIDATION_INFO = Import.from_full_path("pydantic.ValidationInfo")
IMPORT_VALIDATOR_FUNCTION_WRAP_HANDLER = Import.from_full_path("pydantic.ValidatorFunctionWrapHandler")

# Shared Pydantic type imports (moved from model/pydantic/imports.py)
IMPORT_CONSTR = Import.from_full_path("pydantic.constr")
IMPORT_STRING_CONSTRAINTS = Import.from_full_path("pydantic.StringConstraints")
IMPORT_CONINT = Import.from_full_path("pydantic.conint")
IMPORT_CONFLOAT = Import.from_full_path("pydantic.confloat")
IMPORT_CONDECIMAL = Import.from_full_path("pydantic.condecimal")
IMPORT_CONBYTES = Import.from_full_path("pydantic.conbytes")
IMPORT_POSITIVE_INT = Import.from_full_path("pydantic.PositiveInt")
IMPORT_NEGATIVE_INT = Import.from_full_path("pydantic.NegativeInt")
IMPORT_NON_POSITIVE_INT = Import.from_full_path("pydantic.NonPositiveInt")
IMPORT_NON_NEGATIVE_INT = Import.from_full_path("pydantic.NonNegativeInt")
IMPORT_POSITIVE_FLOAT = Import.from_full_path("pydantic.PositiveFloat")
IMPORT_NEGATIVE_FLOAT = Import.from_full_path("pydantic.NegativeFloat")
IMPORT_NON_NEGATIVE_FLOAT = Import.from_full_path("pydantic.NonNegativeFloat")
IMPORT_NON_POSITIVE_FLOAT = Import.from_full_path("pydantic.NonPositiveFloat")
IMPORT_SECRET_STR = Import.from_full_path("pydantic.SecretStr")
IMPORT_EMAIL_STR = Import.from_full_path("pydantic.EmailStr")
IMPORT_UUID1 = Import.from_full_path("pydantic.UUID1")
IMPORT_UUID3 = Import.from_full_path("pydantic.UUID3")
IMPORT_UUID4 = Import.from_full_path("pydantic.UUID4")
IMPORT_UUID5 = Import.from_full_path("pydantic.UUID5")
IMPORT_ANYURL = _pydantic_imports.IMPORT_ANYURL
IMPORT_IPV4ADDRESS = Import.from_full_path("ipaddress.IPv4Address")
IMPORT_IPV6ADDRESS = Import.from_full_path("ipaddress.IPv6Address")
IMPORT_IPV4NETWORKS = Import.from_full_path("ipaddress.IPv4Network")
IMPORT_IPV6NETWORKS = Import.from_full_path("ipaddress.IPv6Network")
IMPORT_FIELD = _pydantic_imports.IMPORT_FIELD
IMPORT_STRICT_INT = Import.from_full_path("pydantic.StrictInt")
IMPORT_STRICT_FLOAT = Import.from_full_path("pydantic.StrictFloat")
IMPORT_STRICT_STR = Import.from_full_path("pydantic.StrictStr")
IMPORT_STRICT_BOOL = Import.from_full_path("pydantic.StrictBool")
IMPORT_STRICT_BYTES = Import.from_full_path("pydantic.StrictBytes")
