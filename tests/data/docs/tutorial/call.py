from client import Client
from client.errors import NotFoundError
from models import NewPet

with Client() as client:
    pet = client.pets.create_pet(body=NewPet(name="Mimi", tag="cat"))
    print(pet)
    for listed in client.pets.list_pets(limit=10).root:
        print(listed.name)
    try:
        client.pets.get_pet(pet_id=99)
    except NotFoundError as error:
        print(error.status_code, error.body)
