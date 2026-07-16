"""
Pure helpers for the authenticated incentive-earnings readout.

Network I/O lives in scripts/reward_earnings.py (PolyClient.get_incentive_earnings).
Live shape (2026-07-16): `{"rewards": [{"reward": 1.3, "programType": "...",
"marketSlug": "...", "date": "YYYY-MM-DD", "status": "PAID"|"PENDING"|"SKIPPED"}, ...]}`.
Credits land ~5+2 business days after a reward period ends.
"""


def flatten_earnings(body) -> list[dict]:
    if body is None:
        return []
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("rewards", "earnings", "data", "items", "results",
                "incentiveEarnings"):
        v = body.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    if any(k in body for k in ("amount", "earned", "reward", "pendingCredit", "total")):
        return [body]
    return []


def _num(v):
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def row_amount(r: dict) -> float | None:
    """Best-effort $ amount on one earnings row."""
    for k in ("reward", "amount", "earned", "earnedAmount", "value", "total"):
        n = _num(r.get(k))
        if n is not None:
            return n
    return None


def summarize(body) -> dict:
    rows = flatten_earnings(body)
    by_status: dict[str, float] = {}
    n_by_status: dict[str, int] = {}
    total = 0.0
    n_amt = 0
    for r in rows:
        amt = row_amount(r)
        st = str(r.get("status") or "?").upper()
        n_by_status[st] = n_by_status.get(st, 0) + 1
        if amt is None:
            continue
        n_amt += 1
        total += amt
        by_status[st] = round(by_status.get(st, 0.0) + amt, 4)
    paid = by_status.get("PAID", 0.0)
    pending = by_status.get("PENDING", 0.0)
    skipped = by_status.get("SKIPPED", 0.0)
    # legacy top-level pendingCredit
    if isinstance(body, dict) and body.get("pendingCredit") is not None:
        n = _num(body.get("pendingCredit"))
        if n is not None and "PENDING" not in by_status:
            pending = n
    # recent sample: newest-looking first if date present
    sample = sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True)[:5]
    return {
        "n_rows": len(rows),
        "sum_amount_fields": round(total, 4) if n_amt else None,
        "paid": round(paid, 4),
        "pending_credit": round(pending, 4),
        "skipped": round(skipped, 4),
        "n_by_status": n_by_status,
        "keys": sorted(body.keys())[:40] if isinstance(body, dict) else [],
        "sample": sample,
    }
