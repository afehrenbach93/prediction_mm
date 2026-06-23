"""
Soccer results/fixtures feed for the Elo model (`lib.soccer`).

Network module (stdlib only). Runs on the Render worker — this dev sandbox is
egress-allowlisted away from sports APIs. Source: ESPN's public scoreboard
(no key). `fetch_scoreboard` does the I/O; `parse_scoreboard` is pure so the
shape handling is unit-tested without the network.

Two uses: seed Elo from recent finished matches (`recent_results`), and list
upcoming fixtures to predict (`upcoming_fixtures`). Team names are normalized so
the same club keys consistently across calls.
"""
import json
import urllib.request

# friendly league key -> ESPN soccer league slug. Extend as Polymarket adds venues.
LEAGUES = {
    "wc": "fifa.world",        # FIFA World Cup (2026 — the active reward driver)
    "epl": "eng.1",            # English Premier League
    "mls": "usa.1",            # Major League Soccer
    "laliga": "esp.1",
    "seriea": "ita.1",
    "bundesliga": "ger.1",
    "ligue1": "fra.1",
    "ucl": "uefa.champions",
}
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard"


def normalize_team(name: str) -> str:
    """Stable key for a club/nation across calls (lowercased, trimmed)."""
    return " ".join((name or "").split()).lower()


def parse_scoreboard(raw: dict) -> list[dict]:
    """Pure: ESPN scoreboard JSON -> list of match dicts. Each:
    {id, date, state(pre|in|post), completed, home, away, home_score, away_score,
     home_raw, away_raw}. Scores are int when present (post/in), else None."""
    out = []
    for ev in (raw or {}).get("events", []) or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        cs = comps[0].get("competitors") or []
        home = next((c for c in cs if c.get("homeAway") == "home"), None)
        away = next((c for c in cs if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        status = (ev.get("status") or {}).get("type") or {}

        def _score(c):
            v = c.get("score")
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        hr = (home.get("team") or {}).get("displayName", "")
        ar = (away.get("team") or {}).get("displayName", "")
        out.append({
            "id": str(ev.get("id", "")),
            "date": ev.get("date", ""),
            "state": status.get("state", ""),
            "completed": bool(status.get("completed", False)),
            "home": normalize_team(hr), "away": normalize_team(ar),
            "home_raw": hr, "away_raw": ar,
            "home_score": _score(home), "away_score": _score(away),
        })
    return out


def fetch_scoreboard(league: str, dates: str | None = None) -> list[dict]:
    """Fetch + parse one league's scoreboard. `dates`: ESPN 'YYYYMMDD' or
    'YYYYMMDD-YYYYMMDD' range; None = current. Returns [] on failure."""
    slug = LEAGUES.get(league, league)
    url = SCOREBOARD.format(slug=slug)
    if dates:
        url += f"?dates={dates}"
    req = urllib.request.Request(url, headers={"User-Agent": "prediction-mm/soccer"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return parse_scoreboard(json.loads(r.read()))
    except Exception:
        return []


def recent_results(league: str, dates: str | None = None) -> list[dict]:
    """Finished matches (state 'post', both scores present) — chronological,
    ready to feed `EloTable.observe`."""
    ms = [m for m in fetch_scoreboard(league, dates)
          if m["completed"] and m["home_score"] is not None
          and m["away_score"] is not None]
    ms.sort(key=lambda m: m["date"])
    return ms


def upcoming_fixtures(league: str, dates: str | None = None) -> list[dict]:
    """Not-yet-started matches (state 'pre') to predict."""
    ms = [m for m in fetch_scoreboard(league, dates) if m["state"] == "pre"]
    ms.sort(key=lambda m: m["date"])
    return ms


def finals_map(league: str, dates: str | None = None) -> dict:
    """{espn_id: match} for COMPLETED matches on `dates` — settlement lookup by id."""
    return {m["id"]: m for m in fetch_scoreboard(league, dates)
            if m["completed"] and m["home_score"] is not None
            and m["away_score"] is not None}
