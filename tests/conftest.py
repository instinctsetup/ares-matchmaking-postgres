"""Integration-test fixtures: a scratch database and a live app.

DATABASE_URL must point at a throwaway database; the app's lifespan drops
and recreates the whole schema on startup, which is exactly what these
tests want.
"""

import os
import time

import pytest
from starlette.testclient import TestClient

from ares import config

# Fast ticks so the engine matches within the test timeout.
config.MATCHMAKER_TICK_SECONDS = 0.2
config.CLEANUP_TICK_SECONDS = 60

if not os.getenv("DATABASE_URL"):
    pytest.skip("DATABASE_URL not set; integration tests need a database", allow_module_level=True)

from ares.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def join_party(client, size: int, elo: int = 1200, region: str = "IN", prefix: str = "Bot") -> str:
    players = [
        {"username": f"{prefix}_{int(time.time() * 1000)}_{i}", "elo": elo, "latency": 20, "region": region}
        for i in range(size)
    ]
    response = client.post("/join", json={"players": players})
    assert response.status_code == 200, response.text
    return response.json()["party_id"]


def wait_for_match(client, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = client.get("/matches").json()
        if matches:
            return matches[0]
        time.sleep(0.25)
    raise AssertionError("no match was created within the timeout")
