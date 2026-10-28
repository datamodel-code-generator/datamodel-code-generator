"""Real parser extensions with opaque model implementations or custom methods."""
from datamodel_code_generator.model import _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model.pydantic_v2 import BaseModel, DataModelField


class OpaqueModel(BaseModel):
    # A module-name prefix cannot establish implementation identity.
    __module__ = 'datamodel_code_generator.model.external_plugin'


class ModelWithMethods(BaseModel):
    def __new__(cls, *args, **kwargs):
        model = BaseModel(*args, **kwargs)
        model.methods.append('def __hash__(self) -> int: return 7')
        return model


class DecoratedModel(BaseModel):
    def __new__(cls, *args, **kwargs):
        model = BaseModel(*args, **kwargs)
        model.decorators.append('@(lambda cls: cls)')
        return model


class IncompleteReferenceModel(BaseModel):
    """Simulate an extension that leaves a primitive reference without its source."""

    def __new__(cls, *args, **kwargs):
        from datamodel_code_generator.reference import Reference

        model = BaseModel(*args, **kwargs)
        if model.name == 'Item':
            model.fields[0].data_type.reference = Reference(path=model.reference.path, name='int')
        return model


class CustomField(DataModelField):
    @property
    def type_hint(self):
        return 'object'


_rebuild_model_with_datamodel_namespace(CustomField)


class ModelWithFields(BaseModel):
    def __new__(cls, *args, **kwargs):
        model = BaseModel(*args, **kwargs)
        if model.name == 'Item':
            model.fields[0] = CustomField(name='value', data_type=model.fields[0].data_type, required=True)
        return model
