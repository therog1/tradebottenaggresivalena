"""
OKX REST Client for Aggressive Execution (Subaccount: Lena)
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
        return time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())

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
            "User-Agent": "TradebottenLena/1.0"
        }
        if self.is_demo:
            headers["x-simulated-trading"] = "1"

        try:
            resp = requests.request(method, url, headers=headers, data=body_str if body_str else None, timeout=10)
            return resp.json()
        except Exception as e:
            return {"code": "-1", "msg": str(e), "data": []}

    def get_balance(self, ccy: str = "USDC") -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/account/balance?ccy={ccy}")

    def get_ticker(self, inst_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/market/ticker?instId={inst_id}")

    def get_orderbook(self, inst_id: str, sz: int = 20) -> Dict[str, Any]:
        return self._request("GET", f"/api/v5/market/books?instId={inst_id}&sz={sz}")

    def place_order(self, inst_id: str, td_mode: str, side: str, ord_type: str, sz: str, px: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "instId": inst_id,
            "tdMode": td_mode,
            "side": side,
            "ordType": ord_type,
            "sz": sz
        }
        if px and ord_type in ("limit", "post_only"):
            payload["px"] = px
        return self._request("POST", "/api/v5/trade/order", data=payload)

    def get_open_orders(self, inst_id: Optional[str] = None) -> Dict[str, Any]:
        path = "/api/v5/trade/orders-pending"
        if inst_id:
            path += f"?instId={inst_id}"
        return self._request("GET", path)

    def cancel_order(self, inst_id: str, ord_id: str) -> Dict[str, Any]:
        return self._request("POST", "/api/v5/trade/cancel-order", data={"instId": inst_id, "ordId": ord_id})
