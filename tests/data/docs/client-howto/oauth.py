from app.client import Client
from app.client.auth import OauthClientCredentials
from fake import http_client

provider = OauthClientCredentials(client_id="shelter-cli", client_secret="pa55", scopes=("pets.read",))
with Client(oauth=provider, http_client=http_client()) as client:
    client.pets.get_pet(pet_id=1)
    client.pets.get_pet(pet_id=2)
