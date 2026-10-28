from pydantic import BaseModel, ConfigDict


class NamespaceBase(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class NamespaceMethodBase(NamespaceBase):
    def model_foo(self) -> str:
        return "method"
