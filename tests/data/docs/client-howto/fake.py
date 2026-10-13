"""A stand-in for the shelter API: an HTTPX2 client whose transport prints each request and answers from memory."""

import json

import httpx2

PETS = {"1": {"id": 1, "name": "Mimi", "tag": "cat"}, "2": {"id": 2, "name": "Rex", "tag": "dog"}}
SHOWN = ("authorization", "content-type", "x-app", "x-trace", "x-signature")
health_checks: list[int] = []


def answer(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path.removeprefix("/v1")
    match request.method, path.split("/")[1:]:
        case "POST", ["token"]:
            return httpx2.Response(200, json={"access_token": "issued-1", "token_type": "Bearer", "expires_in": 3600})
        case "GET", ["pets"]:
            return httpx2.Response(200, json=list(PETS.values()), headers={"X-Request-Id": "req-7"})
        case "POST", ["pets"]:
            return httpx2.Response(201, json={"id": 3, **json.loads(request.content)})
        case "GET", ["pets", "1" | "2"]:
            return httpx2.Response(200, json=PETS[path.split("/")[2]])
        case "POST", ["pets", "1", "adoption"]:
            return httpx2.Response(409, json={"message": "Mimi is already adopted"})
        case "GET", ["health"]:
            health_checks.append(len(health_checks))
            return httpx2.Response(503 if len(health_checks) < 3 else 200, json={"status": "up"})
        case _:
            return httpx2.Response(404, json={"message": f"{path} not found"})


def handle(request: httpx2.Request) -> httpx2.Response:
    if request.url.host == "offline.example":
        raise httpx2.ConnectError("connection refused", request=request)
    headers = "".join(f" {name}: {value}" for name, value in request.headers.items() if name in SHOWN)
    print(f"> {request.method} {request.url}{headers} {request.content.decode()}".rstrip())
    return answer(request)


def http_client() -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handle))
