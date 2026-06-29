"""
Polymarket US market-odds capture for the model tracker.

Goal: attach the live PM market price to each model prediction so we can measure
model-vs-MARKET edge, not just calibration. PM head-to-head game slugs use abbreviated
team codes + a date (like the cricket reward markets, e.g. `aec-mlc-lakr-soe-2026-06-28`),
and this venue is geo-blocked from the dev sandbox — so we match games to markets on the
worker by team-token + date overlap, read the book, and LOG match rate + the matched
market's outcome label so the PM moneyline structure can be confirmed from logs before
edge is computed. Pure helpers + a network attach pass (runs on the worker).
"""
import json
import re
from collections import Counter


def norm_tokens(s: str) -> set:
    """Lowercase alphanumeric tokens of a name, for fuzzy team matching."""
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


# ESPN team code -> PM team code, where the two venues disagree (confirmed from the
# odds-matcher MISS logs). Most codes match; only these few differ. Add as discovered.
ABBR_ALIAS = {
    "chw": "cws",   # Chicago White Sox
    "ari": "az",    # Arizona Diamondbacks
    "wsh": "was",   # Washington (PM sometimes 'was'); 'wsh' already matches too
    "sf": "sf", "sd": "sd",
}


def team_tokens(name: str, abbr: str = "") -> set:
    """Candidate match tokens for a team: its name words, the words concatenated, ESPN's
    short code (e.g. 'nyy'), and the PM alias for that code where the venues disagree."""
    words = norm_tokens(name)
    cands = set(words)
    if words:
        cands.add("".join(sorted(words)))   # order-independent concat fallback
        cands.add("".join(name.lower().split()))
    if abbr:
        a = abbr.lower()
        cands.add(a)
        if a in ABBR_ALIAS:
            cands.add(ABBR_ALIAS[a])
    return {c for c in cands if c}


def _team_matches(name: str, abbr: str, slug_tokens: set) -> bool:
    """True if a PM slug's tokens identify this team. Matches on: exact ESPN code,
    a shared name word, or a 3+ char prefix either way (handles 'bos'<->'boston')."""
    words = norm_tokens(name)
    cands = team_tokens(name, abbr)
    for st in slug_tokens:
        if len(st) < 2 or st.isdigit():
            continue
        if st in cands:                       # exact code / word / concat
            return True
        if len(st) >= 3 and any(w.startswith(st) or st.startswith(w)
                                for w in words if len(w) >= 3):
            return True
    return False


def sports_market_sample(client, log) -> None:
    """DIAGNOSTIC (read-only): log the PM catalog's slug-prefix histogram + samples per
    league keyword, so we can see the real sports-market slug format and team codes
    before building the matcher. Mirrors the tennis/golf validate-first diagnostics."""
    try:
        mks = client.get_markets(max_pages=40)
        slugs = [m.get("slug", "") for m in mks if m.get("slug")]
        pref = Counter("-".join(s.split("-")[:2]) for s in slugs)
        log(f"PM CATALOG: {len(slugs)} active markets; top_prefixes={pref.most_common(18)}")
        for kw in ("mlb", "nba", "nfl", "ncaa", "mls", "wc", "fifa", "soccer",
                   "tennis", "atp", "wta"):
            sample = [s for s in slugs if kw in s][:5]
            if sample:
                log(f"PM sample[{kw}]: {sample}")
    except Exception as e:
        log(f"pm sample error: {e}")


def build_index(markets: list[dict]) -> list[tuple[str, set, str, str]]:
    """[(slug, token_set, date_str, outcome)] for matching. token_set is the slug's
    a-z0-9 tokens; date_str is any YYYY-MM-DD found in the slug; outcome is the market's
    declared outcome label (which side YES is), used later to map a price to home/away."""
    idx = []
    for m in markets:
        slug = m.get("slug", "")
        if not slug:
            continue
        mt = re.search(r"\d{4}-\d{2}-\d{2}", slug)
        # the YES-side label lives under different keys across PM market types; take the
        # first populated one so a price can be mapped to home/away.
        label = next((str(m.get(k)) for k in
                      ("outcome", "groupItemTitle", "shortTitle", "title", "question", "name")
                      if m.get(k)), "")
        idx.append((slug, set(re.findall(r"[a-z0-9]+", slug.lower())),
                    mt.group() if mt else "", label))
    return idx


def find_market_slugs(idx, home: str, away: str, date_iso: str,
                      home_abbr: str = "", away_abbr: str = "") -> list:
    """All markets on `date_iso` whose tokens identify BOTH teams — i.e. head-to-head
    markets for this game (a game can have several market types). Returned [(slug,
    outcome)] best-first by token specificity. Date matches within ±1 day (ESPN dates are
    UTC, PM slugs are ET — late games shift a day, same gotcha as settlement). Empty if
    either side fails to match."""
    hits = []
    for slug, toks, d, outcome in idx:
        if date_iso and d and not _date_near(d, date_iso):
            continue
        if _team_matches(home, home_abbr, toks) and _team_matches(away, away_abbr, toks):
            hits.append((len(toks), slug, outcome))
    hits.sort()                               # fewer tokens = more specific game market
    return [(slug, outcome) for _, slug, outcome in hits]


