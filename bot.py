"""Aggressive Momentum & Orderbook Flow Scalper (Lena)
Ken Griffin Inspired Execution Engine with 3x-5x Futures (USD-Margined X-Perpetuals)
Supports OKX EEA USDC Collateral Multi-Currency Margin
"""

import os
import sys
import time
import json
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from okx_client import OKXClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [LENA-FUTURES-3X-5X] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "active_positions.json")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "trades_history.json")

# Contract multiplier table for OKX UM X-Perp contracts
CONTRACT_SPECS = {
    "BTC-USD_UM_XPERP-310404": {"ctVal": 0.0001, "coin": "BTC", "minSz": 1},
    "ETH-USD_UM_XPERP-310404": {"ctVal": 0.001, "coin": "ETH", "minSz": 1},
    "SOL-USD_UM_XPERP-310404": {"ctVal": 0.01, "coin": "SOL", "minSz": 1},
    "SUI-USD_UM_XPERP-310404": {"ctVal": 1.0, "coin": "SUI", "minSz": 1},
    "DOGE-USD_UM_XPERP-310404": {"ctVal": 10.0, "coin": "DOGE", "minSz": 1},
    "XRP-USD_UM_XPERP-310404": {"ctVal": 1.0, "coin": "XRP", "minSz": 1},
    "PEPE-USD_UM_XPERP-310404": {"ctVal": 1000000.0, "coin": "PEPE", "minSz": 1},
    "AVAX-USD_UM_XPERP-310530": {"ctVal": 10.0, "coin": "AVAX", "minSz": 1},
    "NEAR-USD_UM_XPERP-310613": {"ctVal": 1.0, "coin": "NEAR", "minSz": 1},
    "LINK-USD_UM_XPERP-310523": {"ctVal": 1.0, "coin": "LINK", "minSz": 1},
}

DEFAULT_FUTURES_SYMBOLS = ",".join(CONTRACT_SPECS.keys())

