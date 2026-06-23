"""
Generic 2-way Elo (win/loss) for head-to-head sports — NBA, NFL, NCAA FB, MLB, and
tennis (player-vs-player). Pure, no I/O — fully unit-tested.

Same Elo core as `lib.soccer`, minus the draw split: these sports decide a winner
every game. Home-field advantage applies to team sports (`hfa`), and is disabled for
neutral-site games and tennis (`neutral=True`). Seed ratings from recent results,
then `win_probs` gives (P_home/first, P_away/second) for an upcoming match.

Goal-difference (or margin) is intentionally NOT used as a multiplier here — across
six sports with wildly different scoring scales (a 3-run MLB game vs a 30-point NBA
game) a shared margin term would mis-weight; a plain win/loss update generalizes
cleanly. Per-sport refinements come after the tracker shows where skill is lacking.
"""
from dataclasses import dataclass, field

BASE_RATING = 1500.0
K_FACTOR = 20.0
HFA_DEFAULT = 65.0    # Elo points of home advantage for team sports (~0.59 even game)


def expected_score(r_a: float, r_b: float, hfa: float = 0.0) -> float:
    """Elo win expectation for side A (the home/first side), [0,1]."""
    return 1.0 / (1.0 + 10 ** (-((r_a + hfa) - r_b) / 400.0))


def update(r_a: float, r_b: float, a_won: bool, k: float = K_FACTOR,
           hfa: float = 0.0) -> tuple[float, float]:
    """Updated (A, B) ratings after a decided game (zero-sum)."""
    e_a = expected_score(r_a, r_b, hfa)
    s_a = 1.0 if a_won else 0.0
    delta = k * (s_a - e_a)
    return r_a + delta, r_b - delta


@dataclass
class Elo:
    """Rating table for one sport, keyed by normalized team/player name."""
    hfa: float = HFA_DEFAULT
    k: float = K_FACTOR
    neutral: bool = False        # tennis / neutral-site: no home advantage
    ratings: dict[str, float] = field(default_factory=dict)

    def rating(self, name: str) -> float:
        return self.ratings.get(name, BASE_RATING)

    def _hfa(self, neutral: bool) -> float:
        return 0.0 if (self.neutral or neutral) else self.hfa

    def observe(self, home: str, away: str, home_score: int, away_score: int,
                neutral: bool = False) -> None:
        """Feed one finished game (ties ignored — rare in these sports)."""
        if home_score == away_score:
            return
        rh, ra = self.rating(home), self.rating(away)
        nh, na = update(rh, ra, home_score > away_score, self.k, self._hfa(neutral))
        self.ratings[home], self.ratings[away] = nh, na

    def seed(self, results: list[dict]) -> "Elo":
        for m in results:
            self.observe(m["home"], m["away"], int(m["home_score"]),
                         int(m["away_score"]), m.get("neutral", False))
        return self

    def win_probs(self, home: str, away: str,
                  neutral: bool = False) -> tuple[float, float]:
        """(P_home/first, P_away/second) for an upcoming match. Sums to 1."""
        e = expected_score(self.rating(home), self.rating(away), self._hfa(neutral))
        return e, 1.0 - e
