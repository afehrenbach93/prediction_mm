"""
Read-only Polymarket US (CFTC/QCEX-regulated) client.

Validate-first: this client is intentionally READ-ONLY for the assessment phase
— it has public market/book/incentive readers plus a signed GET for account and
incentive-earnings endpoints. It places NO orders. Order placement would be a
separate, deliberate build only if the economics validate.

Auth (ED25519, per docs.polymarket.us/api-reference/authentication):
  message   = f"{timestamp_ms}{METHOD}{path}"        # path only, no query string
  signature = base64( ed25519_sign(message) )
  key       = Ed25519PrivateKey.from_private_bytes( base64(SECRET)[:32] )
  headers   = X-PM-Access-Key (key id), X-PM-Timestamp (ms), X-PM-Signature
  timestamp must be within 30s of server time.

Hosts:
  public  = https://gateway.polymarket.us   (markets, books, incentives — no auth)
  auth    = https://api.polymarket.us       (accounts, earnings, portfolio — signed)
"""
import base64
import json
import time
import urllib.parse
import urllib.request
import urllib.error

from cryptography.hazmat.primitives.asymmetric import ed25519

PUBLIC = "https://gateway.polymarket.us"
AUTH = "https://api.polymarket.us"
UA = "kalshi-mm-bot/polymarket-research"


