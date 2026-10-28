import asyncio

from app.client import AsyncClient
from app.models import NewPet


async def main() -> None:
    async with AsyncClient() as client:
        pet = await client.pets.create_pet(body=NewPet(name="Rex", tag="dog"))
        print(await client.pets.get_pet(pet_id=pet.id))


asyncio.run(main())
