# -*- coding: latin-1 -*-
# Café

"""Endpoints of the store operations; regenerate them instead of editing."""

from collections.abc import Sequence
from typing import Final

import models
from fastapi import APIRouter, params

from .._generated import contract
from .._generated.contract import OperationDependencies
from .._runtime.server.application import Wiring, build, checked
from .._runtime.server.responses import dispatch
from ..services import StoreService


def _add_get_inventory(router: APIRouter, wiring: Wiring) -> None:
    store: StoreService = wiring.services['store']
    get_inventory_handler = checked(
        store.get_inventory,
        'The store.get_inventory method of GET /store/inventory',
    )

    def get_inventory() -> object:
        return dispatch(
            get_inventory_handler(),
            contract.GetInventory.RESPONSES,
        )

    router.add_api_route(
        '/store/inventory',
        get_inventory,
        methods=['GET'],
        status_code=200,
        response_model=models.FieldStoreInventoryGetResponse,
        response_model_by_alias=True,
        response_model_exclude_unset=True,
        operation_id='getInventory',
        tags=['store'],
        response_description='Counts by status.',
        dependencies=wiring.dependencies.get('get_inventory'),
    )


LITERAL_ROUTES: Final = (
    ('get_inventory', _add_get_inventory),
)
TEMPLATED_ROUTES: Final = ()


def build_router(
    *,
    store: StoreService,
    dependencies: Sequence[params.Depends] = (),
    operation_dependencies: OperationDependencies | None = None,
    prefix: str = "",
) -> APIRouter:
    """Register the store operations on a new router, literal paths first."""
    return build(
        (*LITERAL_ROUTES, *TEMPLATED_ROUTES),
        services={'store': store},
        dependencies=dependencies,
        operation_dependencies=operation_dependencies,
        prefix=prefix,
    )
