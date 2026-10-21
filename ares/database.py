"""PostgreSQL data-access layer (asyncpg).

Every SQL statement in the project lives in this file. The engine and the
HTTP routes call these functions and never build SQL themselves.

Row shape note: queue-candidate rows are plain dicts with at least
player_id, party_id, username, total_elo, region, latency, entered_at.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import asyncpg

from . import config

_pool: asyncpg.Pool | None = None


async def connect(database_url: str | None = None) -> None:
    """Create the global connection pool."""
    global _pool
    _pool = await asyncpg.create_pool(database_url or config.DATABASE_URL)


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    assert _pool is not None, "database.connect() has not been called"
    return _pool


SCHEMA_STATEMENTS = [
    "DROP TABLE IF EXISTS match_participants CASCADE",
    "DROP TABLE IF EXISTS matches CASCADE",
    "DROP TABLE IF EXISTS matchmaking_queue CASCADE",
    "DROP TABLE IF EXISTS maps CASCADE",
    "DROP TABLE IF EXISTS players CASCADE",
    """
    CREATE TABLE players (
        player_id UUID PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        total_elo INTEGER DEFAULT 1200,
        penalty_until TIMESTAMP WITH TIME ZONE DEFAULT NULL
    )
    """,
    """
    CREATE TABLE matchmaking_queue (
        player_id UUID PRIMARY KEY REFERENCES players(player_id),
        party_id UUID NOT NULL,
        region VARCHAR(20) NOT NULL,
        latency INTEGER NOT NULL,
        entered_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE maps (
        map_id SERIAL PRIMARY KEY,
        map_name VARCHAR(50) UNIQUE NOT NULL,
        is_active BOOLEAN DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE matches (
        match_id UUID PRIMARY KEY,
        map_name VARCHAR(50),
        region VARCHAR(20),
        avg_elo INTEGER,
        match_type VARCHAR(10),           -- 'LOCAL' or 'GLOBAL'
        winner CHAR(1) DEFAULT NULL,      -- 'A', 'B', or NULL while ongoing
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE match_participants (
        match_id UUID REFERENCES matches(match_id),
        player_id UUID REFERENCES players(player_id),
        team CHAR(1),
        PRIMARY KEY (match_id, player_id)
    )
    """,
    "CREATE INDEX idx_queue_region ON matchmaking_queue (region, last_seen)",
    "INSERT INTO maps (map_name) VALUES ('Bind'), ('Haven'), ('Split'), ('Ascent') ON CONFLICT DO NOTHING",
]


async def init_schema() -> None:
    """Drop and recreate the full schema. Destroys all existing data."""
    async with pool().acquire() as conn:
        for statement in SCHEMA_STATEMENTS:
            await conn.execute(statement)


# --------------------------------------------------------------------------
# Queue writes
# --------------------------------------------------------------------------

async def join_party(players, party_id: uuid.UUID) -> None:
    """Upsert every player and put the whole party into the queue."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            for player in players:
                player_id = await conn.fetchval(
                    """
                    INSERT INTO players (player_id, username, total_elo)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (username) DO UPDATE SET total_elo = $3
                    RETURNING player_id
                    """,
                    uuid.uuid4(), player.username, player.elo,
                )
                await conn.execute(
                    """
                    INSERT INTO matchmaking_queue (player_id, party_id, region, latency)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (player_id) DO UPDATE SET
                        party_id = $2,
                        last_seen = NOW(),
                        region = $3,
                        latency = $4
                    """,
                    player_id, party_id, player.region, player.latency,
                )


async def heartbeat(party_id: uuid.UUID) -> None:
    async with pool().acquire() as conn:
        await conn.execute(
            "UPDATE matchmaking_queue SET last_seen = NOW() WHERE party_id = $1",
            party_id,
        )


async def purge_stale_players(stale_after_seconds: int) -> int:
    """Delete queue entries whose heartbeat went quiet. Returns rows deleted."""
    async with pool().acquire() as conn:
        status = await conn.execute(
            """
            DELETE FROM matchmaking_queue
            WHERE last_seen < NOW() - make_interval(secs => $1)
            """,
            stale_after_seconds,
        )
    return int(status.split()[-1])


# --------------------------------------------------------------------------
# Matchmaking reads and writes (used by the engine loop)
# --------------------------------------------------------------------------

async def oldest_wait_seconds(region: str, conn=None) -> float | None:
    """Seconds the oldest queue entry in a region has waited. None if empty.

    Pass an open connection to run inside the caller's transaction.
    """
    query = """
        SELECT entered_at FROM matchmaking_queue
        WHERE region = $1 ORDER BY entered_at ASC LIMIT 1
    """
    if conn is not None:
        entered_at = await conn.fetchval(query, region)
    else:
        async with pool().acquire() as owned:
            entered_at = await owned.fetchval(query, region)
    if entered_at is None:
        return None
    return (datetime.now(timezone.utc) - entered_at.replace(tzinfo=timezone.utc)).total_seconds()


async def fetch_queue_candidates(conn, region: str | None, limit: int) -> list[dict]:
    """Fetch queue rows joined with player data, oldest first.

    region=None means a GLOBAL sweep across every region. Must be called
    inside the caller's transaction.
    """
    base = """
        SELECT q.*, p.username, p.total_elo
        FROM matchmaking_queue q
        JOIN players p ON q.player_id = p.player_id
    """
    if region is None:
        rows = await conn.fetch(base + " ORDER BY q.entered_at ASC LIMIT $1", limit)
    else:
        rows = await conn.fetch(
            base + " WHERE q.region = $1 ORDER BY q.entered_at ASC LIMIT $2",
            region, limit,
        )
    return [dict(row) for row in rows]


async def create_match(
    conn,
    match_id: uuid.UUID,
    map_name: str,
    region: str,
    avg_elo: int,
    match_type: str,
    team_a: list[tuple[str, list[dict]]],
    team_b: list[tuple[str, list[dict]]],
) -> None:
    """Record a match, its participants, and clear the queue entries.

    Teams are lists of (party_id, players) pairs. Runs inside the caller's
    transaction so a crash mid-write never leaves a match without its
    participants.
    """
    await conn.execute(
        """
        INSERT INTO matches (match_id, map_name, region, avg_elo, match_type)
        VALUES ($1, $2, $3, $4, $5)
        """,
        match_id, map_name, region, avg_elo, match_type,
    )
    for team, side in ((team_a, "A"), (team_b, "B")):
        for _party_id, players in team:
            for player in players:
                await conn.execute(
                    "INSERT INTO match_participants (match_id, player_id, team) VALUES ($1, $2, $3)",
                    match_id, player["player_id"], side,
                )
    party_ids = [uuid.UUID(party_id) for party_id, _ in team_a + team_b]
    await conn.execute(
        "DELETE FROM matchmaking_queue WHERE party_id = ANY($1::uuid[])",
        party_ids,
    )


async def random_map() -> str | None:
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT map_name FROM maps WHERE is_active = TRUE ORDER BY RANDOM() LIMIT 1"
        )


def acquire():
    """Expose the pool's acquire() so the engine can own a transaction."""
    return pool().acquire()


@asynccontextmanager
async def transaction(conn):
    """One atomic unit of work on an acquired connection."""
    async with conn.transaction():
        yield


# --------------------------------------------------------------------------
# Match resolution
# --------------------------------------------------------------------------

async def get_match_winner(match_id: uuid.UUID):
    """Returns the winner ('A'/'B'/None), or raises KeyError if no such match."""
    async with pool().acquire() as conn:
        row = await conn.fetchrow("SELECT winner FROM matches WHERE match_id = $1", match_id)
    if row is None:
        raise KeyError(str(match_id))
    return row["winner"]


async def resolve_match(match_id: uuid.UUID, winner: str) -> None:
    """Set the winner and apply Elo stakes to every participant."""
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE matches SET winner = $1 WHERE match_id = $2", winner, match_id)
            participants = await conn.fetch(
                "SELECT player_id, team FROM match_participants WHERE match_id = $1", match_id
            )
            for participant in participants:
                delta = config.ELO_WIN_DELTA if participant["team"] == winner else config.ELO_LOSS_DELTA
                await conn.execute(
                    "UPDATE players SET total_elo = total_elo + $1 WHERE player_id = $2",
                    delta, participant["player_id"],
                )


# --------------------------------------------------------------------------
# Read-only reporting endpoints
# --------------------------------------------------------------------------

async def queue_snapshot() -> tuple[dict, int]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT q.party_id, p.username, p.total_elo, q.latency, q.region, q.entered_at
            FROM matchmaking_queue q
            JOIN players p ON q.player_id = p.player_id
            ORDER BY q.entered_at ASC
            """
        )
    parties: dict[str, list[dict]] = {}
    for row in rows:
        entry = dict(row)
        entry["party_id"] = str(entry["party_id"])
        entry["entered_at"] = entry["entered_at"].isoformat()
        parties.setdefault(entry["party_id"], []).append(entry)
    return parties, len(rows)


async def recent_matches(limit: int = 50) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT match_id, map_name AS map, region, avg_elo, match_type, winner, created_at
            FROM matches ORDER BY created_at DESC LIMIT $1
            """,
            limit,
        )
    matches = []
    for row in rows:
        match = dict(row)
        match["match_id"] = str(match["match_id"])
        match["created_at"] = match["created_at"].isoformat()
        matches.append(match)
    return matches


async def leaderboard(limit: int = 10) -> list[dict]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT username, total_elo FROM players ORDER BY total_elo DESC LIMIT $1", limit
        )
    return [dict(row) for row in rows]


