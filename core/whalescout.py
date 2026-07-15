"""
Pure helpers for the READ-ONLY whale scout (copy-trading research).

Ranks Polymarket .com wallets by OFFICIAL leaderboard profit (lb-api/profit) — never
by volume alone — then shapes their public TRADE activity into paper-copy rows.
No orders; a US person still can't place these offshore trades. Network I/O lives in
poly_runner.whale_scout.
"""


def parse_lb_rows(raw):
    """Normalise an lb-api /profit (or /volume) response into
    [{addr, name, amount}, ...]. `amount` is the official metric (profit or volume)."""
    if not isinstance(raw, list):
        return []
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        addr = (r.get("proxyWallet") or r.get("address") or "").strip().lower()
        if not (addr.startswith("0x") and len(addr) == 42):
            continue
        try:
            amt = float(r.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        name = str(r.get("name") or r.get("pseudonym") or addr[:10])[:48]
        out.append({"addr": addr, "name": name, "amount": amt})
    return out


def select_whales(profit_rows, vol_by_addr=None, min_profit=1000.0, min_volume=0.0,
                  max_n=10):
    """Pick copy candidates: official profit first (must be > min_profit), optional
    min volume, then take the top `max_n` by profit. Volume is a secondary filter —
    never the rank key (biggest wallets ≠ edge; see pspspsps5)."""
    vol_by_addr = vol_by_addr or {}
    cands = []
    for r in profit_rows:
        if r["amount"] < min_profit:
            continue
        vol = float(vol_by_addr.get(r["addr"], 0.0) or 0.0)
        if vol < min_volume:
            continue
        cands.append({**r, "volume": vol})
    cands.sort(key=lambda r: -r["amount"])
    return cands[:max_n]


def trade_dedupe_key(trade: dict) -> str:
    """Stable idempotency key for a TRADE activity row."""
    tx = str(trade.get("transactionHash") or "")
    return (f"{tx}-{trade.get('asset', '')}-{trade.get('side', '')}-"
            f"{trade.get('size', '')}-{trade.get('price', '')}")


def is_trade(row: dict) -> bool:
    return str((row or {}).get("type", "")).upper() == "TRADE"


def paper_copy_record(trade: dict, whale: dict, copy_ask=None, today: str = "",
                      window: str = "30d") -> dict:
    """Shape one of their public trades into a model_predictions row for paper scoring.

    `market_ask` = THEIR fill price (what they paid). `meta.copy_ask` = the ask WE
    observed when we first saw the trade (lagged executable price) — None until the
    network layer stamps it. Settlement / PnL vs venue outcome is a later pass."""
    base = str(trade.get("slug") or trade.get("conditionId") or "")[:88]
    tx = str(trade.get("transactionHash") or "")
    uniq = (tx[-10:] or str(trade.get("timestamp", "")))
    side = str(trade.get("side", "")).lower()
    try:
        their_px = float(trade.get("price")) if trade.get("price") is not None else None
    except (TypeError, ValueError):
        their_px = None
    try:
        size = float(trade.get("size") or 0)
    except (TypeError, ValueError):
        size = 0.0
    try:
        usdc = float(trade.get("usdcSize") or 0)
    except (TypeError, ValueError):
        usdc = 0.0
    return {
        "model": "whale-scout",
        "sport": "whale",
        "market_slug": f"{base}|{uniq}"[:120],
        "outcome": side,
        "model_prob": None,
        "market_bid": None,
        "market_ask": their_px,          # their fill
        "edge": None,
        "liquid": None,
        "settle_date": today,
        "run_date": today,
        "meta": {
            "whale_addr": whale.get("addr"),
            "whale_name": whale.get("name"),
            "whale_profit": whale.get("amount"),
            "whale_volume": whale.get("volume"),
            "window": window,
            "slug": base,
            "title": str(trade.get("title", ""))[:160],
            "outcome_name": trade.get("outcome"),
            "size": size,
            "usdc": usdc,
            "ts": trade.get("timestamp"),
            "tx": tx,
            "asset": trade.get("asset"),
            "copy_ask": copy_ask,        # what WE would have paid when we saw it
            "lag_bps": (_lag_bps(their_px, copy_ask, side)
                        if (their_px is not None and copy_ask is not None) else None),
        },
    }


def _lag_bps(their_px, copy_ask, side: str):
    """How much worse our lagged copy price is vs their fill, in bps of price.
    BUY: positive = we pay more (worse). SELL: positive = we receive less (worse)."""
    try:
        their_px = float(their_px)
        copy_ask = float(copy_ask)
    except (TypeError, ValueError):
        return None
    if their_px <= 0:
        return None
    if side == "buy":
        return round((copy_ask - their_px) / their_px * 10000, 1)
    if side == "sell":
        return round((their_px - copy_ask) / their_px * 10000, 1)
    return None
