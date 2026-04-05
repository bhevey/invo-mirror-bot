"""
InvoMirror Bot - Binance Execution Client
============================================
Handles spot order execution on Binance.
Includes native stop-limit orders for instant stop-loss protection.
"""

import logging
import hmac
import hashlib
import time
from urllib.parse import urlencode
from typing import Optional
import requests

logger = logging.getLogger("invo_mirror.binance_client")

TICKER_TO_BINANCE = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "BNB": "BNBUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT", "AVAX": "AVAXUSDT", "DOT": "DOTUSDT",
    "LINK": "LINKUSDT", "MATIC": "MATICUSDT", "ARB": "ARBUSDT",
    "AAVE": "AAVEUSDT", "UNI": "UNIUSDT", "OP": "OPUSDT",
    "APT": "APTUSDT", "SUI": "SUIUSDT", "NEAR": "NEARUSDT",
    "FIL": "FILUSDT", "ATOM": "ATOMUSDT", "LTC": "LTCUSDT",
    "BCH": "BCHUSDT", "ETC": "ETCUSDT", "INJ": "INJUSDT",
    "TIA": "TIAUSDT", "SEI": "SEIUSDT", "FET": "FETUSDT",
    "RNDR": "RNDRUSDT", "WIF": "WIFUSDT", "PEPE": "PEPEUSDT",
    "SHIB": "SHIBUSDT", "BONK": "BONKUSDT", "FLOKI": "FLOKIUSDT",
}


