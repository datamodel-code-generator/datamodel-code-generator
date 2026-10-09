"""Reject credentials of another kind than their scheme takes, and other auth values."""

from __future__ import annotations

from pets import Client
from pets.auth import OauthClientCredentials, OauthRefreshToken, TokenSet
from pets.errors import AuthError
from pets.options import ClientOptions


def wrong_credentials(provider: OauthClientCredentials) -> None:
    """Reject each mistyped credential."""
    Client(bearer=1)  # error
    Client(basic="user:password")  # error
    Client(header_key=provider)  # error
    Client(unknown="token")  # error
    TokenSet(access_token=1)  # error
    OauthRefreshToken("access", client_id="c")  # error
    OauthClientCredentials(client_id="c")  # error
    ClientOptions(auth="token")  # error
    AuthError(reason="expired")  # error
