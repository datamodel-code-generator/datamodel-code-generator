from app.client import Client
from app.client.errors import ConfigurationError
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    client.pets.get_pet(pet_id=1)

tokens = iter(["token-1", "token-2"])
with Client(bearer=lambda: next(tokens), http_client=http_client()) as client:
    client.pets.get_pet(pet_id=1)
    client.pets.get_pet(pet_id=2)

with Client(http_client=http_client()) as client:
    try:
        client.pets.get_pet(pet_id=1)
    except ConfigurationError as error:
        print(error.reason)
