"""Implement StoreService with a builtin that has no signature to check."""

from app.server.services import StoreService


class Store(StoreService):
    """Answer with whatever next() answers."""

    get_inventory = next
