"""A user's orders service, written against the first version of the shop document and kept across regenerations."""

from __future__ import annotations

from shop_models import FieldOrdersGetResponse, Order

from shop import Unset, create_app
from shop.services import OrdersService


class Orders(OrdersService):
    def list_orders(self, *, limit: int | Unset) -> FieldOrdersGetResponse:
        orders = [Order(id=1, total=3), Order(id=2, total=5)]
        return FieldOrdersGetResponse(orders if isinstance(limit, Unset) else orders[:limit])

    def get_order(self, *, id: int) -> Order:  # noqa: A002
        return Order(id=id, total=3)

    def delete_order(self, *, id: int) -> None:  # noqa: A002
        del id


app = create_app(orders=Orders())
