"""Call the installed client's copied runtime through the parent test's TLS fixture server."""

from __future__ import annotations

import asyncio
import importlib
import json
import pkgutil
import ssl
import sys
from importlib.metadata import distributions
from pathlib import Path

import httpx2
import publication_client
import publication_client_models as models
from publication_client import AsyncClient, Client, auth, options


def _options(profile: str, url: str, native: httpx2.Client | httpx2.AsyncClient) -> options.ClientOptions:
    credentials = None
    static = auth.AsyncStaticCredentialProvider if isinstance(native, httpx2.AsyncClient) else auth.StaticCredentialProvider
    if profile == "api-key":
        credentials = auth.AuthConfig({"credential": static(auth.ApiKeyCredential("install-key"))})
    elif profile == "oauth2-client-credentials":
        provider = (
            auth.AsyncClientCredentialsProvider if isinstance(native, httpx2.AsyncClient) else auth.ClientCredentialsProvider
        )
        credentials = auth.AuthConfig({
            "credential": provider(
                f"{url}/token",
                client_id="installed-client",
                client_secret=static(auth.ApiKeyCredential("install-secret")),
                scopes=("read",),
                http_client=native,
            )
        })
    return options.ClientOptions(base_url=url, auth=credentials)


def _body(backend: str, profile: str) -> object:
    value = {"name": "installed"} if profile == "all" else {"id": 7, "name": "installed"}
    model = models.NewPet if profile == "all" else models.Pet
    return value if backend == "typing.TypedDict" else model(**value)


def _result(value: object) -> str:
    return json.dumps(
        {"id": value["id"], "name": value["name"]}
        if isinstance(value, dict)
        else {"id": value.id, "name": value.name},
        sort_keys=True,
    )


async def _async(profile: str, backend: str, url: str, context: ssl.SSLContext) -> None:
    async with httpx2.AsyncClient(verify=context, trust_env=False) as native:
        async with AsyncClient(options=_options(profile, url, native), http_client=native) as client:
            print("async", _result(await client.pets.create_pet(body=_body(backend, profile), **({"media_type": "application/json"} if profile == "all" else {}))))


def main() -> None:
    profile, backend, url, authority = sys.argv[1:]
    print("installed package", Path(publication_client.__file__).is_relative_to(sys.prefix))
    for module in pkgutil.walk_packages(publication_client.__path__, f"{publication_client.__name__}."):
        importlib.import_module(module.name)
    print("all package modules imported")
    installed = {distribution.metadata["Name"].lower().replace("_", "-") for distribution in distributions()}
    names = ("datamodel-code-generator", "jsonschema", "referencing", "google-re2", "pydantic", "msgspec", "websockets", "cryptography")
    print("optional distributions", [name for name in names if name in installed])
    context = ssl.create_default_context(cafile=authority)
    with httpx2.Client(verify=context, trust_env=False) as native:
        with Client(options=_options(profile, url, native), http_client=native) as client:
            print("sync", _result(client.pets.create_pet(body=_body(backend, profile), **({"media_type": "application/json"} if profile == "all" else {}))))
    asyncio.run(_async(profile, backend, url, context))


main()
