from fastapi.testclient import TestClient

from einvoice_reader.api import create_app


def test_health_ok():
    with TestClient(create_app()) as client:
        response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
