method count: 1

class Model(BaseModel):
    first: str
    second: int

    def generated_method(self) -> str:
        return "ok"
