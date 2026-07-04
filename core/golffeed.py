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
    """Finishing position as int, or None. ESPN golf competitors carry the finish rank in
    `order` (1 = winner) and have NO `status` field on the scoreboard — the worker SHAPE
    diag confirmed `status=None`, `order=1` for the leader, which is why the old
    status.position read found nobody and golf settled 0 rows for a week. Prefer `order`;
    fall back to status.position for any sport/shape that uses it."""
    o = comp.get("order")
    if isinstance(o, (int, float)):
        return int(o)
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


def debug_shape(sport_path: str = DEFAULT_TOUR, dates: str | None = None) -> str:
    """DIAGNOSTIC (worker-only): fetch the RAW ESPN golf scoreboard and dump, for the
    first COMPLETED event, its competition-level keys plus a sample competitor's keys +
    status structure. The winner isn't surfacing via status.position/`winner` for post
    events; this reveals where the finishing rank actually lives so the parser can be
    fixed precisely. Returns a one-line string; safe on any shape."""
    url = SCOREBOARD.format(path=sport_path)
    if dates:
        url += f"?dates={dates}"
    req = urllib.request.Request(url, headers={"User-Agent": "prediction-mm/golf"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = json.loads(r.read())
    except Exception as e:
        return f"fetch-error: {str(e)[:60]}"
    for ev in (raw or {}).get("events", []) or []:
        st = (ev.get("status") or {}).get("type") or {}
        if not st.get("completed"):
            continue
        comp = (ev.get("competitions") or [{}])[0]
        cs = comp.get("competitors") or []
        c0 = cs[0] if cs else {}
        winflags = [i for i, c in enumerate(cs[:100]) if c.get("winner")]
        return (f"event={ev.get('id')} '{str(ev.get('name',''))[:18]}' comp_keys={sorted(comp.keys())[:14]} "
                f"n_competitors={len(cs)} winner_idx={winflags[:3]} "
                f"c0_keys={sorted(c0.keys())[:14]} c0.status={str(c0.get('status'))[:180]} "
                f"c0.score={c0.get('score')} c0.order={c0.get('order')}")
    return "no completed event in window"


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
