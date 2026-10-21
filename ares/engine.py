"""Background engine: the matchmaking loop and the heartbeat reaper.

Both run as asyncio tasks for the lifetime of the app (see main.py).
The loop is deliberately simple: every tick it sweeps each active region,
tries to build one fair 5v5 match from the waiting parties, commits it in a
single transaction, and pushes MATCH_FOUND to every party's WebSocket.
"""

import asyncio
import uuid

from . import config, database, matchmaking
from .websocket import ws_manager


async def queue_cleanup_loop() -> None:
    """Purge players who stopped sending heartbeats."""
    while True:
        try:
            purged = await database.purge_stale_players(config.HEARTBEAT_TIMEOUT_SECONDS)
            if purged:
                print(f"[Ares Cleanup] Purged {purged} disconnected players.")
        except Exception as exc:  # keep the reaper alive across DB hiccups
            print(f"[Cleanup Error] {exc}")
        await asyncio.sleep(config.CLEANUP_TICK_SECONDS)


async def _try_build_match(conn, region: str) -> bool:
    """One matchmaking attempt for a region. Returns True if a match was made.

    The read-decide-write sequence runs in one transaction so two sweeps can
    never hand the same players to two matches.
    """
    async with database.transaction(conn):
        wait_seconds = await database.oldest_wait_seconds(region, conn)
        if wait_seconds is None:
            return False  # empty queue

        going_global = wait_seconds > config.GLOBAL_MATCH_WAIT_SECONDS
        match_type = "GLOBAL" if going_global else "LOCAL"

        candidates = await database.fetch_queue_candidates(
            conn, None if going_global else region, config.QUEUE_BATCH_SIZE
        )
        if len(candidates) < config.PLAYERS_PER_MATCH:
            return False

        parties = matchmaking.group_into_parties(candidates)

        team_a = matchmaking.form_team(parties)
        if team_a is None:
            return False  # no exact 5-player combination yet
        used = {party_id for party_id, _ in team_a}
        team_b = matchmaking.form_team([p for p in parties if p[0] not in used])
        if team_b is None:
            return False

        avg_elo_a = matchmaking.team_average_elo([players for _, players in team_a])
        avg_elo_b = matchmaking.team_average_elo([players for _, players in team_b])
        elo_gap = abs(avg_elo_a - avg_elo_b)
        if elo_gap > matchmaking.elo_threshold(wait_seconds):
            return False  # teams too uneven; wait for the threshold to relax

        selected_map = await database.random_map() or config.DEFAULT_MAP
        match_id = uuid.uuid4()
        match_avg_elo = int((avg_elo_a + avg_elo_b) / 2)
        await database.create_match(
            conn, match_id, selected_map, region, match_avg_elo, match_type, team_a, team_b
        )

    _log_match(match_id, selected_map, region, match_type, match_avg_elo, elo_gap, team_a, team_b)
    await _notify_match(match_id, selected_map, match_avg_elo, team_a, team_b)
    return True


def _log_match(match_id, map_name, region, match_type, avg_elo, elo_gap, team_a, team_b) -> None:
    shape = lambda team: " + ".join(f"Party[{len(players)}]" for _, players in team)
    print(f"[ARES] match {match_id} | map={map_name} region={region} type={match_type}")
    print(f"[ARES] team_a: {shape(team_a)}")
    print(f"[ARES] team_b: {shape(team_b)}")
    print(f"[ARES] avg_elo={avg_elo} elo_gap={int(elo_gap)}")


async def _notify_match(match_id, map_name, avg_elo, team_a, team_b) -> None:
    payload = {
        "event": "MATCH_FOUND",
        "match_id": str(match_id),
        "map": map_name,
        "avg_elo": avg_elo,
        "team_a": [p["username"] for _, players in team_a for p in players],
        "team_b": [p["username"] for _, players in team_b for p in players],
    }
    for party_id, _ in team_a + team_b:
        await ws_manager.send_to_party(party_id, payload)


async def matchmaker_loop() -> None:
    """Sweep every active region on a fixed tick."""
    while True:
        try:
            async with database.acquire() as conn:
                for region in config.ACTIVE_REGIONS:
                    # Keep matching while this region still yields matches.
                    while await _try_build_match(conn, region):
                        pass
        except Exception as exc:  # keep the engine alive across DB hiccups
            print(f"[Matchmaker Fault] {exc}")
        await asyncio.sleep(config.MATCHMAKER_TICK_SECONDS)
