from collections.abc import Iterator

import httpx2
import pytest

from app.client import Client
from shelter import describe

PETS = {"/v1/pets/1": {"id": 1, "name": "Mimi", "tag": "cat"}}


def answer(request: httpx2.Request) -> httpx2.Response:
    assert request.headers["Authorization"] == "Bearer test-token"
    if (pet := PETS.get(request.url.path)) is None:
        return httpx2.Response(404, json={"message": "Not found"})
    return httpx2.Response(200, json=pet)


@pytest.fixture
def client() -> Iterator[Client]:
    with (
        httpx2.Client(transport=httpx2.MockTransport(answer)) as http,
        Client(bearer="test-token", http_client=http) as client,
    ):
        yield client


def test_describe(client: Client) -> None:
    assert describe(client, 1) == "Mimi (cat)"


def test_describe_missing(client: Client) -> None:
    assert describe(client, 2) == "No pet 2"
