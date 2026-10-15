from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ALICE = {"Authorization": "Bearer secret-token"}


def test_upload_and_get_photo() -> None:
    uploaded = client.put("/pets/1/photo", headers=ALICE, files={"file": b"\x89PNG"}, data={"caption": "Rex"})
    assert uploaded.status_code == 201
    assert uploaded.json() == {"petId": 1, "size": 4, "owner": "alice", "caption": "Rex"}
    assert client.get("/pets/1/photo").json() == uploaded.json()


def test_photos_per_tenant() -> None:
    client.put("/pets/2/photo", headers={**ALICE, "X-Tenant": "shelter"}, files={"file": b"..."})
    assert client.get("/pets/2/photo", headers={"X-Tenant": "shelter"}).status_code == 200
    missing = client.get("/pets/2/photo")
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Pet 2 has no photo"}


def test_credentials() -> None:
    assert client.put("/pets/1/photo", files={"file": b""}).status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert client.put("/pets/1/photo", headers=wrong, files={"file": b""}).status_code == 403


def test_validation_errors() -> None:
    response = client.get("/pets/one/photo")
    assert response.status_code == 422
    assert response.json() == {
        "detail": [
            {
                "type": "int_parsing",
                "loc": ["path", "petId"],
                "msg": "Input should be a valid integer, unable to parse string as an integer",
            }
        ]
    }


def test_source_document() -> None:
    document = client.get("/openapi.json").json()
    assert document["paths"]["/pets/{petId}/photo"]["put"]["operationId"] == "uploadPhoto"
