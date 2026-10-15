from app import models
from app.server import HTTPResult
from app.server.services import PetsService


class Pets(PetsService):
    def __init__(self) -> None:
        self.pets: dict[int, models.Pet] = {}

    def list_pets(self, *, limit: int) -> models.Pets:
        return models.Pets(list(self.pets.values())[:limit])

    def create_pet(self, *, body: models.NewPet) -> models.Pet:
        pet = models.Pet(id=len(self.pets) + 1, name=body.name, tag=body.tag)
        self.pets[pet.id] = pet
        return pet

    def get_pet(self, *, pet_id: int) -> models.Pet | HTTPResult[models.Error]:
        if (pet := self.pets.get(pet_id)) is None:
            return HTTPResult(404, models.Error(message=f"Pet {pet_id} not found"))
        return pet
