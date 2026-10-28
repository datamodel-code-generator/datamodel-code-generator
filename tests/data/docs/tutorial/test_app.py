from fastapi.testclient import TestClient

from app.server.main import app

client = TestClient(app)


def test_create_and_get_pet() -> None:
    created = client.post("/pets", json={"name": "Mimi", "tag": "cat"})
    assert created.status_code == 201
    assert client.get(f"/pets/{created.json()['id']}").json() == created.json()


def test_missing_pet() -> None:
    response = client.get("/pets/99")
    assert response.status_code == 404
    assert response.json() == {"message": "Pet 99 not found"}
