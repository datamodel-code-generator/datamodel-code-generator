from app.client import Client
from app.client.tools import Toolset
from fake import http_client

CODE = """
found = [entry["name"] for entry in search("pet", limit=5)]
pet = call("pets_get_pet", {"pet_id": 1})
print(found, schema("pets_get_pet")["input_schema"]["required"], pet["name"])
print(call("pets_list_pets", {})["error"])
"""


def run(code: str, functions: dict[str, object]) -> None:
    """Stand in for the host's sandbox, which runs the model's code with only these functions."""
    exec(code, {"__builtins__": {"print": print}, **functions})


with Client(bearer="s3cret", http_client=http_client()) as client:
    tools = Toolset(client, read_only=True, max_result_bytes=64)
    print([(entry["name"], entry["safe"]) for entry in tools.catalog()])
    run(CODE, {"search": tools.search, "schema": tools.schema, "call": tools.call})
