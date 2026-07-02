"""
Multi-sport prediction registry + pure row builder.

One generic recorder covers every head-to-head sport: seed a 2-way Elo (`lib.elo`)
from recent ESPN results, predict each upcoming fixture, and emit `model_predictions`
rows. Adding a sport = one line in SPORTS (the schema is model-agnostic, so nothing
else changes). Golf is NOT here — it's a field/winner model, handled separately.

For MLB a CONTEXT VARIANT (`elo-mlb-ctx`) is recorded alongside the base model:
same Elo, plus a rest-days adjustment, with the probable starting pitchers captured
in meta. It's tracked-only — live betting stays on the proven base model until the
promotion gate shows the variant's Brier beats it (validate-first).

`build_sport_rows` is pure (recent + fixtures injected) so it's unit-tested without
the network; the worker supplies real ESPN data via `core.espnfeed`.
"""
import math

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


def rest_days(recent: list[dict], team: str, fixture_date: str) -> int | None:
    """Days between a team's most recent completed game and the fixture (schedule
    context: fatigue/rotation). None when the team has no recent game."""
    from datetime import date
    try:
        fd = date.fromisoformat((fixture_date or "")[:10])
    except Exception:
        return None
    last = None
    for m in recent:
        if team in (m.get("home"), m.get("away")):
            try:
                d = date.fromisoformat((m.get("date") or "")[:10])
            except Exception:
                continue
            if last is None or d > last:
                last = d
    return (fd - last).days if last else None


def ctx_adjust(p: float, rest_h, rest_a, k: float = 0.05, cap: float = 3.0) -> float:
    """Rest-days context adjustment in logit space: shift the Elo prob by
    k per rest-day differential, clipped to ±cap days. Small by design — the
    variant must EARN a bigger k from tracked results."""
    if rest_h is None or rest_a is None or not 0 < p < 1:
        return p
    diff = max(-cap, min(cap, float(rest_h - rest_a)))
    z = math.log(p / (1 - p)) + k * diff
    return 1 / (1 + math.exp(-z))


def build_sport_rows(key: str, espn_path: str, neutral_sport: bool,
                     recent: list[dict], fixtures: list[dict],
                     today_iso: str) -> list[dict]:
    """Seed Elo from `recent`, predict `fixtures`, return prediction rows (2 per
    match: home/away). For MLB also emits the `elo-mlb-ctx` variant rows
    (rest-adjusted prob; probable pitchers in meta). Pure — no network."""
    rater = elo.Elo(neutral=neutral_sport).seed(recent)
    rows = []
    for fx in fixtures:
        ph, pa = rater.win_probs(fx["home"], fx["away"], fx.get("neutral", False))
        sdate = (fx["date"] or "")[:10] or today_iso
        meta = {"sport": key, "espn_path": espn_path, "espn_id": fx["id"],
                "home": fx["home_raw"], "away": fx["away_raw"],
                "home_abbr": fx.get("home_abbr", ""),
                "away_abbr": fx.get("away_abbr", ""),
                "r_home": round(rater.rating(fx["home"]), 1),
                "r_away": round(rater.rating(fx["away"]), 1),
                "neutral": fx.get("neutral", False),
                "kickoff": fx["date"], "run_date": today_iso}
        variants = [(f"elo-{key}", ph, pa, meta)]
        if key == "mlb":
            rh = rest_days(recent, fx["home"], fx["date"])
            ra = rest_days(recent, fx["away"], fx["date"])
            ph_c = ctx_adjust(ph, rh, ra)
            ctx_meta = dict(meta, rest_home=rh, rest_away=ra,
                            sp_home=fx.get("home_pitcher", ""),
                            sp_away=fx.get("away_pitcher", ""))
            variants.append((f"elo-{key}-ctx", ph_c, 1 - ph_c, ctx_meta))
        for model, p_h, p_a, m_meta in variants:
            for outcome, prob in (("home", p_h), ("away", p_a)):
                rows.append({
                    "model": model, "sport": key,
                    "market_slug": f"espn:{key}:{fx['id']}:{outcome}",
                    "outcome": outcome, "model_prob": round(prob, 4),
                    "market_bid": None, "market_ask": None, "edge": None, "liquid": None,
                    "settle_date": sdate, "run_date": today_iso,
                    "meta": m_meta,
                })
    return rows
