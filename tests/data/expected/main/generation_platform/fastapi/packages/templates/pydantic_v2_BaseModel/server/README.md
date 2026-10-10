# Pets

Ask the platform team before changing the API document; regenerate the package instead of editing it.

- `GET /pets` is handled by `PetsService.list_pets`
- `POST /pets` is handled by `PetsService.create_pet`
- `GET /pets/mine` is handled by `PetsService.list_my_pets`
- `GET /pets/{petId}` is handled by `PetsService.get_pet`
- `DELETE /pets/{petId}` is handled by `PetsService.delete_pet`
- `GET /store/inventory` is handled by `StoreService.get_inventory`
