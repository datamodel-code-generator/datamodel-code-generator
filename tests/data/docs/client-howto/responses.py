from app.client import Client
from fake import http_client

with Client(bearer="s3cret", http_client=http_client()) as client:
    response = client.pets.with_response.list_pets(tag="cat")
    info = response.info
    print(len(response.data.root), info.status_code, info.content_type, info.request_id, info.attempt_count)
    print(info.headers.get("x-request-id"), info.headers)

    raw = client.pets.with_raw_response.get_pet(pet_id=99)
    print(raw.info.status_code, raw.json())

    with client.pets.with_streaming_response.list_pets() as streamed:
        body = b"".join(streamed.iter_bytes())
    print(streamed.info.status_code, len(body))
