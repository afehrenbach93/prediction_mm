"""
Weather edge model for Polymarket US daily high-temperature markets (`tc-temp-*`).

Markets: "Highest temperature in <City> on <date>?" sliced into °F buckets, e.g.
  tc-temp-nychigh-2026-06-23-gte84lt85f   -> 84 <= high < 85
  tc-temp-miahigh-2026-06-23-gte97f       -> high >= 97
  tc-temp-nychigh-2026-06-23-lt78f        -> high < 78
Each bucket trades 0..1 (YES = the official daily high lands in that bucket).

Edge thesis: the market prices each bucket; a good forecast (point high + an
uncertainty band) implies a probability per bucket. Where the forecast's bucket
probability materially exceeds the market ASK (net of fee), that's a buy edge.

This module is PURE (no network): the worker supplies the forecast (point high +
sigma) and the live book; these functions parse buckets, turn a forecast into
bucket probabilities, and compute the edge. Validate read-only before funding.
"""
import math
import re

# station code in the slug -> (city label, climate normal daily-high sigma °F).
# sigma is a fallback day-ahead forecast uncertainty when the feed gives no spread.
CITY = {
    "nyc": "New York (KNYC)",
    "mdw": "Chicago (KMDW)",
    "mia": "Miami (KMIA)",
    "lax": "Los Angeles (KLAX)",
    "sfo": "San Francisco (KSFO)",
}

_SLUG_RE = re.compile(r"tc-temp-(?P<stn>[a-z]{3})high-(?P<date>\d{4}-\d{2}-\d{2})-(?P<bucket>.+)$")


def parse_temp_slug(slug: str):
    """-> dict(station, city, date, lo, hi) or None. lo/hi in °F; None = open end.
    Bucket grammar: gtAltB (A<=T<B) / gteAf (T>=A) / ltAf (T<A)."""
    m = _SLUG_RE.match(slug.strip())
    if not m:
        return None
    b = m.group("bucket")
    lo = hi = None
    g = re.search(r"gte?(\d+)", b)
    l = re.search(r"lt(\d+)", b)
    if g:
        lo = float(g.group(1))
    if l:
        hi = float(l.group(1))
    if lo is None and hi is None:
        return None
    stn = m.group("stn")
    return {"station": stn, "city": CITY.get(stn, stn), "date": m.group("date"),
            "lo": lo, "hi": hi}


def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def bucket_probability(forecast_high: float, sigma: float,
                       lo: float | None, hi: float | None,
                       floor: float | None = None) -> float:
    """P(lo <= high < hi) under Normal(forecast_high, sigma). The official high is
    an integer °F, but treating it continuous over 1°F-wide buckets is a fine
    approximation for edge-finding. Open ends (lo/hi None) -> tail probability.

    `floor` = today's observed max-so-far: the daily high CANNOT be below it, so the
    distribution is truncated at `floor` and renormalized over the surviving mass. A
    bucket entirely below the floor is already impossible (prob 0); a bucket containing
    it keeps only its above-floor share. This is the intraday-conditioning lever — by
    mid-afternoon it collapses most of the boundary uncertainty that lost money."""
    if sigma <= 0:   # degenerate point mass at the forecast (half-open: lo<=mu<hi)
        mu = forecast_high if floor is None else max(forecast_high, floor)
        ge_lo = lo is None or mu >= lo
        lt_hi = hi is None or mu < hi
        return 1.0 if (ge_lo and lt_hi) else 0.0
    if floor is not None:
        if hi is not None and hi <= floor:
            return 0.0                       # bucket already surpassed — cannot be the high
        lo = floor if lo is None else max(lo, floor)
    p_hi = _norm_cdf(hi, forecast_high, sigma) if hi is not None else 1.0
    p_lo = _norm_cdf(lo, forecast_high, sigma) if lo is not None else 0.0
    p = p_hi - p_lo
    if floor is not None:                    # renormalize over the surviving mass (H>=floor)
        denom = 1.0 - _norm_cdf(floor, forecast_high, sigma)
        p = p / denom if denom > 1e-6 else (1.0 if lo == floor else 0.0)
    return max(0.0, min(1.0, p))


def buy_edge(model_prob: float, market_ask: float | None, fee: float = 0.0) -> float | None:
    """Edge from BUYING YES at the ask: model_prob - ask - fee. None if no ask.
    Positive => the forecast thinks this bucket is underpriced."""
    if market_ask is None:
        return None
    return model_prob - market_ask - fee


def sell_edge(model_prob: float, market_bid: float | None, fee: float = 0.0) -> float | None:
    """Edge from SELLING YES at the bid (a short-YES / bet-NO): bid - model_prob - fee.
    None if no bid. Positive => the market's bid is above the forecast's true prob, so
    selling into it is +EV (the favorite-longshot bias on thin temp buckets)."""
    if market_bid is None:
        return None
    return market_bid - model_prob - fee


def taker_fee(price: float, contracts: float = 1.0, rate: float = 0.05) -> float:
    """Polymarket US taker fee ~ rate * C * p * (1-p) (per contract, as a $/contract
    fraction when contracts=1)."""
    return rate * contracts * price * (1.0 - price)
