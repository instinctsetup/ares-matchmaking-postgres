"""End-to-end tests against a live app and scratch database."""

from uuid import uuid4

from conftest import join_party, wait_for_match


def test_health(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "Ares Online"


def test_join_then_match_forms_and_queue_drains(client):
    for i in range(10):
        join_party(client, size=1, prefix=f"Solo{i}")
    match = wait_for_match(client)
    assert match["match_type"] == "LOCAL"
    assert match["winner"] is None
    # All ten solos got matched: queue is empty again.
    assert client.get("/queue").json()["total_count"] == 0


def test_match_resolution_updates_elo_and_rejects_replay(client):
    for i in range(10):
        join_party(client, size=1, elo=1200, prefix=f"Res{i}")
    match = wait_for_match(client)

    response = client.post(f"/match/{match['match_id']}/resolve", params={"winner": "A"})
    assert response.status_code == 200

    replay = client.post(f"/match/{match['match_id']}/resolve", params={"winner": "B"})
    assert replay.status_code == 400

    top = client.get("/leaderboard").json()["top_players"]
    assert top[0]["total_elo"] == 1225  # 1200 + win bonus
    assert len(top) == 10


def test_resolve_rejects_bad_winner_and_unknown_match(client):
    bad = client.post(f"/match/{uuid4()}/resolve", params={"winner": "C"})
    assert bad.status_code == 400
    missing = client.post(f"/match/{uuid4()}/resolve", params={"winner": "A"})
    assert missing.status_code == 404


def test_stats_and_throughput_shape(client):
    stats = client.get("/stats").json()
    assert {"active_queue", "matches_today", "success_rate", "engine_load"} <= stats.keys()
    throughput = client.get("/analytics/throughput").json()
    assert {"throughput", "regions"} <= throughput.keys()
