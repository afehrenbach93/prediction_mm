"""
Multi-sport prediction registry + pure row builder.

One generic recorder covers every head-to-head sport: seed a 2-way Elo (`lib.elo`)
from recent ESPN results, predict each upcoming fixture, and emit `model_predictions`
rows. Adding a sport = one line in SPORTS (the schema is model-agnostic, so nothing
else changes). Golf is NOT here — it's a field/winner model, handled separately.

`build_sport_rows` is pure (recent + fixtures injected) so it's unit-tested without
the network; the worker supplies real ESPN data via `core.espnfeed`.
"""
from lib import elo

# key -> (ESPN sport_path, neutral_site). neutral=True disables home advantage
# (tennis is player-vs-player at neutral venues).
SPORTS = {
    "nba":   ("basketball/nba", False),
    "nfl":   ("football/nfl", False),
    "ncaaf": ("football/college-football", False),
    "mlb":   ("baseball/mlb", False),
    "atp":   ("tennis/atp", True),
    "wta":   ("tennis/wta", True),
}


def build_sport_rows(key: str, espn_path: str, neutral_sport: bool,
                     recent: list[dict], fixtures: list[dict],
                     today_iso: str) -> list[dict]:
    """Seed Elo from `recent`, predict `fixtures`, return prediction rows (2 per
    match: home/away). Pure — no network."""
    rater = elo.Elo(neutral=neutral_sport).seed(recent)
    rows = []
    for fx in fixtures:
        ph, pa = rater.win_probs(fx["home"], fx["away"], fx.get("neutral", False))
        sdate = (fx["date"] or "")[:10] or today_iso
        for outcome, prob in (("home", ph), ("away", pa)):
            rows.append({
                "model": f"elo-{key}", "sport": key,
                "market_slug": f"espn:{key}:{fx['id']}:{outcome}",
                "outcome": outcome, "model_prob": round(prob, 4),
                "market_bid": None, "market_ask": None, "edge": None, "liquid": None,
                "settle_date": sdate, "run_date": today_iso,
                "meta": {"sport": key, "espn_path": espn_path, "espn_id": fx["id"],
                         "home": fx["home_raw"], "away": fx["away_raw"],
                         "r_home": round(rater.rating(fx["home"]), 1),
                         "r_away": round(rater.rating(fx["away"]), 1),
                         "neutral": fx.get("neutral", False),
                         "kickoff": fx["date"], "run_date": today_iso},
            })
    return rows
