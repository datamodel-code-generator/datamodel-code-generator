"""#3866 architecture boundaries: ``DataModelType`` read through its module is the enum, whatever the module is called.

The receiver of the member is the chain ``enums.DataModelType``; its origin is the enum's, while the head ``enums`` is
the module.
layers: parser, input-model
"""
import datamodel_code_generator.enums as backend_enums
from datamodel_code_generator import enums

FIRST = enums.DataModelType.PydanticV2BaseModel
SECOND = backend_enums.DataModelType.PydanticV2BaseModel
