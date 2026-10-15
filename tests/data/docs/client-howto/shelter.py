from app.client import Client
from app.client.errors import NotFoundError


def describe(client: Client, pet_id: int) -> str:
    try:
        pet = client.pets.get_pet(pet_id=pet_id)
    except NotFoundError:
        return f"No pet {pet_id}"
    return f"{pet.name} ({pet.tag or 'no tag'})"
