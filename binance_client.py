"""
Binance USDT-M Futures REST API client.

• Automatic rate-limit tracking (weight-aware).
• Transparent retry with back-off on 429 / 5xx / timeouts.
• Separates closed candles from the still-open candle.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import math
import time
from collections import deque
from threading import Lock
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

_KLINE_FIELDS = (
    "open_time", "open", "high", "low", "close",
    "volume", "close_time", "quote_volume",
    "trades", "taker_buy_base_vol", "taker_buy_quote_vol", "ignore",
)


class BinanceClient:
    """Thin wrapper around the Binance Futures (fapi) REST API."""

    BASE_LIVE    = "https://fapi.binance.com"
    BASE_TESTNET = "https://demo-fapi.binance.com"   # Binance Futures Demo/Testnet
    MAX_WEIGHT_PER_MIN = 2400
    SAFE_WEIGHT_CEILING = 2000

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        delay_ms: int = 100,
        testnet: bool = False,
    ):
        self._delay      = delay_ms / 1000.0
        self._api_secret = api_secret
        self._testnet    = testnet
        self.BASE        = self.BASE_TESTNET if testnet else self.BASE_LIVE

        self._session    = requests.Session()
        self._session.headers["User-Agent"] = "BinanceFuturesScanner/1.0"
        if api_key:
            self._session.headers["X-MBX-APIKEY"] = api_key

        self._weights: deque[tuple[float, int]] = deque()
        self._lock = Lock()

        self._symbols: Optional[List[Dict]] = None
        self._symbols_ts: float = 0.0
        self._precisions: Dict[str, dict] = {}

    def _consume_weight(self, weight: int = 1) -> None:
        with self._lock:
            now = time.time()
            while self._weights and now - self._weights[0][0] > 60:
                self._weights.popleft()
            used = sum(w for _, w in self._weights)
            if used + weight > self.SAFE_WEIGHT_CEILING:
                oldest = self._weights[0][0] if self._weights else now
                sleep = 60.0 - (now - oldest) + 0.5
                if sleep > 0:
                    logger.warning("Rate-limit headroom low — sleeping %.1fs", sleep)
                    time.sleep(sleep)
            self._weights.append((time.time(), weight))

    def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        weight: int = 1,
        retries: int = 3,
    ) -> Any:
        url = f"{self.BASE}{path}"
        for attempt in range(1, retries + 1):
            self._consume_weight(weight)
            time.sleep(self._delay)
            try:
                resp = self._session.get(url, params=params, timeout=30)
                if resp.status_code == 451:
                    logger.error(
                        "HTTP 451 — Binance is blocking this server's IP (geo/legal restriction). "
                        "Sleeping 300s before retry."
                    )
                    time.sleep(300)
                    continue
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    logger.warning("429 from Binance — backing off %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code == 418:
                    logger.error("IP auto-banned — sleeping 120s")
                    time.sleep(120)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning("Timeout %s (attempt %d/%d)", path, attempt, retries)
            except requests.exceptions.ConnectionError as exc:
                logger.warning("Conn error %s: %s (attempt %d/%d)", path, exc, attempt, retries)
            except requests.exceptions.HTTPError:
                if resp.status_code >= 500:
                    logger.warning("Server error %d (attempt %d/%d)", resp.status_code, attempt, retries)
                else:
                    raise
            if attempt < retries:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Failed {path} after {retries} attempts")

    def get_usdt_perpetual_symbols(self, ttl: float = 300) -> List[Dict]:
        """Return list of active USDT perpetual pairs (cached)."""
        now = time.time()
        if self._symbols and now - self._symbols_ts < ttl:
            return self._symbols

        info = self._get("/fapi/v1/exchangeInfo", weight=1)
        result = []
        for s in info["symbols"]:
            if (
                s.get("quoteAsset") == "USDT"
                and s.get("contractType") == "PERPETUAL"
                and s.get("status") == "TRADING"
            ):
                result.append(
                    {
                        "symbol": s["symbol"],
                        "base_asset": s["baseAsset"],
                    }
                )
        self._symbols = result
        self._symbols_ts = now
        logger.info("Loaded %d USDT perpetual symbols from exchange info", len(result))
        return result

    def get_mark_prices(self) -> Dict[str, float]:
        """All mark prices in one call (weight 1)."""
        data = self._get("/fapi/v1/premiumIndex", weight=1)
        return {
            d["symbol"]: float(d["markPrice"])
            for d in data
            if float(d["markPrice"]) > 0
        }

    def get_closed_klines(self, symbol: str, interval: str, count: int) -> List[Dict]:
        """
        Return exactly *count* **closed** candles (newest last).
        """
        raw = self._get(
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": count + 2},
            weight=1 if count + 2 <= 100 else 2,
        )
        now_ms = int(time.time() * 1000)
        closed: list[dict] = []
        for row in raw:
            if int(row[6]) > now_ms:
                continue
            closed.append(
                {
                    "open_time":     int(row[0]),
                    "open":          float(row[1]),
                    "high":          float(row[2]),
                    "low":           float(row[3]),
                    "close":         float(row[4]),
                    "volume":        float(row[5]),
                    "close_time":    int(row[6]),
                    "quote_volume":  float(row[7]),
                    "trades":        int(row[8]),
                }
            )
        return closed[-count:]

    def get_24h_tickers(self) -> Dict[str, dict]:
        """Fetch 24h ticker stats for all USDT futures symbols in one call."""
        data = self._get("/fapi/v1/ticker/24hr", weight=40)
        result = {}
        for d in data:
            result[d["symbol"]] = {
                "price_change_pct": float(d.get("priceChangePercent", 0)),
                "quote_volume_24h": float(d.get("quoteVolume", 0)),
                "volume_24h":       float(d.get("volume", 0)),
                "high_price":       float(d.get("highPrice", 0)),
            }
        return result

    def get_oi_history(self, symbol: str, period: str, limit: int) -> List[Dict]:
        """
        Historical open interest (from /futures/data/ endpoint).
        Returns [] on failure so callers can degrade gracefully.
        """
        try:
            raw = self._get(
                "/futures/data/openInterestHist",
                params={"symbol": symbol, "period": period, "limit": limit},
                weight=1,
            )
            return [
                {
                    "timestamp":     int(e["timestamp"]),
                    "oi":            float(e["sumOpenInterest"]),
                    "oi_value_usdt": float(e["sumOpenInterestValue"]),
                }
                for e in raw
            ]
        except Exception as exc:
            logger.warning("OI history unavailable for %s: %s", symbol, exc)
            return []

    def get_funding_rate(self, symbol: str) -> Optional[float]:
        """
        Fetch current funding rate for a symbol.
        Returns None on failure.
        """
        try:
            data = self._get(
                "/fapi/v1/premiumIndex",
                params={"symbol": symbol},
                weight=1,
            )
            if isinstance(data, list):
                data = data[0]
            return float(data["lastFundingRate"])
        except Exception as exc:
            logger.debug("Funding rate unavailable for %s: %s", symbol, exc)
            return None

    def get_mark_price_single(self, symbol: str) -> Optional[float]:
        """Fetch current mark price for a single symbol."""
        try:
            data = self._get("/fapi/v1/premiumIndex", params={"symbol": symbol}, weight=1)
            if isinstance(data, list):
                data = data[0]
            return float(data["markPrice"])
        except Exception:
            return None

    # ── trading API (signed) ──────────────────────────────────────────

    def has_trading_credentials(self) -> bool:
        """True if both API key and secret are set."""
        return bool(
            self._api_secret
            and self._session.headers.get("X-MBX-APIKEY")
        )

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _post(self, path: str, params: dict, weight: int = 1) -> Optional[Any]:
        url    = f"{self.BASE}{path}"
        signed = self._sign(params.copy())
        for attempt in range(1, 4):
            self._consume_weight(weight)
            time.sleep(self._delay)
            try:
                resp = self._session.post(url, data=signed, timeout=30)
                data = resp.json()
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 60))
                    logger.warning("429 POST %s — backing off %ds", path, wait)
                    time.sleep(wait)
                    continue
                if not resp.ok:
                    logger.error("POST %s error %d: %s", path, resp.status_code, data)
                    return None
                return data
            except Exception as exc:
                logger.warning("POST %s error (attempt %d): %s", path, attempt, exc)
                if attempt < 3:
                    time.sleep(2 ** attempt)
        return None

    def _signed_get(self, path: str, params: Optional[dict] = None, weight: int = 1) -> Optional[Any]:
        p = self._sign(dict(params or {}))
        return self._get(path, params=p, weight=weight)

    def _delete(self, path: str, params: dict, weight: int = 1) -> Optional[Any]:
        url    = f"{self.BASE}{path}"
        signed = self._sign(params.copy())
        for attempt in range(1, 4):
            self._consume_weight(weight)
            time.sleep(self._delay)
            try:
                resp = self._session.delete(url, params=signed, timeout=30)
                if not resp.ok:
                    logger.warning("DELETE %s error %d: %s", path, resp.status_code, resp.text)
                    return None
                return resp.json()
            except Exception as exc:
                logger.warning("DELETE %s error (attempt %d): %s", path, attempt, exc)
                if attempt < 3:
                    time.sleep(2 ** attempt)
        return None

    def get_usdt_balance(self) -> Optional[float]:
        """Return available USDT balance from futures wallet."""
        try:
            data = self._signed_get("/fapi/v2/balance", weight=5)
            if isinstance(data, list):
                for asset in data:
                    if asset.get("asset") == "USDT":
                        return float(asset.get("availableBalance", 0))
            return None
        except Exception as exc:
            logger.warning("get_usdt_balance failed: %s", exc)
            return None

    def set_leverage(self, symbol: str, leverage: int) -> Optional[int]:
        """
        Set leverage for symbol.  Returns the confirmed leverage or None on failure.
        Binance returns an error if leverage exceeds the symbol's max allowed.
        """
        try:
            result = self._post("/fapi/v1/leverage", {
                "symbol":   symbol,
                "leverage": leverage,
            })
            if result and "leverage" in result:
                return int(result["leverage"])
            return None
        except Exception as exc:
            logger.warning("set_leverage %s lev=%d: %s", symbol, leverage, exc)
            return None

    def _load_precisions(self) -> None:
        """Populate self._precisions from exchangeInfo filters."""
        try:
            info = self._get("/fapi/v1/exchangeInfo", weight=1)
            for s in info.get("symbols", []):
                sym  = s["symbol"]
                step = 1.0
                tick = 1e-8
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f.get("stepSize", 1))
                    elif f["filterType"] == "PRICE_FILTER":
                        tick = float(f.get("tickSize", 1e-8))
                price_prec = max(0, round(-math.log10(tick))) if tick > 0 else 8
                self._precisions[sym] = {"step_size": step, "price_precision": price_prec}
        except Exception as exc:
            logger.warning("_load_precisions failed: %s", exc)

    def get_symbol_precision(self, symbol: str) -> tuple:
        """Return (step_size, price_precision) for a symbol."""
        if not self._precisions:
            self._load_precisions()
        info = self._precisions.get(symbol, {})
        return info.get("step_size", 1.0), info.get("price_precision", 8)

    def place_market_order(self, symbol: str, side: str, quantity: float) -> Optional[dict]:
        """Place a MARKET order on USDT-M futures."""
        return self._post("/fapi/v1/order", {
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": quantity,
        })

    def place_stop_market_order(
        self, symbol: str, side: str, quantity: float, stop_price: float
    ) -> Optional[dict]:
        """Place a STOP_MARKET stop-loss order using closePosition=true.

        closePosition=true is used instead of quantity+reduceOnly because:
        - Compatible with both demo-fapi and live fapi (fixes error -4120 on demo)
        - Automatically closes the full remaining position when triggered,
          so it correctly handles partial-close scenarios without needing
          to be re-placed every time remaining_quantity changes.
        - workingType=MARK_PRICE prevents wick-triggered SLs.
        """
        return self._post("/fapi/v1/order", {
            "symbol":        symbol,
            "side":          side,
            "type":          "STOP_MARKET",
            "stopPrice":     stop_price,
            "closePosition": "true",
            "workingType":   "MARK_PRICE",
        })

    def cancel_all_open_orders(self, symbol: str) -> bool:
        """Cancel ALL open orders for a symbol (DELETE /fapi/v1/allOpenOrders).
        Use on any full position close to guarantee no orphan orders remain on Binance,
        even if sl_order_id in the trade record is stale or mismatched after a restart."""
        try:
            result = self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})
            logger.info("cancel_all_open_orders %s: %s", symbol, result)
            return result is not None
        except Exception as exc:
            logger.warning("cancel_all_open_orders %s: %s", symbol, exc)
            return False

    def get_position_risk(self, symbol: str) -> Optional[dict]:
        """Return position risk data for a symbol (positionAmt, etc.)."""
        try:
            data = self._signed_get("/fapi/v2/positionRisk", {"symbol": symbol}, weight=5)
            if isinstance(data, list) and data:
                return data[0]
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("get_position_risk %s: %s", symbol, exc)
            return None

    def get_order_status(self, symbol: str, order_id: int) -> Optional[str]:
        """Return the status string of an order (NEW / FILLED / CANCELED …)."""
        try:
            data = self._signed_get("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
            return data.get("status") if data else None
        except Exception as exc:
            logger.warning("get_order_status %s oid=%s: %s", symbol, order_id, exc)
            return None

    def get_order(self, symbol: str, order_id: int) -> Optional[dict]:
        """Return the full order dict for an order_id (status, avgPrice, etc.)."""
        try:
            return self._signed_get("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
        except Exception as exc:
            logger.warning("get_order %s oid=%s: %s", symbol, order_id, exc)
            return None

    def cancel_order(self, symbol: str, order_id: int) -> bool:
        """Cancel an open order. Returns True on success."""
        try:
            result = self._delete("/fapi/v1/order", {"symbol": symbol, "orderId": order_id})
            return result is not None
        except Exception as exc:
            logger.warning("cancel_order %s oid=%s: %s", symbol, order_id, exc)
            return False

    def place_market_order_reduce(self, symbol: str, side: str, quantity: float) -> Optional[dict]:
        """Place a reduce-only MARKET order (partial or full close)."""
        return self._post("/fapi/v1/order", {
            "symbol":     symbol,
            "side":       side,
            "type":       "MARKET",
            "quantity":   quantity,
            "reduceOnly": "true",
        })
