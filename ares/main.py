"""FastAPI application: HTTP routes, the WebSocket endpoint, and lifespan.

Run with:  uvicorn ares.main:app --reload
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import database, engine
from .models import PartyJoin
from .websocket import ws_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.connect()
    await database.init_schema()
    cleanup_task = asyncio.create_task(engine.queue_cleanup_loop())
    matchmaker_task = asyncio.create_task(engine.matchmaker_loop())
    print("Ares engine, WebSockets and background monitors ready.")
    yield
    cleanup_task.cancel()
    matchmaker_task.cancel()
    # Let the tasks finish unwinding (rolling back any open transaction)
    # before the pool closes, so close() never waits on checked-out conns.
    await asyncio.gather(cleanup_task, matchmaker_task, return_exceptions=True)
    await database.close()


app = FastAPI(title="Ares Matchmaking", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"status": "Ares Online", "engine": "Autonomous Relational Tick"}


@app.post("/join")
async def join_queue(party: PartyJoin):
    """Put a party into the matchmaking queue. Returns its new party_id."""
    party_id = uuid.uuid4()
    await database.join_party(party.players, party_id)
    return {"message": "Party Joined", "party_id": party_id}


@app.websocket("/ws/{party_id}")
async def websocket_endpoint(websocket: WebSocket, party_id: str):
    """One socket per party; receives MATCH_FOUND payloads."""
    await ws_manager.connect(websocket, party_id)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(party_id)
    except Exception:
        ws_manager.disconnect(party_id)


@app.patch("/heartbeat/{party_id}")
async def player_heartbeat(party_id: uuid.UUID):
    """Keep a party's queue entries alive."""
    await database.heartbeat(party_id)
    return {"status": "alive"}


@app.get("/queue")
async def view_queue():
    """Current queue, grouped by party."""
    parties, total = await database.queue_snapshot()
    return {"waiting_parties": parties, "total_count": total}


@app.post("/match/{match_id}/resolve")
async def resolve_match(match_id: uuid.UUID, winner: str):
    """Resolve a match: record the winner and apply Elo stakes."""
    if winner not in ("A", "B"):
        raise HTTPException(status_code=400, detail="Winner must be 'A' or 'B'")
    try:
        existing = await database.get_match_winner(match_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Match not found")
    if existing is not None:
        raise HTTPException(status_code=400, detail="Match already resolved")
    await database.resolve_match(match_id, winner)
    return {"message": f"Match {match_id} resolved. Winners: Team {winner}. Elo updated."}


@app.get("/matches")
async def get_recent_matches(limit: int = 50):
    return await database.recent_matches(limit)


@app.get("/leaderboard")
async def get_leaderboard():
    return {"top_players": await database.leaderboard()}


@app.get("/stats")
async def get_system_stats():
    return await database.system_stats()


@app.get("/analytics/throughput")
async def get_throughput():
    return await database.throughput()
