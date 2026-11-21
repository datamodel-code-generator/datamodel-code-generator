class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class User(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    id: int
    name: str
    tag: Optional[str] = None


class Users(RootModel[List[User]]):
    root: List[User]


class Id(RootModel[str]):
    root: str


class Rules(RootModel[List[str]]):
    root: List[str]


class Error(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    code: int
    message: str


class Event(BaseModel):
    name: Optional[str] = None


class Result(BaseModel):
    event: Optional[Event] = None


class Broken(BaseModel):
    foo: Optional[str] = None
    bar: Optional[int] = None


class BrokenArray(BaseModel):
    broken: Optional[Dict[str, List[Broken]]] = None


class FileSetUpload(BaseModel):
    task_id: Optional[str] = None
    tags: Dict[str, List[str]]


class Test(BaseModel):
    broken: Optional[Dict[str, Broken]] = None
    failing: Optional[Dict[str, str]] = {}