class BinanceClient:
    BASE_URL = "https://api.binance.com"

    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key})
        self._exchange_info_cache = {}
        self._time_offset = 0
        self._sync_time()

    def _sync_time(self):
        """Sync local clock with Binance server time to avoid timestamp errors."""
        try:
            resp = requests.get(f"{self.BASE_URL}/api/v3/time", timeout=5)
            if resp.status_code == 200:
                server_time = resp.json()["serverTime"]
                local_time = int(time.time() * 1000)
                self._time_offset = server_time - local_time
                logger.info(f"Binance time synced (offset: {self._time_offset}ms)")
        except Exception as e:
            logger.warning(f"Could not sync Binance server time: {e}")
            self._time_offset = 0

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000) + self._time_offset
        params["recvWindow"] = 10000
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    def _get(self, endpoint, params=None, signed=False):
        url = f"{self.BASE_URL}{endpoint}"
        if params is None:
            params = {}
        if signed:
            params = self._sign(params)
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Binance GET {endpoint}: {e}")
            return None

    def _post(self, endpoint, params, signed=True):
        url = f"{self.BASE_URL}{endpoint}"
        if signed:
            params = self._sign(params)
        try:
            resp = self.session.post(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"Binance POST {endpoint}: {e}")
            if e.response is not None:
                logger.error(f"Body: {e.response.text}")
                try:
                    error_body = e.response.json()
                    return {"error": True, "code": error_body.get("code"), "msg": error_body.get("msg")}
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.error(f"Binance POST {endpoint}: {e}")
            return None

    def _delete(self, endpoint, params, signed=True):
        url = f"{self.BASE_URL}{endpoint}"
        if signed:
            params = self._sign(params)
        try:
            resp = self.session.delete(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Binance DELETE {endpoint}: {e}")
            return None

    def get_account(self):
        return self._get("/api/v3/account", params={}, signed=True)

    def get_usdt_balance(self) -> float:
        account = self.get_account()
        if account:
            for b in account.get("balances", []):
                if b["asset"] == "USDT":
                    return float(b["free"])
        return 0.0

    def get_asset_balance(self, asset: str) -> float:
        account = self.get_account()
        if account:
            for b in account.get("balances", []):
                if b["asset"] == asset.upper():
                    return float(b["free"])
        return 0.0

    def get_price(self, symbol: str) -> Optional[float]:
        data = self._get("/api/v3/ticker/price", {"symbol": symbol})
        return float(data["price"]) if data else None

    def get_symbol_info(self, symbol: str):
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]
        data = self._get("/api/v3/exchangeInfo", {"symbol": symbol})
        if data and data.get("symbols"):
            info = data["symbols"][0]
            self._exchange_info_cache[symbol] = info
            return info
        return None

    def get_lot_size(self, symbol: str) -> dict:
        info = self.get_symbol_info(symbol)
        if info:
            for f in info.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    return {"min_qty": float(f["minQty"]), "max_qty": float(f["maxQty"]),
                            "step_size": float(f["stepSize"])}
        return {"min_qty": 0, "max_qty": 999999, "step_size": 0.001}

    def get_price_filter(self, symbol: str) -> dict:
        info = self.get_symbol_info(symbol)
        if info:
            for f in info.get("filters", []):
                if f["filterType"] == "PRICE_FILTER":
                    return {"min_price": float(f["minPrice"]), "max_price": float(f["maxPrice"]),
                            "tick_size": float(f["tickSize"])}
        return {"min_price": 0, "max_price": 999999, "tick_size": 0.01}

    def get_min_notional(self, symbol: str) -> float:
        info = self.get_symbol_info(symbol)
        if info:
            for f in info.get("filters", []):
                if f["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                    return float(f.get("minNotional", 10))
        return 10.0

    def round_quantity(self, qty: float, step: float) -> float:
        if step == 0: return qty
        precision = len(str(step).rstrip('0').split('.')[-1])
        return round(qty - (qty % step), precision)

    def round_price(self, price: float, tick: float) -> float:
        if tick == 0: return price
        precision = len(str(tick).rstrip('0').split('.')[-1])
        return round(price - (price % tick), precision)

    def market_buy(self, symbol: str, usdt_amount: float):
        logger.info(f"MARKET BUY: {symbol} for ${usdt_amount:.2f} USDT")
        min_notional = self.get_min_notional(symbol)
        if usdt_amount < min_notional:
            logger.error(f"Amount ${usdt_amount} below minimum ${min_notional}")
            return None
        result = self._post("/api/v3/order", {
            "symbol": symbol, "side": "BUY", "type": "MARKET",
            "quoteOrderQty": f"{usdt_amount:.2f}",
        })
        if result:
            qty = float(result.get("executedQty", 0))
            val = float(result.get("cummulativeQuoteQty", 0))
            avg = val / qty if qty > 0 else 0
            logger.info(f"BUY filled: {qty} {symbol} @ ${avg:.4f} (${val:.2f})")
        return result

    def market_sell(self, symbol: str, quantity: float):
        lot = self.get_lot_size(symbol)
        quantity = self.round_quantity(quantity, lot["step_size"])
        logger.info(f"MARKET SELL: {quantity} {symbol}")
        if quantity < lot["min_qty"]:
            logger.error(f"Qty {quantity} below min {lot['min_qty']}")
            return None
        result = self._post("/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "MARKET",
            "quantity": f"{quantity}",
        })
        if result:
            qty = float(result.get("executedQty", 0))
            val = float(result.get("cummulativeQuoteQty", 0))
            avg = val / qty if qty > 0 else 0
            logger.info(f"SELL filled: {qty} {symbol} @ ${avg:.4f} (${val:.2f})")
        return result

    def place_stop_loss_order(self, symbol, quantity, stop_price, sell_price):
        lot = self.get_lot_size(symbol)
        pf = self.get_price_filter(symbol)
        quantity = self.round_quantity(quantity, lot["step_size"])
        stop_price = self.round_price(stop_price, pf["tick_size"])
        sell_price = self.round_price(sell_price, pf["tick_size"])
        logger.info(f"STOP-LOSS order: {symbol} qty={quantity} stop=${stop_price} limit=${sell_price}")
        if quantity < lot["min_qty"]:
            logger.error(f"Stop-loss qty {quantity} below min for {symbol}")
            return None
        return self._post("/api/v3/order", {
            "symbol": symbol, "side": "SELL", "type": "STOP_LOSS_LIMIT",
            "timeInForce": "GTC", "quantity": f"{quantity}",
            "stopPrice": f"{stop_price}", "price": f"{sell_price}",
        })

    def cancel_order(self, symbol: str, order_id: int):
        logger.info(f"Cancelling order {order_id} for {symbol}")
        return self._delete("/api/v3/order", {"symbol": symbol, "orderId": order_id})

    def get_order_status(self, symbol: str, order_id: int):
        return self._get("/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    @staticmethod
    def invo_ticker_to_binance(ticker: str) -> Optional[str]:
        ticker = ticker.upper().strip()
        return TICKER_TO_BINANCE.get(ticker, f"{ticker}USDT")
