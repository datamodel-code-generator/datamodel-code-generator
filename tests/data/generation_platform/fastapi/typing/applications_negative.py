"""Connections to the generated secured server that type checkers must reject, one error per line from line 23."""

from __future__ import annotations

from fastapi import Request

from secured import FastAPIOptions, OperationDependencies
from secured.auth_types import AuthContext, Authorizer, Credential, CredentialExtractor, CustomSecret


def authorize(context: AuthContext[str]) -> str:
    return context.operation_key


def wrong_scheme(request: Request) -> Credential[str] | None:
    return None


class Settings:
    pass


unknown: FastAPIOptions = {"lifespan": None}
classes: FastAPIOptions = {"contact": {"model": Settings}}
response_class: FastAPIOptions = {"default_response_class": dict}
flag: FastAPIOptions = {"debug": "yes"}
dependencies: OperationDependencies = {"/paths/~1nope/get": []}
scheme = Credential[str](scheme_name="nope", payload=CustomSecret(value="x"))
narrow: Authorizer[int, str] = authorize
extractor: CredentialExtractor[bytes] = wrong_scheme
