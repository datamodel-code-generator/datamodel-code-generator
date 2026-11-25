import json

from app.client import Client
from app.client.tools import Toolset
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    tools = Toolset(client, read_only=True)
    print([entry["name"] for entry in tools.search("pet")])
    print(json.dumps(tools.schema("pets_get_pet")["input_schema"]))
    print(json.dumps(tools.call("pets_get_pet", {"pet_id": 1})))
    print(json.dumps(tools.call("pets_get_pet", {"pet_id": "one"})))
    print(json.dumps(tools.call("pets_get_pet", {"pet_id": 99})))
