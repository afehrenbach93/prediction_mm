"""
Settlement — resolve recorded predictions against realized outcomes.

Pure logic with the realized-data fetchers INJECTED, so scoring is unit-tested with
no network. The worker passes real fetchers (`wxfeed.daily_high_observed`,
`soccerfeed.finals_map`); tests pass stubs.

Each resolved row yields (realized_yes, pnl): realized_yes = did this outcome occur;
pnl = the P&L of having BOUGHT YES at the recorded ask ((1 if yes else 0) − ask), or
None when no market price was recorded (e.g. soccer, price-less for now). Rows whose
outcome can't be determined yet (no data) are skipped — left unsettled to retry.
"""
from lib.weather import parse_temp_slug


def _pnl(realized_yes: bool, ask) -> float | None:
    if ask is None:
        return None
    return (1.0 if realized_yes else 0.0) - float(ask)


def settle_weather(rows: list[dict], fetch_high) -> dict[int, tuple[bool, float | None]]:
    """Resolve weather buckets. `fetch_high(station, date) -> float|None` (realized
    daily high °F). realized_yes = lo <= high < hi (open ends = tail). Returns
    {prediction_id: (realized_yes, pnl)} for rows that could be settled."""
    out, cache = {}, {}
    for r in rows:
        p = parse_temp_slug(r.get("market_slug", ""))
        if not p:
            continue
        key = (p["station"], p["date"])
        if key not in cache:
            cache[key] = fetch_high(p["station"], p["date"])
        high = cache[key]
        if high is None:
            continue   # no realized data yet — leave unsettled
        ry = ((p["lo"] is None or high >= p["lo"]) and
              (p["hi"] is None or high < p["hi"]))
        out[r["id"]] = (ry, _pnl(ry, r.get("market_ask")))
    return out


def settle_sport(rows: list[dict], fetch_finals) -> dict[int, tuple[bool, float | None]]:
    """Resolve 2-way (win/loss) sport rows — NBA/NFL/NCAAF/MLB/tennis. `fetch_finals(
    espn_path, dateYYYYMMDD) -> {espn_id: match}`. realized_yes = (row.outcome == the
    winner home/away). Ties (rare) are skipped (no 2-way winner). Returns
    {prediction_id: (realized_yes, pnl)} for completed matches only."""
    out, cache = {}, {}
    for r in rows:
        meta = r.get("meta") or {}
        path, eid = meta.get("espn_path"), meta.get("espn_id")
        date = (r.get("settle_date") or "")
        if not path or not eid or not date:
            continue
        key = (path, date)
        if key not in cache:
            cache[key] = fetch_finals(path, date.replace("-", ""))
        match = (cache[key] or {}).get(str(eid))
        if not match:
            continue
        hs, as_ = match["home_score"], match["away_score"]
        if hs == as_:
            continue   # tie — no 2-way winner; leave unsettled
        winner = "home" if hs > as_ else "away"
        ry = (r.get("outcome") == winner)
        out[r["id"]] = (ry, _pnl(ry, r.get("market_ask")))
    return out


def settle_golf(rows: list[dict], fetch_winners) -> dict[int, tuple[bool, float | None]]:
    """Resolve golf 'win' rows. `fetch_winners(dateYYYYMMDD) -> {tourney_id: winner}`.
    realized_yes = (this row's player == the tournament winner). Resolves only once the
    tournament is final (winner present). {prediction_id: (realized_yes, pnl)}."""
    out, cache = {}, {}
    for r in rows:
        meta = r.get("meta") or {}
        tid, player = meta.get("tourney_id"), meta.get("player")
        date = (r.get("settle_date") or "")
        if not tid or not player or not date:
            continue
        if date not in cache:
            cache[date] = fetch_winners(date.replace("-", ""))
        winner = (cache[date] or {}).get(str(tid))
        if not winner:
            continue   # not final yet
        ry = (player == winner)
        out[r["id"]] = (ry, _pnl(ry, r.get("market_ask")))
    return out


def settle_soccer(rows: list[dict], fetch_finals) -> dict[int, tuple[bool, float | None]]:
    """Resolve soccer 1X2 rows. `fetch_finals(league, date) -> {espn_id: match}` with
    home_score/away_score. realized_yes = (row.outcome == actual winner home/draw/away).
    Returns {prediction_id: (realized_yes, pnl)} for completed matches only."""
    out, cache = {}, {}
    for r in rows:
        meta = r.get("meta") or {}
        league, eid = meta.get("league"), meta.get("espn_id")
        date = (r.get("settle_date") or "")
        if not league or not eid or not date:
            continue
        key = (league, date)
        if key not in cache:
            cache[key] = fetch_finals(league, date.replace("-", ""))
        match = (cache[key] or {}).get(str(eid))
        if not match:
            continue   # not final yet (or not on this date) — retry later
        hs, as_ = match["home_score"], match["away_score"]
        winner = "home" if hs > as_ else ("away" if as_ > hs else "draw")
        ry = (r.get("outcome") == winner)
        out[r["id"]] = (ry, _pnl(ry, r.get("market_ask")))
    return out