async def system_stats() -> dict:
    async with pool().acquire() as conn:
        queue_count = await conn.fetchval("SELECT count(*) FROM matchmaking_queue")
        matches_today = await conn.fetchval(
            "SELECT count(*) FROM matches WHERE created_at > NOW() - INTERVAL '24 hours'"
        )
        matched_players = await conn.fetchval(
            "SELECT COUNT(DISTINCT player_id) FROM match_participants"
        )
        total_players = await conn.fetchval("SELECT COUNT(*) FROM players")

    success_rate = round(matched_players / total_players * 100, 1) if total_players else 0
    engine_load = min(int(queue_count), 100)
    return {
        "active_queue": queue_count,
        "matches_today": matches_today,
        "success_rate": f"{success_rate}%",
        "engine_load": f"{engine_load}%",
    }


async def throughput() -> dict:
    async with pool().acquire() as conn:
        by_minute = await conn.fetch(
            """
            SELECT to_char(created_at, 'HH24:MI') AS time, count(*) AS matches
            FROM matches
            WHERE created_at > NOW() - INTERVAL '30 minutes'
            GROUP BY time ORDER BY time ASC
            """
        )
        by_region = await conn.fetch(
            "SELECT region AS name, count(*) AS value FROM matches GROUP BY region"
        )
    return {
        "throughput": [dict(row) for row in by_minute],
        "regions": [dict(row) for row in by_region],
    }
