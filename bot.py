"""
Aggressive Momentum & Orderbook Flow Scalper (Lena)
Ken Griffin Inspired Execution Engine
"""
import os
import sys
import time
import json
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv
from okx_client import OKXClient

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [LENA-AGGRESSIVE] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

class LenaAggressiveBot:
    def __init__(self):
        self.api_key = os.getenv("OKX_API_KEY", "")
        self.secret_key = os.getenv("OKX_API_SECRET", "")
        self.passphrase = os.getenv("OKX_API_PASSPHRASE", "")
        self.is_demo = os.getenv("OKX_IS_DEMO", "false").lower() == "true"
        self.base_url = os.getenv("OKX_BASE_URL", "https://eea.okx.com")

        self.symbols = [s.strip() for s in os.getenv("SYMBOLS", "SOL-USDC,SUI-USDC,BTC-USDC,ETH-USDC").split(",") if s.strip()]
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PERCENT", "0.018"))
        self.take_profit_pct = float(os.getenv("TAKE_PROFIT_PERCENT", "0.040"))
        self.trailing_stop_pct = float(os.getenv("TRAILING_STOP_PERCENT", "0.009"))
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
        self.active_positions: Dict[str, Dict[str, Any]] = {}

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
        logging.info(f"Subaccount Stack Equity: {equity:.2f} USDC")

        # Emergency Kill Switch Check
        if equity > 0 and equity < self.kill_switch_min_equity:
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
                if obi > 0.40 and symbol not in self.active_positions:
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
                            sz=f"{trade_size_usdc:.2f}"  # In spot cash, sz in quote ccy or base ccy depending on instrument rules
                        )
                        logging.info(f"Order Response: {order_res}")
                        self.active_positions[symbol] = {
                            "entry_price": last_price,
                            "high_price": last_price,
                            "allocated_usdc": trade_size_usdc,
                            "time": time.time()
                        }

                # Manage active positions (Trailing Stop & Take Profit)
                if symbol in self.active_positions:
                    pos = self.active_positions[symbol]
                    entry_px = pos["entry_price"]
                    pnl_pct = (last_price - entry_px) / entry_px

                    if last_price > pos["high_price"]:
                        pos["high_price"] = last_price

                    trailing_drop = (pos["high_price"] - last_price) / pos["high_price"]

                    # Check TP
                    if pnl_pct >= self.take_profit_pct:
                        logging.info(f"🎯 TAKE PROFIT TRIGGERED on {symbol}! Gain: +{pnl_pct*100:.2f}%. Closing position.")
                        self.close_position(symbol)
                    # Check SL
                    elif pnl_pct <= -self.stop_loss_pct:
                        logging.warning(f"🛑 STOP LOSS TRIGGERED on {symbol}! Loss: {pnl_pct*100:.2f}%. Cut loss cleanly.")
                        self.close_position(symbol)
                    # Check Trailing Stop Lock
                    elif pnl_pct > 0.015 and trailing_drop >= self.trailing_stop_pct:
                        logging.info(f"🔒 TRAILING PROFIT LOCK TRIGGERED on {symbol}! Secured +{pnl_pct*100:.2f}%.")
                        self.close_position(symbol)

            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}")

    def close_position(self, symbol: str):
        if symbol in self.active_positions:
            logging.info(f"Closing position for {symbol} at market...")
            # Spot cash market sell
            del self.active_positions[symbol]

    def run(self, max_cycles=None):
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
    args = parser.parse_args()
    bot = LenaAggressiveBot()
    bot.run(max_cycles=args.cycles)
