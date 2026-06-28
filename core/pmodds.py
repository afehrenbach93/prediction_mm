"""
Polymarket US market-odds capture for the model tracker.

Goal: attach the live PM market price to each model prediction so we can measure
model-vs-MARKET edge, not just calibration. PM sports slug/team-code formats vary by
league and this venue is geo-blocked from the dev sandbox, so we LEARN the real format
from worker logs first (`sports_market_sample`), then match games to markets and read
their books. Pure helpers + a network sampler (runs on the worker).
"""
import re
from collections import Counter


def norm_tokens(s: str) -> set:
    """Lowercase alphanumeric tokens of a name, for fuzzy team matching."""
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


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


def build_index(markets: list[dict]) -> list[tuple[str, set, str]]:
    """[(slug, token_set, date_str)] for matching. token_set is the slug's a-z0-9
    tokens; date_str is any YYYY-MM-DD found in the slug."""
    idx = []
    for m in markets:
        slug = m.get("slug", "")
        if not slug:
            continue
        mt = re.search(r"\d{4}-\d{2}-\d{2}", slug)
        idx.append((slug, set(re.findall(r"[a-z0-9]+", slug.lower())),
                    mt.group() if mt else ""))
    return idx


def find_market_slug(idx, home: str, away: str, date_iso: str,
                     min_score: int = 2) -> str | None:
    """Best slug whose tokens overlap both team names on the given date. Returns None
    below `min_score` overlap. (Team-name vs slug-abbrev matching is league-specific;
    refined once the real slug format is known from sports_market_sample.)"""
    ht, at = norm_tokens(home), norm_tokens(away)
    best, best_score = None, 0
    for slug, toks, d in idx:
        if date_iso and d and d != date_iso:
            continue
        score = len(ht & toks) + len(at & toks)
        if score > best_score:
            best, best_score = slug, score
    return best if best_score >= min_score else None
