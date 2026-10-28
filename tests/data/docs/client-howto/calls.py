from app.client import Client
from app.client.options import UNSET
from fake import http_client
from app.models import NewPet

with Client(bearer="s3cret", http_client=http_client()) as client:
    print(client.pets.get_pet(pet_id=1))

    client.pets.list_pets()
    client.pets.list_pets(limit=5, tag="cat")
    client.pets.list_pets(tag=UNSET)

    print(client.pets.create_pet(body=NewPet(name="Kiki")))
    print(client.pets.create_pet(name="Kiki", tag="bird"))
