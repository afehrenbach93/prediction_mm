"""
Generic ESPN scoreboard feed for ALL sports (no key). One parser, every sport.

Network module (stdlib only). Runs on the Render worker — this dev sandbox is
egress-allowlisted away from sports APIs. `fetch` does the I/O; `parse_scoreboard`
is pure so the shape handling is unit-tested without the network.

ESPN's scoreboard JSON is consistent across sports: each event has a competition
with two competitors that carry either a `team` (NBA/NFL/MLB/NCAA/soccer) or an
`athlete` (tennis). We extract a normalized name + score + state for both, so the
same feed seeds Elo and lists fixtures for any head-to-head sport.

`sport_path` is ESPN's '<sport>/<league>' segment, e.g. 'basketball/nba',
'football/nfl', 'baseball/mlb', 'football/college-football', 'tennis/atp',
'soccer/fifa.world'.
"""
import json
import urllib.request

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"


def normalize_name(name: str) -> str:
    """Stable key for a team/player across calls (lowercased, whitespace-collapsed)."""
    return " ".join((name or "").split()).lower()


def _competitor_name(c: dict) -> str:
    """ESPN puts a team OR an athlete on a competitor depending on the sport."""
    team = c.get("team") or {}
    if team.get("displayName"):
        return team["displayName"]
    ath = c.get("athlete") or {}
    return ath.get("displayName") or ath.get("shortName") or ""


def parse_scoreboard(raw: dict) -> list[dict]:
    """Pure: ESPN scoreboard JSON -> list of match dicts. Each:
    {id, date, state(pre|in|post), completed, neutral, home, away, home_score,
     away_score, home_raw, away_raw}. Scores int when present, else None. 'home' is
     the home/first competitor; neutral-site games still have a nominal home/away."""
    def _score(c):
        v = c.get("score")
        if isinstance(v, dict):              # tennis nests score under {value/displayValue}
            v = v.get("value")
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    out = []
    for ev in (raw or {}).get("events", []) or []:
        # team sports put the match at events[].competitions[]; tennis nests each
        # match under events[].groupings[].competitions[] (per-tournament grouping).
        comps = list(ev.get("competitions") or [])
        for g in (ev.get("groupings") or []):
            comps.extend(g.get("competitions") or [])
        for comp in comps:
            cs = comp.get("competitors") or []
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                if len(cs) == 2:             # tennis omits homeAway — first two players
                    home, away = cs[0], cs[1]
                else:
                    continue
            # status/date can live on the competition (tennis) or the event (teams)
            status = ((comp.get("status") or {}).get("type")
                      or (ev.get("status") or {}).get("type") or {})
            hr, ar = _competitor_name(home), _competitor_name(away)
            if not hr or not ar:
                continue
            out.append({
                "id": str(comp.get("id") or ev.get("id", "")),
                "date": comp.get("date") or ev.get("date", ""),
                "state": status.get("state", ""),
                "completed": bool(status.get("completed", False)),
                "neutral": bool(comp.get("neutralSite", False)),
                "home": normalize_name(hr), "away": normalize_name(ar),
                "home_raw": hr, "away_raw": ar,
                "home_score": _score(home), "away_score": _score(away),
            })
    return out


def fetch(sport_path: str, dates: str | None = None) -> list[dict]:
    """Fetch + parse one sport/league's scoreboard. `dates`: ESPN 'YYYYMMDD' or
    'YYYYMMDD-YYYYMMDD'. Returns [] on failure."""
    url = SCOREBOARD.format(path=sport_path)
    if dates:
        url += f"?dates={dates}&limit=400"
    req = urllib.request.Request(url, headers={"User-Agent": "prediction-mm/espn"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return parse_scoreboard(json.loads(r.read()))
    except Exception:
        return []


def recent_results(sport_path: str, dates: str | None = None) -> list[dict]:
    """Finished matches (completed, both scores present) — chronological, for Elo."""
    ms = [m for m in fetch(sport_path, dates)
          if m["completed"] and m["home_score"] is not None
          and m["away_score"] is not None]
    ms.sort(key=lambda m: m["date"])
    return ms


def upcoming_fixtures(sport_path: str, dates: str | None = None) -> list[dict]:
    """Not-yet-started matches (state 'pre') to predict."""
    ms = [m for m in fetch(sport_path, dates) if m["state"] == "pre"]
    ms.sort(key=lambda m: m["date"])
    return ms


def finals_map(sport_path: str, dates: str | None = None) -> dict:
    """{espn_id: match} for COMPLETED matches — settlement lookup by id."""
    return {m["id"]: m for m in fetch(sport_path, dates)
            if m["completed"] and m["home_score"] is not None
            and m["away_score"] is not None}
