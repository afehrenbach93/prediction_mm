"""
Golf results/field feed (ESPN, no key). Runs on the Render worker.

ESPN's golf scoreboard returns tournaments (events), each with one competition whose
competitors are the WHOLE field, each carrying an athlete + a finishing position. We
parse:
  - finished events  -> [{player, position}] to seed player skill (`lib.golf`)
  - the live/next event field -> [player] to predict
  - the winner (position 1) -> settlement

`parse_golf` is pure (unit-tested); `fetch` does the I/O. `sport_path` defaults to
'golf/pga'.
"""
import json
import re
import urllib.request

from core.espnfeed import normalize_name, SCOREBOARD

DEFAULT_TOUR = "golf/pga"


def _position(comp: dict):
    """Finishing position as int, or None. ESPN gives displayName '1' / 'T5' (tie) /
    'CUT'. Prefer displayName (the human RANK) — `status.position.id` is an internal
    identifier, NOT the finish place, so reading it first mis-ranked everyone and the
    winner (place 1) was never found (golf settled 0 rows for a week)."""
    st = comp.get("status") or {}
    pos = st.get("position") or {}
    raw = pos.get("displayName") or pos.get("id") or ""
    m = re.search(r"\d+", str(raw))
    return int(m.group()) if m else None


def _is_winner(comp: dict) -> bool:
    """ESPN's authoritative winner marker on the tournament champion — a competitor-level
    `winner: true` flag (present on completed events). Used ahead of position==1 so a
    quirk in the position field can't hide the winner."""
    return bool(comp.get("winner"))


def parse_golf(raw: dict) -> list[dict]:
    """Pure: ESPN golf scoreboard -> list of tournaments. Each:
    {id, name, date, state(pre|in|post), completed, field: [{player, player_raw,
     position, won}]}. position is None pre-event / for cut players; won = ESPN's
    winner flag."""
    out = []
    for ev in (raw or {}).get("events", []) or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        status = (ev.get("status") or {}).get("type") or {}
        field = []
        for c in comps[0].get("competitors") or []:
            ath = c.get("athlete") or {}
            name = ath.get("displayName") or ath.get("shortName") or ""
            if not name:
                continue
            field.append({"player": normalize_name(name), "player_raw": name,
                          "position": _position(c), "won": _is_winner(c)})
        if not field:
            continue
        out.append({
            "id": str(ev.get("id", "")), "name": ev.get("name", ""),
            "date": ev.get("date", ""),
            "end_date": ev.get("endDate", "") or ev.get("end", ""),
            "state": status.get("state", ""),
            "completed": bool(status.get("completed", False)),
            "field": field,
        })
    return out


def fetch(sport_path: str = DEFAULT_TOUR, dates: str | None = None) -> list[dict]:
    url = SCOREBOARD.format(path=sport_path)
    if dates:
        url += f"?dates={dates}"
    req = urllib.request.Request(url, headers={"User-Agent": "prediction-mm/golf"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return parse_golf(json.loads(r.read()))
    except Exception:
        return []


def recent_events(sport_path: str = DEFAULT_TOUR, dates: str | None = None) -> list[list[dict]]:
    """Finished tournaments as lists of {player, position} — to seed skill."""
    return [t["field"] for t in fetch(sport_path, dates)
            if t["completed"] and any(p["position"] for p in t["field"])]


def current_event(sport_path: str = DEFAULT_TOUR, dates: str | None = None):
    """The live or next tournament to predict (pre/in), or None."""
    upcoming = [t for t in fetch(sport_path, dates) if t["state"] in ("pre", "in")]
    upcoming.sort(key=lambda t: t["date"])
    return upcoming[0] if upcoming else None


def winners_map(sport_path: str = DEFAULT_TOUR, dates: str | None = None) -> dict:
    """{tournament_id: winning_player} for completed tournaments — settlement. Winner by
    ESPN's `won` flag first, then finishing position 1 (either identifies the champion)."""
    out = {}
    for t in fetch(sport_path, dates):
        if not t["completed"]:
            continue
        winner = (next((p["player"] for p in t["field"] if p.get("won")), None)
                  or next((p["player"] for p in t["field"] if p["position"] == 1), None))
        if winner:
            out[t["id"]] = winner
    return out
