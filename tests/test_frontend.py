"""The demo UI is served as static files at /ui."""

from starlette.testclient import TestClient

from ares.main import app


def test_frontend_index_served():
    client = TestClient(app)
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "ARES" in response.text
    assert "app.js" in response.text


def test_frontend_assets_served():
    client = TestClient(app)
    assert client.get("/ui/style.css").status_code == 200
    assert client.get("/ui/app.js").status_code == 200
