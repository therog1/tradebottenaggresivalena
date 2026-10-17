"""OKX REST Client for Aggressive Execution (Subaccount: Lena)
Enhanced with Futures, Perpetual Swaps, Leverage & Order History
"""
import os
import time
import base64
import hmac
import hashlib
from typing import Dict, Any, Optional
import requests

class OKXClient:
    def __init__(self, api_key: str, secret_key: str, passphrase: str, is_demo: bool = False, base_url: str = "https://eea.okx.com"):
        self.api_key = api_key
        self.secret_key = secret_key
        self.passphrase = passphrase
        self.is_demo = is_demo
        self.base_url = base_url.rstrip("/")

    def _get_timestamp(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

    def _sign(self, timestamp: str, method: str, request_path: str, body: str = "") -> str:
        message = timestamp + method.upper() + request_path + body
        mac = hmac.new(self.secret_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        query_str = ""
        if params:
            import urllib.parse
            query_str = "?" + urllib.parse.urlencode(params)
            url += query_str

        body_str = ""
        if data:
            import json
            body_str = json.dumps(data)

        timestamp = self._get_timestamp()
        sign = self._sign(timestamp, method, path + query_str, body_str)

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
            "User-Agent": "TradebottenLena/2.0"
        }
        if self.is_demo:
            headers["x-simulated-trading"] = "1"

        try:
            resp = requests.request(method, url, headers=headers, data=body_str if body_str else None, timeout=10)
            if resp.status_code == 429:
                return {"code": "429", "msg": "rate limited", "data": []}
            return resp.json()
        except Exception as e:
            return {"code": "-1", "msg": str(e), "data": []}

    def get_balance(self, ccy: Optional[str] = None) -> Dict[str, Any]:
        path = "/api/v5/account/balance"
        if ccy:
            path += f"?ccy={ccy}"
        return self._request("GET", path)

    def get_ticker(self, inst_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/market/ticker?instId={inst_id}")

    def get_candles(self, inst_id: str, bar: str = "1H", limit: int = 100) -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}")

    def get_orderbook(self, inst_id: str, sz: int = 20) -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/market/books?instId={inst_id}&sz={sz}")

    def set_leverage(self, lever: str, mgn_mode: str = "cross", inst_id: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "lever": str(lever),
            "mgnMode": mgn_mode
        }
        if inst_id:
            payload["instId"] = inst_id
        return self._request("POST", "/api/v5/account/set-leverage", data=payload)

    def place_order(self, inst_id: str, td_mode: str, side: str, ord_type: str, sz: str, pos_side: str = "net", px: Optional[str] = None, tgt_ccy: Optional[str] = None,
                    cl_ord_id: Optional[str] = None, attach_algo_ords: Optional[list] = None, reduce_only: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": sz
        }
        if pos_side:
            payload["posSide"] = pos_side
        if tgt_ccy:
            payload["tgtCcy"] = tgt_ccy
        if px and ord_type in ("limit", "post_only"):
            payload["px"] = px
        if cl_ord_id:
            payload["clOrdId"] = cl_ord_id
        if attach_algo_ords:
            payload["attachAlgoOrds"] = attach_algo_ords
        if reduce_only:
            payload["reduceOnly"] = True
        return self._request("POST", "/api/v5/trade/order", data=payload)

    def get_order(self, inst_id: str, ord_id: Optional[str] = None, cl_ord_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"instId": inst_id}
        if ord_id:
            params["ordId"] = ord_id
        if cl_ord_id:
            params["clOrdId"] = cl_ord_id
        return self._request("GET", "/api/v5/trade/order", params=params)

    def get_algo_orders_pending(self, ord_type: str = "conditional,oco", inst_type: Optional[str] = None) -> Dict[str, Any]:
        params = {"ordType": ord_type}
        if inst_type:
            params["instType"] = inst_type
        return self._request("GET", "/api/v5/trade/orders-algo-pending", params=params)

    def place_algo_order(self, inst_id: str, td_mode: str, side: str, sz: str, sl_trigger_px: Optional[str] = None,
                         tp_trigger_px: Optional[str] = None, pos_side: str = "net") -> Dict[str, Any]:
        """Fristående SL/TP (reduce-only, market vid trigger). Används om attachAlgoOrds saknas på fylld position."""
        payload: Dict[str, Any] = {"instId": inst_id, "tdMode": td_mode, "side": side, "sz": sz,
                                   "posSide": pos_side, "reduceOnly": True,
                                   "ordType": "oco" if (sl_trigger_px and tp_trigger_px) else "conditional"}
        if sl_trigger_px:
            payload.update({"slTriggerPx": sl_trigger_px, "slOrdPx": "-1", "slTriggerPxType": "last"})
        if tp_trigger_px:
            payload.update({"tpTriggerPx": tp_trigger_px, "tpOrdPx": "-1", "tpTriggerPxType": "last"})
        return self._request("POST", "/api/v5/trade/order-algo", data=payload)

    def get_positions(self, inst_type: str = "FUTURES") -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/account/positions?instType={inst_type}")

    def get_open_orders(self, inst_id: Optional[str] = None) -> Dict[str, Any]:
        path = "/api/v5/trade/orders-pending"
        if inst_id:
            path += f"?instId={inst_id}"
        return self._request("GET", path)

    def get_order_history(self, inst_type: str = "FUTURES", inst_id: Optional[str] = None, state: str = "filled", limit: int = 50) -> Dict[str, Any]:
        params = {"instType": inst_type, "state": state, "limit": str(limit)}
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", "/api/v5/trade/orders-history", params=params)

    def get_fills(self, inst_type: str = "FUTURES", inst_id: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        params = {"instType": inst_type, "limit": str(limit)}
        if inst_id:
            params["instId"] = inst_id
        return self._request("GET", "/api/v5/trade/fills", params=params)

    def cancel_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        return self._request("POST", "/api/v5/trade/cancel-order", data={"instId": inst_id, "ordId": ord_id})
