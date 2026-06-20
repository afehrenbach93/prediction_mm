"""
Pure quoting strategy for the Polymarket US liquidity-reward maker.

The reward score is discountFactor^(ticks_from_best) * size, so to earn we rest
post-only orders AT the touch (tick 0 = full score; one tick off at discount 0.30
keeps only 30%). We quote both sides to stay roughly inventory-neutral and farm the
reward + maker rebate, but bound one-sided accumulation (in-play soccer adverse
selection) with an inventory cap and inventory skew.

Pure functions, no network, unit-tested. The worker translates these into
post-only orders via PolyClient.place_order (which is shadow-gated).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class MakerParams:
    size: float = 100.0          # target resting contracts per side
    max_inventory: float = 300.0 # hard cap on |net position| (contracts)
    min_price: float = 0.03      # don't quote in the extreme tails
    max_price: float = 0.97


def _round_tick(px: float) -> float:
    # The book already returns valid exchange prices (futures tick in 0.001s,
    # not 0.01s) — join the touch at the EXACT book price. Rounding to 0.01
    # pushed quotes across a 1-tick book and got them post-only-rejected.
    return round(px, 4)


def pick_sell_intent(position: float) -> str:
    """Sell to reduce a long (SELL_LONG) if we hold inventory, else open a short
    (SELL_SHORT). Refined against real position mechanics after the live test."""
    return "ORDER_INTENT_SELL_LONG" if position > 0 else "ORDER_INTENT_SELL_SHORT"


def maker_quotes(best_bid: float | None, best_ask: float | None,
                 position: float, p: MakerParams) -> list[tuple[str, float, float]]:
    """Desired post-only resting orders as (intent, price, qty), joining the touch.

    Inventory skew via remaining room to the cap: a long position shrinks the buy
    and leaves the sell full (and vice versa), so we never accumulate past
    max_inventory on either side. Returns [] when there's no room or no book.
    Never crosses (the worker sends these post-only)."""
    orders: list[tuple[str, float, float]] = []
    # position is the NET long-positive contract count.
    buy_room = max(0.0, p.max_inventory - position)
    sell_room = max(0.0, p.max_inventory + position)
    buy_qty = round(min(p.size, buy_room), 2)
    sell_qty = round(min(p.size, sell_room), 2)

    if best_bid is not None and buy_qty > 0 and p.min_price <= best_bid <= p.max_price:
        orders.append(("ORDER_INTENT_BUY_LONG", _round_tick(best_bid), buy_qty))
    if best_ask is not None and sell_qty > 0 and p.min_price <= best_ask <= p.max_price:
        orders.append((pick_sell_intent(position), _round_tick(best_ask), sell_qty))
    return orders


def program_active(now_ts: float, period: str, prog_start: float, prog_end: float,
                   game_start: float, settle: float, day_of_hours: float = 6.0) -> bool:
    """Is a reward PROGRAM currently in its earning window? Driven by the program's
    own period + start/end so we're robust to the venue rotating program types
    (it changes day-to-day): in-play `live`, pre-game `day_of`, or continuous
    windows (`daily_event` / `early` / unknown) that earn whenever the program is
    running. prog_end<=0 means open-ended. No active program = don't quote."""
    if period == "live":
        return bool(game_start and settle) and game_start <= now_ts < settle
    if period == "day_of":
        return bool(game_start) and game_start - day_of_hours * 3600 <= now_ts < game_start
    # continuous program windows: active for the program's own start..end span
    if prog_start and now_ts < prog_start:
        return False
    if prog_end and now_ts >= prog_end:
        return False
    return True
