"""Load injector: drops 50 random parties into the queue.

Run the server first (uvicorn ares.main:app), then:
    python scripts/stress_test.py [base_url]
"""

import random
import sys
import time

import requests

API_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def run_stress_test(party_count: int = 50) -> None:
    total_players = 0
    for _ in range(party_count):
        party_size = random.randint(1, 5)
        players = [
            {
                "username": f"Bot_{random.randint(1000, 9999)}_{time.time_ns() % 100000}",
                "elo": 1100,
                "latency": random.randint(10, 50),
                "region": "IN",
            }
            for _ in range(party_size)
        ]
        try:
            requests.post(f"{API_URL}/join", json={"players": players}, timeout=5)
            total_players += party_size
            print(f"Injected party of {party_size} (total players: {total_players})")
        except Exception as exc:
            print(f"Failed to add party: {exc}")
    print(f"Injection complete. {total_players} players queued.")


if __name__ == "__main__":
    run_stress_test()
