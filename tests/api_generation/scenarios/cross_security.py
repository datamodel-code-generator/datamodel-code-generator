"""Call the secured server through its client: API keys in a header, a query, and a cookie, Basic, bearer, OAuth 2.

Requests with a header or cookie credential that is not ASCII present no credential of that scheme, so another
requirement set may still admit them.

The client takes each scheme's credential and the server's `authorize` callback reports the requirement sets and
credentials each operation presented. The OAuth 2 client credentials flow requests its token from the document's
token URL, which the application serves. The document's digest and mutual TLS operation is left out, since the
client has no credential for either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

from tests.api_generation.support.cross_targets import service

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from types import ModuleType

    from fastapi import FastAPI

    from tests.api_generation.support.cross_targets import Calls, Kit, Recorder

DOCUMENT = "fastapi/server-security.yaml"
MODEL = {"openapi_include_paths": ["/pets*", "/public", "/maybe", "/session"]}


def services(server: ModuleType, models: ModuleType, record: Recorder) -> dict[str, object]:
    """Return recording services under the path of the document's server, and an `authorize` callback."""
    del models

    def authorize(requirement_sets: tuple[object, ...], credentials: Mapping[str, object]) -> str:
        record("authorize", {"requirement_sets": requirement_sets, "credentials": dict(credentials)})
        if credentials.get("api_key") == "revoked":
            raise HTTPException(403, "revoked")
        return f"principal of {', '.join(sorted(credentials))}"

    return {
        "pets": service(server.services.PetsService, record, list_pets=lambda **_: ["Mimi"]),
        "public": service(server.services.PublicService, record),
        "default": service(server.services.DefaultService, record),
        "authorize": authorize,
        "prefix": "/v2",
    }


def extend(app: FastAPI, record: Recorder) -> None:
    """Serve the client credentials token endpoint the document's OAuth 2 scheme names."""

    @app.post("/token")
    async def token(request: Request) -> dict[str, object]:
        form = await request.form()
        record("token", {"form": dict(form), "authorization": request.headers.get("authorization")})
        return {"access_token": "issued-token", "token_type": "Bearer", "expires_in": 3600}


def calls(kit: Kit) -> Calls:
    """Return calls with each scheme's credential, a credential the server rejects, and none."""
    oauth = kit.module("auth").OauthClientCredentials(client_id="pets", client_secret="s3cret", scopes=("read:pets",))
    keyed = kit.connect(api_key="key-1")
    base = kit.options.get("base_url", "http://testserver")

    def raw(path: str, headers: list[tuple[bytes, bytes]]) -> Callable[[], object]:
        url = f"{base}/v2/{path}"
        return lambda: kit.then(kit.http.get(url, headers=headers), lambda response: response.status_code)

    return [
        ("list_pets with the header key", keyed.pets.list_pets),
        ("list_pets with a rejected key", kit.connect(api_key="revoked").pets.list_pets),
        ("list_pets without credentials", kit.api.pets.list_pets),
        ("get_public", kit.api.public.get_public),
        ("get_maybe anonymous", lambda: kit.api.default.get_maybe(principal="query value")),
        ("get_maybe with a bearer token", kit.connect(bearer="token-1").default.get_maybe),
        ("put_pet with OAuth client credentials", lambda: kit.connect(oauth=oauth).default.put_pet(petId=1)),
        ("put_pet with the cached token", lambda: kit.connect(oauth=oauth).default.put_pet(petId=2)),
        (
            "put_pet with the query key and Basic",
            lambda: kit.connect(query_key="key 2", basic=("user", "pass:word")).default.put_pet(petId=3),
        ),
        (
            "put_pet with the query key and Basic beyond ASCII",
            lambda: kit.connect(query_key="key 3", basic=("ü", "pässword")).default.put_pet(petId=4),
        ),
        ("get_session with the cookie key", kit.connect(cookie_key="cookie-1").default.get_session),
        ("get_session with OpenID Connect", kit.connect(oidc="id-token").default.get_session),
        ("get_session with the header key", keyed.default.get_session),
        ("get_session with a header key beyond ASCII", raw("session", [(b"X-API-Key", b"caf\xe9")])),
        ("get_session with a cookie key beyond ASCII", raw("session", [(b"Cookie", "session=café".encode())])),
        ("get_session with an OpenID token beyond ASCII", raw("session", [(b"Authorization", b"Bearer t\xe9")])),
        ("get_session with Basic beyond ASCII", raw("session", [(b"Authorization", "Basic dXNlcjpw é".encode())])),
        (
            "get_session with a header key beyond ASCII and the cookie key",
            raw("session", [(b"X-API-Key", b"caf\xe9"), (b"Cookie", b"session=cookie-1")]),
        ),
        ("get_maybe with a bearer token beyond ASCII", raw("maybe", [(b"Authorization", "Bearer té".encode())])),
    ]
