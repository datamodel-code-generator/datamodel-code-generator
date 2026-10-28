"""Real custom parser and model extensions consuming root annotations."""

from __future__ import annotations

from datamodel_code_generator.model import pydantic_v2
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser


class AttributesParser(JsonSchemaParser):
    """Use a vendor annotation to enable attribute conversion on model adapters."""

    def set_schema_extensions(self, path: str, obj: JsonSchemaObject) -> None:
        """Consume the application annotation without a configured custom template."""
        super().set_schema_extensions(path, obj)
        if self.raw_obj.get("x-notes"):
            self.extra_template_data[path]["config"] = {"from_attributes": True}


class CustomSchema(JsonSchemaObject):
    """Application schema type with the standard default field behavior."""


class CustomModel(pydantic_v2.BaseModel):
    """Application model type retaining the standard template."""


class CustomUnprovenModel(CustomModel):
    """Application model explicitly disabling the root annotation capability."""

    PLAIN_PATTERN_ROOT_TYPES = None


class CustomUncheckedModel(CustomModel):
    """Application model explicitly disabling the output annotation check."""

    PLAIN_PATTERN_ROOT_CHECKER = None


class CustomRoot(pydantic_v2.RootModel):
    """Application root model type retaining the standard template."""


class CustomField(pydantic_v2.DataModelField):
    """Application field type retaining standard annotations."""


class CustomManager(pydantic_v2.DataTypeManager):
    """Application type manager retaining standard primitive mappings."""


CustomField.model_rebuild(_types_namespace=vars(pydantic_v2.base_model))
