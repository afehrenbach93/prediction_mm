"""
Shadow-gated CLOB trading wrapper.

CLOB_MODE=shadow (default): place/cancel are recorded locally; no authenticated
mutations hit the exchange. Reads (books via public client) still work.

Live requires BOTH CLOB_MODE=live AND ELIGIBILITY_CONFIRMED=true — otherwise
construction and mutations stay shadow (P0 hard gate).

Live execution prefers the official ``polymarket`` SecureClient (package
``polymarket-client``) for deposit-wallet / POLY_1271 accounts. Set
``CLOB_USE_SECURE_CLIENT=0`` to force legacy ``py-clob-client-v2``.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from core.clobclient import ClobClient as PublicClobClient
from core.eligibility import resolve_live_mode


def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in ("1", "true", "yes", "on")


def _use_secure_client() -> bool:
    """Default on for signature_type=3; override with CLOB_USE_SECURE_CLIENT."""
    explicit = os.getenv("CLOB_USE_SECURE_CLIENT")
    if explicit is not None and explicit.strip() != "":
        return _truthy(explicit)
    return int(os.getenv("CLOB_SIGNATURE_TYPE", "0") or 0) == 3


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="python")
        except Exception:
            pass
    if hasattr(obj, "dict"):
        try:
            return obj.dict()
        except Exception:
            pass
    out = {}
    for k in ("order_id", "orderID", "id", "status", "ok", "trade_ids"):
        if hasattr(obj, k):
            out[k] = getattr(obj, k)
    if "order_id" in out and "orderID" not in out:
        out["orderID"] = out["order_id"]
    return out or {"resp": str(obj)}


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
        self._secure = None
        self._backend = None  # "secure" | "legacy"

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

    def _secure_client(self):
        if not self._live_mutations_allowed():
            raise RuntimeError(
                self.refuse_reason
                or "live client refused: ELIGIBILITY_CONFIRMED required with CLOB_MODE=live"
            )
        if self._secure is not None:
            return self._secure
        try:
            from polymarket import SecureClient
            from polymarket.auth import BuilderApiKey
        except ImportError as e:
            raise RuntimeError(
                "polymarket-client required for deposit-wallet live ops: "
                "pip install 'polymarket-client>=0.1.0'"
            ) from e
        pk = os.getenv("CLOB_PRIVATE_KEY") or os.getenv("PK") or ""
        if not pk:
            raise RuntimeError("CLOB_PRIVATE_KEY (or PK) missing")
        funder = os.getenv("CLOB_FUNDER", "") or os.getenv("CLOB_FUNDER_ADDRESS", "")
        if not funder:
            raise RuntimeError("CLOB_FUNDER required for SecureClient live path")

        kwargs: dict[str, Any] = {"private_key": pk, "wallet": funder}
        bkey = os.getenv("POLYMARKET_BUILDER_API_KEY", "")
        bsec = os.getenv("POLYMARKET_BUILDER_SECRET", "")
        bpass = os.getenv("POLYMARKET_BUILDER_PASSPHRASE", "")
        if bkey and bsec and bpass:
            kwargs["api_key"] = BuilderApiKey(key=bkey, secret=bsec, passphrase=bpass)

        # Let SecureClient derive/bind L2 creds for the deposit wallet.
        # Passing pre-derived CLOB_* L2 keys can mismatch POLY_1271 binding.
        self._secure = SecureClient.create(**kwargs)
        self._backend = "secure"
        self._refresh_secure_collateral()
        print("[clob] live backend=SecureClient (polymarket-client)", flush=True)
        return self._secure

    def _refresh_secure_collateral(self) -> None:
        """Force CLOB to re-index deposit-wallet collateral after funding."""
        if self._secure is None:
            return
        try:
            from polymarket._internal.actions import account as _account_actions
            from polymarket._internal.wallet import signature_type_for

            sig = signature_type_for(self._secure.wallet_type)
            path, params = _account_actions.build_update_balance_allowance_request(
                asset_type="COLLATERAL", token_id=None, signature_type=sig,
            )
            self._secure._ctx.secure_clob.get_bytes(path, params=params)
            bal = self._secure.get_balance_allowance(asset_type="COLLATERAL")
            print(
                f"[clob] collateral refreshed balance_usd={bal.balance / 1e6:.2f}",
                flush=True,
            )
        except Exception as e:
            print(f"[clob] collateral refresh warning: {e}", flush=True)

    def _auth_client(self):
        if not self._live_mutations_allowed():
            raise RuntimeError(
                self.refuse_reason
                or "live client refused: ELIGIBILITY_CONFIRMED required with CLOB_MODE=live"
            )
        if _use_secure_client():
            return self._secure_client()
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
        self._backend = "legacy"
        print("[clob] live backend=py-clob-client-v2", flush=True)
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

        if _use_secure_client():
            client = self._secure_client()
            resp = client.place_limit_order(
                token_id=str(token_id),
                price=float(price),
                size=float(size),
                side=side.upper(),
                post_only=bool(post_only),
            )
            out = _as_dict(resp)
            if "orderID" not in out and out.get("order_id"):
                out["orderID"] = out["order_id"]
            return out

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
        client = self._auth_client()
        if _use_secure_client():
            return _as_dict(client.cancel_all())
        return client.cancel_all()

    def cancel_market(self, token_id: str = "", condition_id: str = "") -> dict:
        if not self._live_mutations_allowed():
            self.shadow_orders.append({
                "ts": time.time(), "shadow": True,
                "cancel_market": token_id or condition_id,
            })
            return {"shadow": True, "cancelled": token_id or condition_id}
        client = self._auth_client()
        if _use_secure_client():
            try:
                return _as_dict(client.cancel_market_orders(
                    market=condition_id or None,
                    token_id=token_id or None,
                ))
            except Exception:
                return _as_dict(client.cancel_all())
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
        client = self._auth_client()
        if _use_secure_client():
            out = []
            n = 0
            for item in client.list_open_orders().iter_items():
                out.append(_as_dict(item))
                n += 1
                if n >= 500:
                    break
            return out
        return client.get_open_orders() or []

    def get_trades(self) -> list:
        if not self._live_mutations_allowed():
            return []
        client = self._auth_client()
        if _use_secure_client():
            out = []
            n = 0
            # Prefer account trades; fall back to list_trades if needed
            try:
                it = client.list_account_trades().iter_items()
            except Exception:
                it = client.list_trades().iter_items()
            for item in it:
                d = _as_dict(item)
                # normalize common field aliases for runner/ledger
                if "asset_id" not in d and d.get("token_id"):
                    d["asset_id"] = d["token_id"]
                if "token_id" not in d and d.get("asset_id"):
                    d["token_id"] = d["asset_id"]
                out.append(d)
                n += 1
                if n >= 500:
                    break
            return out
        return client.get_trades() or []

    def get_earnings_today(self):
        if not self._live_mutations_allowed():
            return None
        client = self._auth_client()
        try:
            if _use_secure_client():
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                return client.get_total_earnings_for_user_for_day(date=day)
            return client.get_total_earnings_for_user_for_day()
        except Exception as e:
            return {"_err": str(e)}
