from app.client import Client
from app.client.errors import APIConnectionError, APIStatusError, NotFoundError
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    try:
        client.pets.get_pet(pet_id=99)
    except NotFoundError as error:
        print(error.status_code, repr(error.body))

    try:
        client.adoptions.adopt_pet(pet_id=1)
    except APIStatusError as error:
        print(type(error).__name__, error)

    offline = client.with_options(base_url="https://offline.example/v1", max_retries=0)
    try:
        offline.pets.list_pets()
    except APIConnectionError as error:
        print(type(error).__name__, error.reason, repr(error.cause))
