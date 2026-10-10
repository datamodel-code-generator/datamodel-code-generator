"""Keep constructor credentials, OAuth providers, and native Auth settings typed."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import httpx2
from pets import AsyncClient, Client
from pets.auth import ClientCredentials, OauthClientCredentials, OauthRefreshToken, RefreshToken, TokenSet
from pets.errors import AuthError, AuthReason
from pets.options import RequestOptions
from typing_extensions import assert_type


def credentials(token: Callable[[], str], user: Callable[[], tuple[str, str]]) -> None:
    """Pass each scheme kind's values and callables, and OAuth providers for bearer schemes."""
    Client(bearer="token", header_key=token, basic=("user", "password"), query_key=lambda: "key", cookie_key="c")
    AsyncClient(basic=user, oauth=OauthClientCredentials(client_id="c", client_secret=token, audience="api"))
    tokens = TokenSet("access", "refresh", datetime.now(timezone.utc))
    assert_type(tokens.refresh_token, str | None)
    refresh = OauthRefreshToken(tokens, client_id="c", on_token_refreshed=lambda refreshed: None)
    Client(oauth=refresh, openid=RefreshToken(TokenSet("access"), client_id="c", token_url="https://id.example.com/t"))
    assert_type(ClientCredentials.token_url, str | None)
    provider = ClientCredentials(client_id="c", client_secret="s", token_url="https://id.example.com/t", scopes=["a"])
    Client(bearer_alias=provider, http_client=httpx2.Client())


def errors() -> None:
    """Keep the credentials' errors typed."""
    auth = AuthError(reason="oauth_error", status_code=400, oauth_error="invalid_grant")
    assert_type(auth.reason, AuthReason)
    assert_type(auth.status_code, int | None)


def native(client: Client) -> None:
    """Replace the credentials with a native Auth, or send none."""
    Client(auth=httpx2.BasicAuth("user", "password"))
    client.with_options(auth=None).auth.anonymous()
    client.auth.bearer(options=RequestOptions(auth=OauthClientCredentials(client_id="c", client_secret="s")))
