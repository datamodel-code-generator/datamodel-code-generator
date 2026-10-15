import hashlib
import hmac
from collections.abc import Generator

import httpx2

from app.client import Client
from fake import http_client


class BodySigner(httpx2.Auth):
    requires_request_body = True

    def __init__(self, key: bytes, token: str) -> None:
        self.key = key
        self.token = token

    def auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        digest = hmac.digest(self.key, request.method.encode() + b"\n" + request.content, hashlib.sha256)
        request.headers["Authorization"] = f"Bearer {self.token}"
        request.headers["X-Signature"] = digest.hex()
        yield request


with Client(http_client=http_client()) as client:
    signed = client.with_options(auth=BodySigner(b"signing-key", "s3cret"))
    print(signed.pets.create_pet(name="Kiki"))
