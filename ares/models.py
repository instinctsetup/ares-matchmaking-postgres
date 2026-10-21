"""Pydantic request/response models for the HTTP and WebSocket API."""

from typing import List

from pydantic import BaseModel


class Player(BaseModel):
    """One player joining the queue as part of a party."""

    username: str
    elo: int
    latency: int
    region: str


class PartyJoin(BaseModel):
    """POST /join body: a party of 1-5 players queueing together."""

    players: List[Player]
