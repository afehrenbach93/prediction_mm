"""
Hard eligibility gate for global CLOB live trading.

Live orders require BOTH:
  CLOB_MODE=live
  ELIGIBILITY_CONFIRMED=true

ELIGIBILITY_CONFIRMED asserts the operator verified US/FL access and
Polymarket ToS for global polymarket.com — the historically US-restricted path.
This check does not perform legal determination; it only blocks footguns.
"""
from __future__ import annotations

import os


def eligibility_confirmed() -> bool:
    return os.getenv("ELIGIBILITY_CONFIRMED", "").strip().lower() in ("true", "1", "yes")


def resolve_live_mode(mode: str | None = None) -> tuple[bool, str]:
    """Return (live_allowed, reason). Never True unless mode=live AND flag set."""
    m = (mode if mode is not None else os.getenv("CLOB_MODE", "shadow")).strip().lower()
    if m != "live":
        return False, ""
    if not eligibility_confirmed():
        return False, (
            "CLOB_MODE=live refused: ELIGIBILITY_CONFIRMED is not true — "
            "staying in shadow (no exchange mutations)."
        )
    return True, ""
