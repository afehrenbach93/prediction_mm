"""
Pure helpers for the economics-pilot GO/KILL readout.

Assembles a same-day verdict from the worker's `poly_status` heartbeat +
`poly_control` row — no exchange calls. Inventory drift is only visible if the
heartbeat (or an optional positions overlay) carries it; otherwise the readout
stays WATCH/INCONCLUSIVE rather than inventing a GO.
"""
from datetime import datetime, timezone


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def hours_until(live_until, now=None) -> float | None:
    """Hours remaining on the live window (negative if already expired)."""
    lu = _parse_ts(live_until)
    if lu is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (lu - now).total_seconds() / 3600.0


def summarize(status_row: dict, control_row: dict | None = None,
              now=None) -> dict:
    """Flatten heartbeat + control into a small fact dict for printing/verdict."""
    status_row = status_row or {}
    control_row = control_row or {}
    detail = status_row.get("detail") or {}
    if not isinstance(detail, dict):
        detail = {}
    ry = detail.get("reward_yield") or {}
    if not isinstance(ry, dict):
        ry = {}
    top = ry.get("top") or []
    fattest = ry.get("fattest") or []
    top0 = top[0] if top else {}
    fat0 = fattest[0] if fattest else {}
    hrs = hours_until(control_row.get("live_until"), now=now)
    return {
        "mode": status_row.get("mode"),
        "status": status_row.get("status") or detail.get("status"),
        "last_seen": status_row.get("last_seen"),
        "desired_mode": control_row.get("desired_mode"),
        "live_until": control_row.get("live_until"),
        "hours_left": None if hrs is None else round(hrs, 2),
        "budget": detail.get("budget") if detail.get("budget") is not None
                  else control_row.get("budget"),
        "markets": detail.get("markets"),
        "size": detail.get("size"),
        "placed_ok": detail.get("placed_ok"),
        "rej": detail.get("rej"),
        "balance": detail.get("balance"),
        "buying_power": detail.get("buying_power"),
        "realized_pnl": detail.get("realized_pnl"),
        "open_contracts": detail.get("open_contracts"),
        "max_pool": ry.get("max_pool"),
        "ry_warming": ry.get("warming"),
        "ry_n": ry.get("n"),
        "top_slug": (top0.get("slug") or "")[:48] or None,
        "top_rwd_hr": top0.get("rwd_hr"),
        "top_yld_hr": top0.get("yld_hr"),
        "top_vol_min": top0.get("vol_min"),
        "top_share": top0.get("share"),
        "fattest_slug": (fat0.get("slug") or "")[:48] or None,
        "fattest_pool": fat0.get("pool"),
    }


def verdict(summary: dict, *, min_pool_for_go: float = 500.0,
            max_rej_ratio: float = 0.25) -> tuple[str, str]:
    """Return (VERDICT, reason).

    Conservative by design:
      KILL         — breaker tripped, or live window over with negative realized,
                     or fat pools gone while we were supposed to be farming them.
      GO           — only when quoting, fat pool present, vol warmed, rejection
                     rate low, AND realized_pnl not negative. Still provisional
                     until credited earnings confirm (see PILOT.md).
      WATCH        — live quoting / window still open but signal incomplete.
      INCONCLUSIVE — idle/track/missing data; don't scale, don't kill yet.
    """
    s = summary or {}
    status = (s.get("status") or "").lower()
    mode = (s.get("mode") or "").lower()
    desired = (s.get("desired_mode") or "").lower()
    hrs = s.get("hours_left")
    max_pool = s.get("max_pool")
    realized = s.get("realized_pnl")
    placed = s.get("placed_ok")
    rej = s.get("rej")
    warming = bool(s.get("ry_warming"))

    if status == "tripped":
        return "KILL", "breaker tripped — farm stood aside (check deny-list / inventory)"

    if realized is not None and float(realized) < 0:
        return "KILL", f"realized_pnl={realized} negative on heartbeat"

    rej_ratio = None
    try:
        tot = float(placed or 0) + float(rej or 0)
        if tot > 0:
            rej_ratio = float(rej or 0) / tot
    except (TypeError, ValueError):
        pass
    if rej_ratio is not None and rej_ratio > max_rej_ratio and float(placed or 0) >= 4:
        return "KILL", f"rejection rate {rej_ratio:.0%} above {max_rej_ratio:.0%}"

    live_open = desired == "live" and (hrs is None or hrs > 0)
    quoting = mode == "live" and status in ("quoting", "live")

    if live_open and quoting:
        if max_pool is not None and float(max_pool) < min_pool_for_go:
            return "WATCH", (f"quoting but max_pool=${max_pool} < ${min_pool_for_go:.0f} "
                             f"— fat-pool thesis not active")
        # treat missing/zero top vol as incomplete adverse-selection signal even if
        # the sampler dropped the warming flag (single mid sample → vol_min=0).
        if warming or s.get("top_vol_min") in (None, 0, 0.0):
            return "WATCH", "quoting on fat pools; vol still warming / incomplete"
        if max_pool is not None and float(max_pool) >= min_pool_for_go:
            return "GO", ("provisional GO — quoting, fat pool, warmed vol, no negative "
                          "realized; confirm with credited earnings (~5+2bd)")
        return "WATCH", "live quoting; gather more same-day adverse-selection signal"

    if hrs is not None and hrs <= 0:
        if realized is not None and float(realized) >= 0 and max_pool and float(max_pool) >= min_pool_for_go:
            return "WATCH", "window ended; modeled surface looked fat — await earnings credit"
        return "INCONCLUSIVE", "live window ended without a clear same-day edge signal"

    return "INCONCLUSIVE", "not in a live quoting window (or heartbeat incomplete)"