def _date_near(d1: str, d2: str, days: int = 1) -> bool:
    """True if two YYYY-MM-DD strings are within `days` of each other (TZ tolerance)."""
    from datetime import date
    try:
        a, b = date.fromisoformat(d1[:10]), date.fromisoformat(d2[:10])
        return abs((a - b).days) <= days
    except Exception:
        return d1[:10] == d2[:10]


def find_market_slug(idx, home: str, away: str, date_iso: str,
                     home_abbr: str = "", away_abbr: str = "") -> str | None:
    """Best single slug whose tokens identify both teams on the date, else None."""
    hits = find_market_slugs(idx, home, away, date_iso, home_abbr, away_abbr)
    return hits[0][0] if hits else None


def _side_of(outcome: str, home: str, away: str) -> str:
    """Which side a SINGLE-team outcome label refers to ('home'/'away'/'') by name overlap.
    Only meaningful when `outcome` names ONE team — PM's game-market title names both, so
    use `yes_side_from_slug` for those (this stays for genuine single-outcome labels)."""
    ot = norm_tokens(outcome)
    if not ot:
        return ""
    hs = len(ot & norm_tokens(home))
    as_ = len(ot & norm_tokens(away))
    return "home" if hs > as_ else "away" if as_ > hs else ""


def _outcome_prices(market: dict, home: str, away: str):
    """(home_price, away_price) market-implied probs from a game market's parallel
    `outcomes` (team names) / `outcomePrices` arrays. Each outcome names ONE team, so
    _side_of maps it cleanly. None where a side can't be mapped/parsed."""
    try:
        outs = json.loads(market.get("outcomes") or "[]")
        prs = [float(x) for x in json.loads(market.get("outcomePrices") or "[]")]
    except Exception:
        return None, None
    hp = ap = None
    for nm, pr in zip(outs, prs):
        s = _side_of(str(nm), home, away)
        if s == "home":
            hp = pr
        elif s == "away":
            ap = pr
    return hp, ap


def attach_market_odds(client, fixtures: list[dict], log, max_pages: int = 150) -> dict:
    """For each upcoming fixture, find its PM market by team+date and read top-of-book.
    Returns {espn_id: {slug, bid, ask, yes_side, alts}}. yes_side maps the price to
    'home'/'away' via the market's outcome label. Read-only; logs match rate + samples.
    max_pages must cover the whole active catalog — per-game markets are created day-of
    and sort late, so a small cap silently truncates them (first read missed today's games
    at the 4000-market/40-page cap)."""
    try:
        mks = client.get_markets(max_pages=max_pages)
    except Exception as e:
        log(f"odds: catalog fetch error: {e}")
        return {}
    idx = build_index(mks)
    by_slug = {m.get("slug", ""): m for m in mks}
    log(f"odds: catalog {len(idx)} markets (max_pages={max_pages})")
    out, matched, samples, misses, probed = {}, 0, [], [], False
    for fx in fixtures:
        date = (fx.get("date") or "")[:10]
        hits = find_market_slugs(idx, fx.get("home_raw", ""), fx.get("away_raw", ""),
                                 date, fx.get("home_abbr", ""), fx.get("away_abbr", ""))
        if not hits:
            # DIAG: for the first few misses, show the fixture + catalog slugs on date±1
            # that share EITHER team token — reveals the real per-game slug format (or
            # that PM lists no single-game market for this sport).
            if len(misses) < 4:
                ht = team_tokens(fx.get("home_raw", ""), fx.get("home_abbr", ""))
                at = team_tokens(fx.get("away_raw", ""), fx.get("away_abbr", ""))
                near = [s for s, toks, d, _ in idx
                        if d and _date_near(d, date) and (toks & ht or toks & at)][:6]
                misses.append(f'MISS {fx.get("away_abbr") or fx.get("away_raw")}@'
                              f'{fx.get("home_abbr") or fx.get("home_raw")} {date}: '
                              f'near-date team-token slugs={near}')
            continue
        slug = hits[0][0]
        m = by_slug.get(slug, {})
        if not probed:
            log(f"  odds PROBE {slug}: outcomes={m.get('outcomes')} "
                f"prices={m.get('outcomePrices')}")
            probed = True
        # game markets carry parallel outcomes/outcomePrices arrays (each outcome is ONE
        # team) — map each to home/away directly; no YES-side guessing or book read needed.
        home_p, away_p = _outcome_prices(m, fx.get("home_raw", ""), fx.get("away_raw", ""))
        out[str(fx["id"])] = {"slug": slug, "home_price": home_p, "away_price": away_p,
                              "alts": len(hits)}
        matched += 1
        if len(samples) < 8:
            samples.append(f'{fx.get("away_raw")}@{fx.get("home_raw")} {date} -> {slug} '
                           f'home={home_p} away={away_p} [{len(hits)} mkts]')
    log(f"odds: matched {matched}/{len(fixtures)} fixtures to PM markets")
    for s in samples:
        log(f"  odds: {s}")
    for s in misses:
        log(f"  odds: {s}")
    return out