class LenaAggressiveBot:
    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY", "")
        self.secret_key = os.getenv("OKX_API_SECRET", "")
        self.passphrase = os.getenv("OKX_API_PASSPHRASE", "")
        self.is_demo = os.getenv("OKX_IS_DEMO", "false").lower() == "true"
        self.base_url = os.getenv("OKX_BASE_URL", "https://eea.okx.com")

        self.symbols = [s.strip() for s in os.getenv("SYMBOLS", DEFAULT_FUTURES_SYMBOLS).split(",") if s.strip()]
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PERCENT", "0.018"))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PERCENT", "0.040"))
        self.trailing_stop_pct = float(os.getenv("TRAILING_STOP_PERCENT", "0.009"))
        self.imbalance_threshold = float(os.getenv("IMBALANCE_THRESHOLD", "0.45"))
        self.risk_per_trade_pct = float(os.getenv("RISK_PER_TRADE_PERCENT", "0.35"))
        self.kill_switch_min_equity = float(os.getenv("KILL_SWITCH_MIN_EQUITY", "25.0"))
        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
        self.min_leverage = int(os.getenv("MIN_LEVERAGE", "3"))
        self.max_leverage = int(os.getenv("MAX_LEVERAGE", "5"))
        self.default_leverage = int(os.getenv("DEFAULT_LEVERAGE", "3"))
        self.poll_interval = float(os.getenv("POLL_INTERVAL_SECONDS", "3.0"))

        self.client = OKXClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            passphrase=self.passphrase,
            is_demo=self.is_demo,
            base_url=self.base_url
        )

        self.active_positions: Dict[str, Dict[str, Any]] = self._load_positions()
        self.trades_history: List[Dict[str, Any]] = self._load_history()

    def _load_positions(self) -> Dict[str, Dict[str, Any]]:
        if os.path.exists(POSITIONS_FILE):
            try:
                with open(POSITIONS_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Could not load active positions: {e}")
        return {}

    def _load_history(self) -> List[Dict[str, Any]]:
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.warning(f"Could not load trade history: {e}")
        return []

    def save_state(self):
        try:
            with open(POSITIONS_FILE, "w") as f:
                json.dump(self.active_positions, f, indent=2)
            with open(HISTORY_FILE, "w") as f:
                json.dump(self.trades_history, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving state to disk: {e}")

    def get_equity(self) -> float:
        res = self.client.get_balance("USDC")
        if res.get("code") == "0" and res.get("data"):
            details = res["data"][0].get("details", [])
            for d in details:
                if d.get("ccy") == "USDC":
                    avail = float(d.get("availBal", 0.0))
                    eq = float(d.get("eq", 0.0))
                    return max(avail, eq)
            total_eq = float(res["data"][0].get("totalEq", 0.0))
            if total_eq > 0:
                return total_eq
        return 0.0

    def compute_orderbook_imbalance(self, inst_id: str) -> float:
        book = self.client.get_orderbook(inst_id, sz=15)
        if book.get("code") != "0" or not book.get("data"):
            return 0.0
        bids = book["data"][0].get("bids", [])
        asks = book["data"][0].get("asks", [])
        bid_vol = sum(float(b[1]) for b in bids[:10])
        ask_vol = sum(float(a[1]) for a in asks[:10])
        total_vol = bid_vol + ask_vol
        if total_vol == 0:
            return 0.0
        return (bid_vol - ask_vol) / total_vol

    def calculate_leverage(self, obi: float) -> int:
        """Scale leverage dynamically between min_leverage (3x) and max_leverage (5x)"""
        if abs(obi) >= 0.75:
            return self.max_leverage  # 5x on strong flow
        elif abs(obi) >= 0.60:
            return 4  # 4x on solid flow
        else:
            return self.min_leverage  # 3x on base threshold

    def scan_and_execute(self):
        equity = self.get_equity()
        logging.info(f"Subaccount USDC Equity: ${equity:.2f} | Active Positions: {len(self.active_positions)}/{self.max_open_positions}")

        if equity < self.kill_switch_min_equity:
            logging.critical(f"Equity (${equity:.2f}) below kill switch threshold (${self.kill_switch_min_equity:.2f})! Halting new executions.")
            return

        for symbol in self.symbols:
            try:
                ticker_resp = self.client.get_ticker(symbol)
                if ticker_resp.get("code") != "0" or not ticker_resp.get("data"):
                    continue

                last_price = float(ticker_resp["data"][0].get("last", 0))
                if last_price <= 0:
                    continue

                obi = self.compute_orderbook_imbalance(symbol)
                logging.info(f"[{symbol}] Price: {last_price} | Order Book Imbalance: {obi:+.2%}")

                is_futures = ("_UM_XPERP" in symbol or "-SWAP" in symbol)
                if abs(obi) >= self.imbalance_threshold and symbol not in self.active_positions:
                    if len(self.active_positions) >= self.max_open_positions:
                        logging.info(f"Max active positions reached ({len(self.active_positions)}/{self.max_open_positions}). Skipping {symbol}.")
                        continue

                    side = "buy" if obi > 0 else "sell"
                    pos_side = "net"
                    leverage = self.calculate_leverage(obi) if is_futures else 1

                    if is_futures:
                        lev_res = self.client.set_leverage(lever=str(leverage), mgn_mode="cross", inst_id=symbol)
                        logging.info(f"Configured {leverage}x Cross Leverage for {symbol}: {lev_res.get('msg', 'OK')}")

                    trade_budget_usdc = min(equity * self.risk_per_trade_pct, equity * 0.90)
                    if trade_budget_usdc < 5.0:
                        continue

                    spec = CONTRACT_SPECS.get(symbol, {"ctVal": 1.0, "minSz": 1})
                    contract_val_usd = spec["ctVal"] * last_price
                    if contract_val_usd <= 0:
                        continue

                    effective_purchasing_power = trade_budget_usdc * leverage
                    num_contracts = max(spec["minSz"], int(effective_purchasing_power / contract_val_usd))

                    logging.info(f"🚀 {side.upper()} SIGNAL on {symbol}! OBI={obi:+.2%} | Leverage={leverage}x | Executing {num_contracts} contracts (~${num_contracts * contract_val_usd:.2f} notional)")

                    order_res = self.client.place_order(
                        inst_id=symbol,
                        td_mode="cross" if is_futures else "cash",
                        side=side,
                        ord_type="market",
                        sz=str(num_contracts) if is_futures else f"{trade_budget_usdc:.2f}",
                        pos_side=pos_side
                    )
                    logging.info(f"OKX Order Response: {order_res}")

                    if order_res.get("code") == "0":
                        self.active_positions[symbol] = {
                            "entry_price": last_price,
                            "high_price": last_price,
                            "low_price": last_price,
                            "side": side.upper(),
                            "leverage": leverage,
                            "num_contracts": num_contracts,
                            "allocated_usdc": trade_budget_usdc,
                            "time": time.time(),
                            "order_res": order_res
                        }
                        self.save_state()

                # Manage active positions (Trailing Stop & Take Profit)
                if symbol in self.active_positions:
                    pos = self.active_positions[symbol]
                    entry_px = pos["entry_price"]
                    side = pos.get("side", "BUY")

                    lev = pos.get("leverage", 1)
                    raw_move = (last_price - entry_px) / entry_px if side == "BUY" else (entry_px - last_price) / entry_px
                    leveraged_pnl = raw_move * lev

                    if last_price > pos.get("high_price", entry_px):
                        pos["high_price"] = last_price
                    if last_price < pos.get("low_price", entry_px):
                        pos["low_price"] = last_price

                    high_px = pos.get("high_price", entry_px)
                    trailing_drop = (high_px - last_price) / high_px if high_px > 0 else 0

                    if leveraged_pnl >= self.take_profit_pct:
                        logging.info(f"🎯 TAKE PROFIT TRIGGERED on {symbol}! Gain: +{leveraged_pnl*100:.2f}%. Closing position.")
                        self.close_position(symbol, last_price=last_price, reason="TAKE_PROFIT")
                    elif leveraged_pnl <= -self.stop_loss_pct:
                        logging.warning(f"🛑 STOP LOSS TRIGGERED on {symbol}! Loss: {leveraged_pnl*100:.2f}%. Cutting loss.")
                        self.close_position(symbol, last_price=last_price, reason="STOP_LOSS")
                    elif leveraged_pnl > 0.02 and trailing_drop >= self.trailing_stop_pct:
                        logging.info(f"🔒 TRAILING PROFIT LOCK TRIGGERED on {symbol}! Locked in +{leveraged_pnl*100:.2f}%.")
                        self.close_position(symbol, last_price=last_price, reason="TRAILING_STOP")

            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}")

    def close_position(self, symbol: str, last_price: Optional[float] = None, reason: str = "MANUAL_CLOSE"):
        if symbol not in self.active_positions:
            return

        pos = self.active_positions[symbol]
        side = pos.get("side", "BUY")
        close_side = "sell" if side == "BUY" else "buy"
        num_contracts = pos.get("num_contracts", 1)
        is_futures = ("_UM_XPERP" in symbol or "-SWAP" in symbol)

        if not last_price:
            ticker = self.client.get_ticker(symbol)
            last_price = float(ticker.get("data", [{}])[0].get("last", pos["entry_price"]))

        logging.info(f"Closing position {symbol} ({side}) at market price {last_price} (Reason: {reason})...")

        order_res = self.client.place_order(
            inst_id=symbol,
            td_mode="cross" if is_futures else "cash",
            side=close_side,
            ord_type="market",
            sz=str(num_contracts) if is_futures else f"{pos.get('allocated_usdc', 10.0):.2f}",
            pos_side="net"
        )
        logging.info(f"Close Order Response: {order_res}")

        lev = pos.get("leverage", 1)
        raw_move = (last_price - pos["entry_price"]) / pos["entry_price"] if side == "BUY" else (pos["entry_price"] - last_price) / pos["entry_price"]
        pnl_pct = raw_move * lev * 100
        realized_pnl = (pos.get("allocated_usdc", 15.0) * pnl_pct) / 100

        closed_trade = {
            "id": f"TRD-LENA-{int(time.time()*1000)}",
            "bot": "Aggressiva Lena",
            "symbol": symbol,
            "side": side,
            "leverage": lev,
            "entry_price": pos["entry_price"],
            "exit_price": last_price,
            "size_units": num_contracts,
            "notional_usd": round(pos.get("allocated_usdc", 15.0) * lev, 2),
            "realized_pnl": round(realized_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": reason,
            "entry_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(pos.get("time", time.time()))),
            "exit_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "CLOSED"
        }
        self.trades_history.insert(0, closed_trade)
        del self.active_positions[symbol]
        self.save_state()
        logging.info(f"✅ Closed trade recorded: PnL=${realized_pnl:+.2f} ({pnl_pct:+.2f}%) with {lev}x leverage")

    def sync_exchange_history(self):
        logging.info("Querying OKX for recent futures and order history...")
        history_resp = self.client.get_order_history(inst_type="FUTURES", limit=20)
        if history_resp.get("code") == "0" and history_resp.get("data"):
            for o in history_resp["data"]:
                fill_px = float(o.get("fillPx", o.get("avgPx", 0)))
                sz = float(o.get("accFillSz", o.get("sz", 0)))
                if fill_px > 0 and sz > 0:
                    trade_record = {
                        "id": f"OKX-{o.get('ordId', int(time.time()))}",
                        "bot": "Aggressiva Lena",
                        "symbol": o.get("instId"),
                        "side": o.get("side", "").upper(),
                        "leverage": self.min_leverage,
                        "entry_price": fill_px,
                        "exit_price": fill_px,
                        "size_units": sz,
                        "notional_usd": round(fill_px * sz, 2),
                        "realized_pnl": 0.0,
                        "pnl_pct": 0.0,
                        "exit_reason": "EXCHANGE_FILL",
                        "entry_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(o.get("cTime", 0))/1000)),
                        "exit_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(o.get("uTime", 0))/1000)),
                        "status": "CLOSED"
                    }
                    if not any(t.get("id") == trade_record["id"] for t in self.trades_history):
                        self.trades_history.append(trade_record)
            self.save_state()

    def run(self, max_cycles=None, sync_history=False):
        if sync_history:
            self.sync_exchange_history()
        logging.info(f"Starting Aggressive Lena Futures Execution Loop (cycles={max_cycles or 'continuous'})...")
        cycle = 0
        while True:
            cycle += 1
            try:
                logging.info(f"--- SCALP SCAN CYCLE #{cycle} ---")
                self.scan_and_execute()
            except Exception as err:
                logging.error(f"Execution error in cycle {cycle}: {err}")
            if max_cycles and cycle >= max_cycles:
                logging.info(f"Completed {max_cycles} scheduled scalp cycles successfully.")
                break
            time.sleep(self.poll_interval)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Lena Aggressive Futures Scalper")
    parser.add_argument("--cycles", type=int, default=None, help="Number of scan cycles to run before exiting")
    parser.add_argument("--sync-history", action="store_true", help="Sync exchange fills and order history on startup")
    args = parser.parse_args()
    bot = LenaAggressiveBot()
    bot.run(max_cycles=args.cycles, sync_history=args.sync_history)
