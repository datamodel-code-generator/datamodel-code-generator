# multiline custom ;
# header ;
# file ;


from pydantic import AnyUrl, BaseModel, Field, RootModel


class Pet(BaseModel):
    id: int
    name: str
    tag: str | None = None


class Pets(RootModel[list[Pet]]):
    root: list[Pet]


class User(BaseModel):
    id: int
    name: str
    tag: str | None = None


class Users(RootModel[list[User]]):
    root: list[User]


class Id(RootModel[str]):
    root: str


class Rules(RootModel[list[str]]):
    root: list[str]


class Error(BaseModel):
    code: int
    message: str


class Api(BaseModel):
    apiKey: str | None = Field(
        None, description='To be used as a dataset parameter value'
    )
    apiVersionNumber: str | None = Field(
        None, description='To be used as a version parameter value'
    )
    apiUrl: AnyUrl | None = Field(
        None, description="The URL describing the dataset's fields"
    )
    apiDocumentationUrl: AnyUrl | None = Field(
        None, description='A URL to the API console for each API'
    )


class Apis(RootModel[list[Api]]):
    root: list[Api]


class Event(BaseModel):
    name: str | None = None


class Result(BaseModel):
    event: Event | None = None
