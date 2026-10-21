"""Unit tests for the pure matchmaking logic. No database needed."""

from ares import config, matchmaking


def make_players(party_id: str, size: int, elo: int = 1200) -> tuple[str, list[dict]]:
    return party_id, [
        {"player_id": f"{party_id}-{i}", "party_id": party_id, "username": f"u{party_id}{i}",
         "total_elo": elo, "region": "IN", "latency": 20}
        for i in range(size)
    ]


class TestGroupIntoParties:
    def test_groups_by_party_id(self):
        rows = [p for party in (make_players("a", 2), make_players("b", 3)) for p in party[1]]
        parties = dict(matchmaking.group_into_parties(rows))
        assert set(parties) == {"a", "b"}
        assert len(parties["a"]) == 2 and len(parties["b"]) == 3

    def test_empty_queue(self):
        assert matchmaking.group_into_parties([]) == []


class TestFormTeam:
    def test_exact_combination(self):
        parties = [make_players("a", 2), make_players("b", 3), make_players("c", 4)]
        team = matchmaking.form_team(parties, team_size=5)
        assert team is not None
        assert sum(len(players) for _, players in team) == 5

    def test_prefers_earliest_parties(self):
        parties = [make_players("a", 5), make_players("b", 2), make_players("c", 3)]
        team = matchmaking.form_team(parties, team_size=5)
        assert [party_id for party_id, _ in team] == ["a"]

    def test_returns_none_when_no_exact_fit(self):
        parties = [make_players("a", 4), make_players("b", 4)]
        assert matchmaking.form_team(parties, team_size=5) is None

    def test_five_solos_make_a_team(self):
        parties = [make_players(str(i), 1) for i in range(5)]
        team = matchmaking.form_team(parties, team_size=5)
        assert team is not None and len(team) == 5


class TestTeamAverageElo:
    def test_average(self):
        team = [make_players("a", 2, 1000)[1], make_players("b", 3, 1400)[1]]
        assert matchmaking.team_average_elo(team) == (2 * 1000 + 3 * 1400) / 5

    def test_empty_team_is_zero(self):
        assert matchmaking.team_average_elo([]) == 0


class TestEloThreshold:
    def test_starts_at_base(self):
        assert matchmaking.elo_threshold(0) == config.ELO_THRESHOLD_BASE

    def test_relaxes_with_wait(self):
        waited = 3 * config.ELO_THRESHOLD_RELAX_EVERY_SECONDS
        expected = config.ELO_THRESHOLD_BASE + 3 * config.ELO_THRESHOLD_STEP
        assert matchmaking.elo_threshold(waited) == expected
