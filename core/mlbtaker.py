"""
MLB game-market probe — pure selection/sizing/safety (no network).

Buys the model-cheap side of PM game markets near kickoff, at EXECUTABLE book
prices (rows must have been through odds_refresh_pass: meta.odds_at + book_side0).
Venue order semantics (live-verified, inverted vs their names): resting post-only
`BUY_LONG` opens a long on the book's outcome (the cricket farm's proven path);
resting post-only `BUY_SHORT` opens a short on it (the weather taker's proven path)
— so fading the book side = BUY_SHORT, no SELL_* intents anywhere.
"""
from datetime import datetime


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def candidates(rows: list[dict], now_ts: float, ahead_secs: int = 9000,
               edge_min: float = 0.05) -> list[dict]:
    """Best-edge side per game from odds-refreshed prediction rows: unsettled, executable
    odds attached, kickoff within [now, now+ahead_secs], edge = model_prob − executable ask
    >= edge_min. One candidate per market (the better-edge side). Sorted best-first."""
    best: dict[str, dict] = {}
    for r in rows:
        # ONLY the proven base model drives bets — variant/blend rows (elo-mlb-ctx,
        # blend-mlb) are tracked-for-comparison and must never reach the order path.
        if (r.get("model") or "elo-mlb") != "elo-mlb":
            continue
        meta = r.get("meta") or {}
        slug = meta.get("pm_slug")
        if not slug or not meta.get("odds_at"):
            continue
        ko = _ts(meta.get("kickoff", ""))
        if not ko or not (0 <= ko - now_ts <= ahead_secs):
            continue
        ask, prob = r.get("market_ask"), r.get("model_prob")
        if ask is None or prob is None:
            continue
        edge = float(prob) - float(ask)
        if edge < edge_min:
            continue
        c = {"slug": slug, "outcome": r.get("outcome"), "side0": meta.get("book_side0", ""),
             "edge": round(edge, 4), "ask": float(ask), "kickoff": ko,
             "row_id": r.get("id")}
        if slug not in best or edge > best[slug]["edge"]:
            best[slug] = c
    return sorted(best.values(), key=lambda c: -c["edge"])


def order_for(outcome: str, side0: str, best_bid, best_ask):
    """(intent, price, collateral_per_contract) for one candidate against the LIVE book,
    or None. Long the book side: BUY_LONG resting inside the spread (ask−0.01, floored at
    the bid — post-only can never cross). Fade the book side: short it via BUY_SHORT at
    bid+0.01 (capped at the ask). Collateral: long = price, short = 1−price."""
    if best_bid is None or best_ask is None or side0 not in ("home", "away"):
        return None
    bb, ba = float(best_bid), float(best_ask)
    if not (0 < bb <= ba < 1):
        return None
    if outcome == side0:
        px = max(bb, round(ba - 0.01, 2))
        return "ORDER_INTENT_BUY_LONG", px, px
    px = min(ba, round(bb + 0.01, 2))
    return "ORDER_INTENT_BUY_SHORT", px, round(1 - px, 4)


def stale_order_ids(open_orders: list[dict], kickoff_by_slug: dict, now_ts: float,
                    prefix: str = "aec-mlb") -> list[tuple[str, str]]:
    """[(order_id, slug)] of OUR resting game orders whose game has started (or whose
    kickoff we can't determine) — cancel them: an unfilled pre-game maker order left
    resting in-play is pure adverse selection."""
    out = []
    for o in open_orders:
        if not isinstance(o, dict):
            continue
        slug = str(o.get("marketSlug", ""))
        if not slug.startswith(prefix):
            continue
        ko = kickoff_by_slug.get(slug)
        if ko is None or now_ts >= ko:
            oid = o.get("id") or o.get("orderId")
            if oid:
                out.append((str(oid), slug))
    return out


def wrong_direction(positions: dict, expected_sign: dict) -> list[str]:
    """Slugs where the venue position's sign contradicts the direction WE opened
    (expected_sign: slug -> +1 long / -1 short). A mismatch means an order intent did
    the opposite of its name — halt (the SELL_SHORT-opened-a-LONG failure mode)."""
    bad = []
    for slug, sign in expected_sign.items():
        try:
            net = float((positions.get(slug) or {}).get("netPosition", 0) or 0)
        except (TypeError, ValueError):
            net = 0.0
        if net and (net > 0) != (sign > 0):
            bad.append(slug)
    return bad
