# Ares Matchmaking (PostgreSQL)

A real-time 5v5 matchmaking backend: FastAPI, PostgreSQL (asyncpg) and
WebSockets, with Elo-based team balancing, party support and live
MATCH_FOUND notifications.

This is a readability-focused refactor of
[AayushK29/ares_engine](https://github.com/AayushK29/ares_engine)
(original authors: Aayush Kanade, Mahesh Thakur, Ankur Raj), reorganized
into modules with the SQL isolated in one data-access layer, the
matchmaking logic made pure and unit-testable, and a real test suite added.
A MySQL port of the same codebase lives at
[instinctsetup/ares-matchmaking-mysql](https://github.com/instinctsetup/ares-matchmaking-mysql).

## What it does

- Parties of 1-5 players join a region queue (`POST /join`)
- A background engine sweeps the queue every few seconds, packs parties
  into two exact 5-player teams (subset-sum over party sizes) and creates a
  match when the teams' average Elos are within a threshold
- The threshold relaxes the longer the oldest player waits; after 60s the
  sweep goes cross-region (GLOBAL)
- Every party in a match gets a MATCH_FOUND push over its WebSocket
  (`/ws/{party_id}`)
- Heartbeats keep queue entries alive; a reaper purges stale ones
- Match resolution (`POST /match/{id}/resolve`) records the winner and
  applies Elo stakes (+25 / -20)
- Reporting: `/queue`, `/matches`, `/leaderboard`, `/stats`,
  `/analytics/throughput`

## Project layout

```
ares/
  config.py        all tunables and the DATABASE_URL
  models.py        Pydantic request models
  database.py      every SQL statement (asyncpg data-access layer)
  matchmaking.py   pure logic: party grouping, team packing, Elo math
  engine.py        background loops: matchmaker + heartbeat reaper
  websocket.py     per-party WebSocket registry
  main.py          FastAPI app, routes, lifespan
tests/
  test_matchmaking_logic.py   pure unit tests, no database
  test_api.py                 end-to-end tests against a scratch database
scripts/
  stress_test.py   queue load injector
```

## Setup

```bash
pip install -r requirements.txt
createdb ares_db   # or: CREATE DATABASE ares_db;
export DATABASE_URL=postgresql://ares_user:ares_password@localhost:5432/ares_db
uvicorn ares.main:app --reload
```

The app (re)creates its schema on startup - point it at a scratch database,
not a production one. Optional load test:

```bash
python scripts/stress_test.py            # injects 50 random parties
```

## Tests

```bash
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql://user@localhost/ares_test   # scratch DB
pytest
```

15 tests: pure matchmaking-logic units plus end-to-end API tests that join
10 players, watch a real match form, resolve it and check Elo changes.
The suite is synchronous (starlette TestClient); pytest.ini disables
pytest-asyncio, which interferes with TestClient teardown if installed.

## Behaviour changes vs the original

Only one deliberate fix: rejoining the queue now also updates the player's
`party_id` (the original kept the stale party id). Everything else is a
reorganization: same endpoints, same algorithm, same thresholds - plus a
committed-`__pycache__` cleanup and the missing `requirements.txt`.
Known original quirks kept as-is: a GLOBAL match is still recorded under
the region whose sweep created it.

## Credits

Original project: [AayushK29/ares_engine](https://github.com/AayushK29/ares_engine)
by Aayush Kanade, Mahesh Thakur and Ankur Raj (April 2026).
