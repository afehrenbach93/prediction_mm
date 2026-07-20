"""
Shadow-gated CLOB trading wrapper around py-clob-client-v2.

CLOB_MODE=shadow (default): place/cancel are recorded locally; no authenticated
mutations hit the exchange. Reads (books via public client) still work.

Live requires BOTH CLOB_MODE=live AND ELIGIBILITY_CONFIRMED=true — otherwise
construction and mutations stay shadow (P0 hard gate).
"""
from __future__ import annotations

import os
import time
from typing import Any

from core.clobclient import ClobClient as PublicClobClient
from core.eligibility import resolve_live_mode


class ClobTrader:
    def __init__(self, live: bool = False, public: PublicClobClient | None = None,
                 refuse_reason: str = ""):
        # Defense in depth: never honor live=True without eligibility
        allowed, reason = resolve_live_mode("live" if live else "shadow")
        if live and not allowed:
            self.live = False
            self.refuse_reason = reason or refuse_reason
        else:
            self.live = bool(allowed and live)
            self.refuse_reason = refuse_reason
        self.public = public or PublicClobClient()
        self.shadow_orders: list[dict] = []
        self._client = None

    @classmethod
    def from_env(cls) -> "ClobTrader":
        mode = os.getenv("CLOB_MODE", "shadow").strip().lower()
        live, reason = resolve_live_mode(mode)
        if mode == "live" and not live:
            print(f"[clob] {reason}", flush=True)
        return cls(live=live, refuse_reason=reason)

    def _live_mutations_allowed(self) -> bool:
        allowed, reason = resolve_live_mode("live" if self.live else "shadow")
        if self.live and not allowed:
            self.live = False
            self.refuse_reason = reason
            return False
        return bool(self.live and allowed)

    def _auth_client(self):
        if not self._live_mutations_allowed():
            raise RuntimeError(
                self.refuse_reason
                or "live client refused: ELIGIBILITY_CONFIRMED required with CLOB_MODE=live"
            )
        if self._client is not None:
            return self._client
        try:
            from py_clob_client_v2 import ApiCreds, ClobClient
        except ImportError as e:
            raise RuntimeError(
                "py-clob-client-v2 required for live/auth ops: pip install py-clob-client-v2"
            ) from e
        host = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
        chain_id = int(os.getenv("CLOB_CHAIN_ID", "137"))
        pk = os.getenv("CLOB_PRIVATE_KEY") or os.getenv("PK") or ""
        if not pk:
            raise RuntimeError("CLOB_PRIVATE_KEY (or PK) missing")
        api_key = os.getenv("CLOB_API_KEY", "")
        api_secret = os.getenv("CLOB_SECRET", "") or os.getenv("CLOB_API_SECRET", "")
        api_pass = os.getenv("CLOB_PASS_PHRASE", "") or os.getenv("CLOB_PASSPHRASE", "")
        funder = os.getenv("CLOB_FUNDER", "") or os.getenv("CLOB_FUNDER_ADDRESS", "")
        sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "0"))
        kwargs: dict[str, Any] = {
            "host": host,
            "chain_id": chain_id,
            "key": pk,
        }
        if api_key and api_secret and api_pass:
            kwargs["creds"] = ApiCreds(
                api_key=api_key, api_secret=api_secret, api_passphrase=api_pass
            )
        if funder:
            kwargs["funder"] = funder
        if sig_type:
            kwargs["signature_type"] = sig_type
        self._client = ClobClient(**kwargs)
        if "creds" not in kwargs:
            creds = self._client.create_or_derive_api_key()
            self._client.set_api_creds(creds)
        return self._client

    def get_book(self, token_id: str):
        return self.public.get_book(token_id)

    def get_sampling_markets(self):
        return list(self.public.iter_sampling_markets())

    def place_limit(self, token_id: str, side: str, price: float, size: float,
                    tick_size: str = "0.01", neg_risk: bool = False,
                    post_only: bool = True) -> dict:
        rec = {
            "ts": time.time(),
            "token_id": token_id,
            "side": side,
            "price": price,
            "size": size,
            "tick_size": tick_size,
            "neg_risk": neg_risk,
            "post_only": post_only,
        }
        if not self._live_mutations_allowed():
            rec["shadow"] = True
            self.shadow_orders.append(rec)
            return {"shadow": True, "orderID": f"shadow-{len(self.shadow_orders)}", **rec}
        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions, Side
        side_enum = Side.BUY if side.upper() == "BUY" else Side.SELL
        client = self._auth_client()
        resp = client.create_and_post_order(
            order_args=OrderArgs(
                token_id=str(token_id),
                price=float(price),
                size=float(size),
                side=side_enum,
            ),
            options=PartialCreateOrderOptions(
                tick_size=str(tick_size), neg_risk=bool(neg_risk)
            ),
            order_type=OrderType.GTC,
            post_only=bool(post_only),
        )
        return resp if isinstance(resp, dict) else {"resp": resp}

    def cancel_all(self) -> dict:
        if not self._live_mutations_allowed():
            self.shadow_orders.append({"ts": time.time(), "shadow": True, "cancel_all": True})
            return {"shadow": True, "cancelled": "all"}
        return self._auth_client().cancel_all()

    def cancel_market(self, token_id: str = "", condition_id: str = "") -> dict:
        if not self._live_mutations_allowed():
            self.shadow_orders.append({
                "ts": time.time(), "shadow": True,
                "cancel_market": token_id or condition_id,
            })
            return {"shadow": True, "cancelled": token_id or condition_id}
        client = self._auth_client()
        try:
            from py_clob_client_v2 import OrderMarketCancelParams
            params = OrderMarketCancelParams(
                market=condition_id or None,
                asset_id=token_id or None,
            )
            return client.cancel_market_orders(params)
        except Exception:
            return client.cancel_all()

    def get_open_orders(self) -> list:
        if not self._live_mutations_allowed():
            return [o for o in self.shadow_orders if o.get("side")]
        return self._auth_client().get_open_orders() or []

    def get_trades(self) -> list:
        if not self._live_mutations_allowed():
            return []
        return self._auth_client().get_trades() or []

    def get_earnings_today(self):
        if not self._live_mutations_allowed():
            return None
        client = self._auth_client()
        try:
            return client.get_total_earnings_for_user_for_day()
        except Exception as e:
            return {"_err": str(e)}
