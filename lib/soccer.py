"""
Soccer model core — Elo ratings -> 1X2 (home/draw/away) probabilities.

Pure functions, no network. The worker seeds ratings from recent results (a feed)
and supplies the two teams + home-field advantage; these functions produce a
probability per outcome that we compare to the Polymarket soccer market price to
find edges. Validate-first: backtest predictions vs realized results + market
before funding — a lightweight Elo will likely reproduce efficient lines, so the
edge (if any) lives in neglected leagues, not the majors.

Elo: expected home score E = 1 / (1 + 10^((R_away - R_home - HFA)/400)), where E
counts a draw as half. Update after a match: R += K * (actual - expected).
"""

HFA_DEFAULT = 60.0     # home-field advantage in Elo points (~tunable per league)
K_DEFAULT = 20.0       # Elo update speed
DRAW_SCALE = 0.30      # max draw probability (when teams are evenly matched)


def expected_score(r_home: float, r_away: float, hfa: float = HFA_DEFAULT) -> float:
    """Elo expected score for the HOME side (0..1, draw counts as 0.5)."""
    return 1.0 / (1.0 + 10.0 ** ((r_away - r_home - hfa) / 400.0))


def update_elo(rating: float, expected: float, actual: float,
               k: float = K_DEFAULT) -> float:
    """New rating after a match. actual = 1 win / 0.5 draw / 0 loss (that side's)."""
    return rating + k * (actual - expected)


def match_probabilities(r_home: float, r_away: float, hfa: float = HFA_DEFAULT,
                        draw_scale: float = DRAW_SCALE):
    """(p_home, p_draw, p_away), summing to 1. Draw probability peaks when the teams
    are evenly matched and shrinks with the rating gap; the remainder is split in the
    ratio implied by the Elo expectation. A deliberately simple v1 to be calibrated
    against realized results, not a finished model."""
    e = expected_score(r_home, r_away, hfa)              # home expectation, 0..1
    p_draw = draw_scale * (1.0 - abs(2.0 * e - 1.0))     # peaks at e=0.5
    rest = 1.0 - p_draw
    p_home = rest * e
    p_away = rest * (1.0 - e)
    return p_home, p_draw, p_away


def buy_edge(model_prob: float, market_ask: float | None, fee: float = 0.0) -> float | None:
    """Edge from buying that outcome at the market ask, net of fee. None if no ask."""
    if market_ask is None:
        return None
    return model_prob - market_ask - fee


class EloTable:
    """Minimal rolling Elo table seeded from a results stream. New teams start at
    `base`. Feed it (home, away, home_goals, away_goals) in chronological order."""
    def __init__(self, base: float = 1500.0, k: float = K_DEFAULT,
                 hfa: float = HFA_DEFAULT):
        self.base, self.k, self.hfa = base, k, hfa
        self.r: dict[str, float] = {}

    def rating(self, team: str) -> float:
        return self.r.get(team, self.base)

    def observe(self, home: str, away: str, hg: int, ag: int):
        rh, ra = self.rating(home), self.rating(away)
        e_home = expected_score(rh, ra, self.hfa)
        a_home = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        self.r[home] = update_elo(rh, e_home, a_home, self.k)
        self.r[away] = update_elo(ra, 1.0 - e_home, 1.0 - a_home, self.k)

    def probabilities(self, home: str, away: str):
        return match_probabilities(self.rating(home), self.rating(away), self.hfa)
