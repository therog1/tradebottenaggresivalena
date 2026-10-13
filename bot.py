"""
Aggressive Momentum & Orderbook Flow Scalper (Lena)
Ken Griffin Inspired Execution Engine with Complete Position & Order History Persistence
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
    format="%(asctime)s [%(levelname)s] [LENA-AGGRESSIVE] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

POSITIONS_FILE = os.path.join(os.path.dirname(__file__), "active_positions.json")
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "trades_history.json")

class LenaAggressiveBot:
    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY", "")
        self.secret_key = os.getenv("OKX_API_SECRET", "")
        self.passphrase = os.getenv("OKX_API_PASSPHRASE", "")
        self.is_demo = os.getenv("OKX_IS_DEMO", "false").lower() == "true"
        self.base_url = os.getenv("OKX_BASE_URL", "https://eea.okx.com")
        self.symbols = [s.strip() for s in os.getenv("SYMBOLS", "BTC-USDC,ETH-USDC,SOL-USDC,NEAR-USDC,SUI-USDC,DOGE-USDC,XRP-USDC,AVAX-USDC,LINK-USDC,ADA-USDC,RENDER-USDC,ARB-USDC,TAO-USDC,ONDO-USDC,ENA-USDC,PUMP-USDC,FET-USDC,UNI-USDC,STRK-USDC,AAVE-USDC,PEPE-USDC,APT-USDC,SHIB-USDC,DOT-USDC,TIA-USDC,FIL-USDC,WIF-USDC,WLD-USDC,CRV-USDC,ICP-USDC").split(",") if s.strip()]
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PERCENT", "0.018"))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PERCENT", "0.040"))
        self.trailing_stop_pct = float(os.getenv("TRAILING_STOP_PERCENT", "0.009"))
        self.imbalance_threshold = float(os.getenv("IMBALANCE_THRESHOLD", "0.50"))
        self.risk_per_trade_pct = float(os.getenv("RISK_PER_TRADE_PERCENT", "0.35"))
        self.kill_switch_min_equity = float(os.getenv("KILL_SWITCH_MIN_EQUITY", "35.0"))
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
                    return float(d.get("eq", d.get("availBal", 0.0)))
            total_eq = res["data"][0].get("totalEq", 0.0)
            return float(total_eq)
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
        # Positive = strong buying pressure, Negative = selling pressure
        return (bid_vol - ask_vol) / total_vol

    def scan_and_execute(self):
        equity = self.get_equity()
        logging.info(f"Subaccount Stack Equity: {equity:.2f} USDC | Active Positions: {len(self.active_positions)} | Closed Trades: {len(self.trades_history)}")

        # Emergency Kill Switch Check (Only trigger if free USDC is zero AND no active positions exist, or total eq < 10)
        if equity > 0 and equity < self.kill_switch_min_equity and len(self.active_positions) == 0:
            logging.critical(f"KILL-SWITCH TRIGGERED: Equity ({equity:.2f} USDC) is below minimum threshold ({self.kill_switch_min_equity} USDC). Halting execution!")
            return

        for symbol in self.symbols:
            try:
                obi = self.compute_orderbook_imbalance(symbol)
                ticker_resp = self.client.get_ticker(symbol)
                if ticker_resp.get("code") != "0" or not ticker_resp.get("data"):
                    continue

                last_price = float(ticker_resp["data"][0].get("last", 0))
                logging.info(f"[{symbol}] Price: {last_price} | Order Book Imbalance: {obi:+.2%}")

                # Signal generation: High positive imbalance indicates aggressive buyer absorption
                if obi >= self.imbalance_threshold and symbol not in self.active_positions:
                    trade_size_usdc = min(equity * self.risk_per_trade_pct, equity * 0.95)
                    if trade_size_usdc >= 10.0:  # Minimum viable order
                        qty = trade_size_usdc / last_price
                        logging.info(f"🚀 BUY SIGNAL on {symbol}! OBI={obi:+.2%}. Allocating {trade_size_usdc:.2f} USDC (~{qty:.4f} units)")
                        
                        # Execute market buy order
                        order_res = self.client.place_order(
                            inst_id=symbol,
                            td_mode="cash",
                            side="buy",
                            ord_type="market",
                            sz=f"{trade_size_usdc:.2f}"
                        )
                        logging.info(f"Order Response: {order_res}")

                        self.active_positions[symbol] = {
                            "entry_price": last_price,
                            "high_price": last_price,
                            "allocated_usdc": trade_size_usdc,
                            "qty": qty,
                            "time": time.time(),
                            "order_res": order_res
                        }
                        self.save_state()

                # Manage active positions (Trailing Stop & Take Profit)
                if symbol in self.active_positions:
                    pos = self.active_positions[symbol]
                    entry_px = pos["entry_price"]
                    pnl_pct = (last_price - entry_px) / entry_px
                    
                    if last_price > pos.get("high_price", entry_px):
                        pos["high_price"] = last_price

                    high_px = pos.get("high_price", entry_px)
                    trailing_drop = (high_px - last_price) / high_px if high_px > 0 else 0

                    # Check TP
                    if pnl_pct >= self.take_profit_pct:
                        logging.info(f"🎯 TAKE PROFIT TRIGGERED on {symbol}! Gain: +{pnl_pct*100:.2f}%. Closing position.")
                        self.close_position(symbol, last_price=last_price, reason="TAKE_PROFIT")
                    # Check SL
                    elif pnl_pct <= -self.stop_loss_pct:
                        logging.warning(f"🛑 STOP LOSS TRIGGERED on {symbol}! Loss: {pnl_pct*100:.2f}%. Cut loss cleanly.")
                        self.close_position(symbol, last_price=last_price, reason="STOP_LOSS")
                    # Check Trailing Stop Lock
                    elif pnl_pct > 0.015 and trailing_drop >= self.trailing_stop_pct:
                        logging.info(f"🔒 TRAILING PROFIT LOCK TRIGGERED on {symbol}! Secured +{pnl_pct*100:.2f}%.")
                        self.close_position(symbol, last_price=last_price, reason="TRAILING_STOP")

            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}")

    def close_position(self, symbol: str, last_price: Optional[float] = None, reason: str = "MANUAL_CLOSE"):
        if symbol not in self.active_positions:
            logging.warning(f"Cannot close position for {symbol}: Not in active positions.")
            return

        pos = self.active_positions[symbol]
        base_ccy = symbol.split("-")[0]

        if not last_price:
            ticker_resp = self.client.get_ticker(symbol)
            if ticker_resp.get("code") == "0" and ticker_resp.get("data"):
                last_price = float(ticker_resp["data"][0].get("last", pos["entry_price"]))
            else:
                last_price = pos["entry_price"]

        logging.info(f"Closing position for {symbol} at market price {last_price} (Reason: {reason})...")

        # Query available base currency to sell
        avail_qty = 0.0
        bal_res = self.client.get_balance(base_ccy)
        if bal_res.get("code") == "0" and bal_res.get("data"):
            details = bal_res["data"][0].get("details", [])
            for d in details:
                if d.get("ccy") == base_ccy:
                    avail_qty = float(d.get("availBal", 0.0))

        sell_qty = avail_qty if avail_qty > 0 else float(pos.get("qty", 0.0))
        
        # Format precision according to instrument standards
        sz_str = f"{sell_qty:.4f}" if sell_qty < 100 else f"{sell_qty:.2f}"
        if sell_qty > 0:
            order_res = self.client.place_order(
                inst_id=symbol,
                td_mode="cash",
                side="sell",
                ord_type="market",
                sz=sz_str
            )
            logging.info(f"OKX Market Sell Order Response: {order_res}")
        else:
            logging.warning(f"No available balance found to sell for {symbol}. Recording paper exit.")

        # Compute Realized PnL
        allocated_usdc = float(pos.get("allocated_usdc", sell_qty * pos["entry_price"]))
        realized_usdc = sell_qty * last_price if sell_qty > 0 else allocated_usdc * (last_price / pos["entry_price"])
        realized_pnl = realized_usdc - allocated_usdc
        pnl_pct = ((last_price - pos["entry_price"]) / pos["entry_price"]) * 100

        closed_trade = {
            "id": f"TRD-LENA-{int(time.time()*1000)}",
            "bot": "Lena (Aggressive Scalper)",
            "symbol": symbol,
            "side": "SELL",
            "entry_price": pos["entry_price"],
            "exit_price": last_price,
            "size_units": round(sell_qty, 4),
            "notional_usd": round(realized_usdc, 2),
            "realized_pnl": round(realized_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "exit_reason": reason,
            "entry_time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(pos.get("time", time.time()))),
            "exit_time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            "status": "CLOSED"
        }

        self.trades_history.insert(0, closed_trade)
        del self.active_positions[symbol]
        self.save_state()
        logging.info(f"✅ Closed trade recorded: PnL=${realized_pnl:+.2f} ({pnl_pct:+.2f}%)")

    def sync_exchange_history(self):
        """Fetch real historical fills from OKX and merge into trades_history.json"""
        logging.info("Querying OKX for recent order history and fills...")
        history_resp = self.client.get_order_history(inst_type="SPOT", limit=30)
        if history_resp.get("code") == "0" and history_resp.get("data"):
            logging.info(f"Fetched {len(history_resp['data'])} historical orders from OKX.")
            for o in history_resp["data"]:
                fill_px = float(o.get("fillPx", o.get("avgPx", 0)))
                sz = float(o.get("accFillSz", o.get("sz", 0)))
                if fill_px > 0 and sz > 0:
                    trade_record = {
                        "id": f"OKX-{o.get('ordId', int(time.time()))}",
                        "bot": "Lena (Aggressive Scalper)",
                        "symbol": o.get("instId"),
                        "side": o.get("side", "").upper(),
                        "entry_price": fill_px,
                        "exit_price": fill_px,
                        "size_units": sz,
                        "notional_usd": round(fill_px * sz, 2),
                        "realized_pnl": 0.0,
                        "pnl_pct": 0.0,
                        "exit_reason": "EXCHANGE_FILL",
                        "entry_time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(o.get("cTime", 0))/1000)),
                        "exit_time": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(int(o.get("uTime", 0))/1000)),
                        "status": "CLOSED"
                    }
                    if not any(t.get("id") == trade_record["id"] for t in self.trades_history):
                        self.trades_history.append(trade_record)
            self.save_state()

    def run(self, max_cycles=None, sync_history=False):
        if sync_history:
            self.sync_exchange_history()

        logging.info(f"Starting Aggressive Lena Execution Loop (cycles={max_cycles or 'continuous'})...")
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
    parser = argparse.ArgumentParser(description="Lena Aggressive Scalper")
    parser.add_argument("--cycles", type=int, default=None, help="Number of scan cycles to run before exiting")
    parser.add_argument("--sync-history", action="store_true", help="Sync exchange fills and order history on startup")
    args = parser.parse_args()

    bot = LenaAggressiveBot()
    bot.run(max_cycles=args.cycles, sync_history=args.sync_history)