class PolyClient:
    def __init__(self, api_key_id: str = "", secret_b64: str = "",
                 public_base: str = PUBLIC, auth_base: str = AUTH,
                 live: bool = False):
        self.api_key_id = api_key_id
        self._signer = None
        if secret_b64:
            seed = base64.b64decode(secret_b64)[:32]
            self._signer = ed25519.Ed25519PrivateKey.from_private_bytes(seed)
        self.public_base = public_base
        self.auth_base = auth_base
        # HARD SAFETY GATE: order mutations are no-ops unless live is True.
        # Default False = shadow. The worker sets this only from BOT_MODE=live.
        self.live = live
        self.shadow_orders: list[dict] = []   # recorded intended orders in shadow

    # ---- low-level ----
    def _get(self, url: str, headers: dict | None = None):
        req = urllib.request.Request(url, headers={"User-Agent": UA,
                                                   "Accept": "application/json",
                                                   **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {"_raw": "(non-json error body)"}
        except Exception as e:
            return None, {"_err": str(e)}

    def public_get(self, path: str, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return self._get(self.public_base + path + q)

    def signed_get(self, path: str, **params):
        """Signed GET against the auth host. Signs the PATH ONLY (no query)."""
        if not self._signer or not self.api_key_id:
            return None, {"_err": "no credentials configured"}
        ts = str(int(time.time() * 1000))
        msg = f"{ts}GET{path}".encode()
        sig = base64.b64encode(self._signer.sign(msg)).decode()
        headers = {"X-PM-Access-Key": self.api_key_id,
                   "X-PM-Timestamp": ts,
                   "X-PM-Signature": sig}
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return self._get(self.auth_base + path + q, headers)

    def signed_post(self, path: str, body: dict):
        """Signed POST against the auth host. Signs ts+POST+path (body sent
        separately, per the auth docs)."""
        if not self._signer or not self.api_key_id:
            return None, {"_err": "no credentials configured"}
        ts = str(int(time.time() * 1000))
        msg = f"{ts}POST{path}".encode()
        sig = base64.b64encode(self._signer.sign(msg)).decode()
        headers = {"X-PM-Access-Key": self.api_key_id,
                   "X-PM-Timestamp": ts,
                   "X-PM-Signature": sig,
                   "Content-Type": "application/json"}
        data = json.dumps(body).encode()
        req = urllib.request.Request(self.auth_base + path, data=data,
                                     headers={"User-Agent": UA,
                                              "Accept": "application/json",
                                              **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {"_raw": "(non-json error body)"}
        except Exception as e:
            return None, {"_err": str(e)}

    # ---- order mutations (SHADOW-GATED) ----
    def place_order(self, market_slug: str, intent: str, price: float,
                    quantity: float, post_only: bool = True,
                    order_type: str = "ORDER_TYPE_LIMIT",
                    tif: str = "TIME_IN_FORCE_GOOD_TILL_CANCEL"):
        """Place an order. In SHADOW (self.live False) this records the intended
        order and returns a synthetic ack WITHOUT contacting the exchange — a real
        order is impossible unless live was explicitly enabled. `intent` is one of
        ORDER_INTENT_BUY_LONG / SELL_LONG / BUY_SHORT / SELL_SHORT. post_only=True
        sets participateDontInitiate so the order never crosses (maker-only)."""
        body = {
            "marketSlug": market_slug,
            "type": order_type,
            "price": {"value": f"{price:.4f}", "currency": "USD"},
            "quantity": quantity,
            "tif": tif,
            "intent": intent,
            # a bot must declare itself automated, not manual (compliance)
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATED",
            "participateDontInitiate": bool(post_only),
        }
        if not self.live:
            rec = {"ts": time.time(), "shadow": True, **body}
            self.shadow_orders.append(rec)
            return 200, {"shadow": True, "orderId": f"shadow-{len(self.shadow_orders)}",
                         "order": body}
        return self.signed_post("/v1/orders", body)

    def cancel_order(self, order_id: str, market_slug: str = ""):
        """Cancel a resting order. The exchange REQUIRES the order's marketSlug in
        the body (an empty body 400s — this was the order-accumulation bug)."""
        if not self.live:
            self.shadow_orders.append({"ts": time.time(), "shadow": True,
                                       "cancel": order_id})
            return 200, {"shadow": True, "cancelled": order_id}
        return self.signed_post(f"/v1/order/{order_id}/cancel",
                                {"marketSlug": market_slug})

    def get_open_orders(self):
        return self.signed_get("/v1/orders/open")

    def get_order(self, order_id: str):
        return self.signed_get(f"/v1/order/{order_id}")

    # ---- public readers ----
    def get_incentives(self, statuses="active", page_size=100):
        """All incentive programs (paginated, deduped by (marketSlug, programId))."""
        out, token, seen = {}, None, set()
        for _ in range(40):
            params = {"statuses": statuses, "pageSize": page_size}
            if token:
                params["pageToken"] = token
            s, d = self.public_get("/v1/incentives", **params)
            if s != 200 or not isinstance(d, dict):
                break
            page = d.get("programs", [])
            if not page:
                break
            ids = []
            for pm in page:
                slug = pm.get("marketSlug")
                for tp in pm.get("timePeriods", []):
                    pid = tp.get("programId")
                    ids.append((slug, pid))
                    out[(slug, pid)] = {"marketSlug": slug, **tp}
            token = d.get("nextPageToken") or d.get("pageToken")
            key = tuple(ids[:5])
            if not token or key in seen:
                break
            seen.add(key)
        return list(out.values())

    def get_market(self, slug: str):
        s, d = self.public_get(f"/v1/market/slug/{slug}")
        return d.get("market") if isinstance(d, dict) else None

    def get_book(self, slug: str):
        """Order book for a market by slug. Returns (bids, offers) as lists of
        (price float, size float), best-first; ([],[]) on error."""
        s, d = self.public_get(f"/v1/markets/{slug}/book")
        md = d.get("marketData", {}) if isinstance(d, dict) else {}

        def lvls(arr):
            out = []
            for e in arr or []:
                try:
                    out.append((float(e["px"]["value"]), float(e["qty"])))
                except Exception:
                    pass
            return out
        return lvls(md.get("bids")), lvls(md.get("offers"))

    # ---- signed readers (account / earnings) ----
    def get_accounts(self):
        return self.signed_get("/v1/accounts")

    def get_incentive_earnings(self, **params):
        return self.signed_get("/v1/incentives/earnings", **params)

    def get_positions(self):
        return self.signed_get("/v1/portfolio/positions")


def load_env(path=".env") -> dict:
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


def from_env(env: dict | None = None) -> "PolyClient":
    env = env or load_env()
    return PolyClient(api_key_id=env.get("POLYMARKET_API_KEY", ""),
                      secret_b64=env.get("POLYMARKET_SECRET", ""))
