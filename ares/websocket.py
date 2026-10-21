"""WebSocket connection tracking for party notifications.

One WebSocket per party. When a match is found every party in the match
gets the same MATCH_FOUND payload pushed to its socket.
"""

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, party_id: str) -> None:
        await websocket.accept()
        self.active_connections[party_id] = websocket

    def disconnect(self, party_id: str) -> None:
        self.active_connections.pop(party_id, None)

    async def send_to_party(self, party_id: str, message: dict) -> None:
        websocket = self.active_connections.get(party_id)
        if websocket is not None:
            await websocket.send_json(message)


ws_manager = ConnectionManager()
