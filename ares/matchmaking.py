"""Pure matchmaking logic: no database, no asyncio, fully unit-testable.

The engine in engine.py fetches queue rows from the database and hands
them here as plain dicts; everything below is deterministic and side-effect
free.
"""

from collections import defaultdict
from typing import Optional

from . import config


def group_into_parties(queue_rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group queue rows by party_id.

    A party is indivisible: its players always land on the same team.
    Returns a list of (party_id, players) pairs.
    """
    parties: dict[str, list[dict]] = defaultdict(list)
    for row in queue_rows:
        parties[str(row["party_id"])].append(row)
    return list(parties.items())


def form_team(
    available_parties: list[tuple[str, list[dict]]],
    team_size: int = config.TEAM_SIZE,
) -> Optional[list[tuple[str, list[dict]]]]:
    """Pick a subset of parties whose sizes sum exactly to team_size.

    Classic subset-sum over party sizes ("bin packing" the team slots).
    Returns the chosen (party_id, players) pairs, or None when no exact
    combination exists yet.
    """

    def backtrack(index: int, size: int, team: list) -> Optional[list]:
        if size == team_size:
            return team
        if size > team_size or index >= len(available_parties):
            return None
        party_id, players = available_parties[index]
        with_party = backtrack(index + 1, size + len(players), team + [(party_id, players)])
        if with_party is not None:
            return with_party
        return backtrack(index + 1, size, team)

    return backtrack(0, 0, [])


def team_average_elo(team: list[list[dict]]) -> float:
    """Average Elo over every player in a team (a list of party player-lists)."""
    players = [player for party in team for player in party]
    if not players:
        return 0.0
    return sum(p["total_elo"] for p in players) / len(players)


def elo_threshold(wait_seconds: float) -> float:
    """Allowed Elo gap between the two teams.

    Starts at ELO_THRESHOLD_BASE and relaxes by ELO_THRESHOLD_STEP for every
    ELO_THRESHOLD_RELAX_EVERY_SECONDS the oldest player has waited, so long
    waits trade match quality for match speed.
    """
    steps = int(wait_seconds // config.ELO_THRESHOLD_RELAX_EVERY_SECONDS)
    return config.ELO_THRESHOLD_BASE + steps * config.ELO_THRESHOLD_STEP
