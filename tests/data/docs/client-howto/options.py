import httpx2

from app.client import Client
from app.client.options import RequestOptions, ServerSelection
from fake import http_client

with Client(
    bearer="s3cret",
    server=ServerSelection(variables={"region": "us"}),
    default_headers={"X-App": "shelter-cli"},
    timeout=httpx2.Timeout(10, connect=2),
    http_client=http_client(),
) as client:
    client.pets.list_pets()

    traced = client.with_options(default_headers={"X-Trace": "on"}, total_timeout=30)
    traced.pets.list_pets()

    staging = RequestOptions(base_url="https://staging.shelter.example/v1", extra_headers={"X-App": None})
    client.pets.list_pets(options=staging)
