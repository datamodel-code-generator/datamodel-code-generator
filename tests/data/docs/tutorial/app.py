from models import Error, NewPet, Pet, Pets
from server import HTTPResult, create_app
from server.services import PetsService


class PetStore(PetsService):
    def __init__(self) -> None:
        self.pets: dict[int, Pet] = {}

    def list_pets(self, *, limit: int) -> Pets:
        return Pets(list(self.pets.values())[:limit])

    def create_pet(self, *, body: NewPet) -> Pet:
        pet = Pet(id=len(self.pets) + 1, name=body.name, tag=body.tag)
        self.pets[pet.id] = pet
        return pet

    def get_pet(self, *, pet_id: int) -> Pet | HTTPResult[Error]:
        if (pet := self.pets.get(pet_id)) is None:
            return HTTPResult(404, Error(message=f"Pet {pet_id} not found"))
        return pet


app = create_app(pets=PetStore())
