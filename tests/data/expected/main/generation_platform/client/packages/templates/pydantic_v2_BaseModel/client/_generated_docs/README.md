# client

The `explicit` methods of this package send through HTTPX2 and decode with the `pydantic_v2.BaseModel` models.

- `pets.list_pets`: `GET /pets`
- `pets.create_pet`: `POST /pets`
- `pets.get_pet`: `GET /pets/{petId}`
- `pets.delete_pets_by_pet_id`: `DELETE /pets/{petId}`
- `pets.head_pet`: `HEAD /pets/{petId}`
- `pets.upload_photo`: `PUT /pets/{petId}/photo`
- `pets.attach_files`: `POST /pets/{petId}/files`
- `pets.read_files`: `GET /pets/{petId}/files`

## Support

Ask the platform team before changing the API document; regenerate the package instead of editing it.
