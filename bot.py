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
from risk import (validate_book, orderbook_imbalance, leverage_for_obi, choose_leverage, sl_tp_prices,
                  size_contracts, trailing_hit, reconcile, closes_from_candles, trend_allows, obi_confirmed)
import crossbot

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
        # Signalkvalitet: OBI måste hålla i flera mätningar, och riktningen måste följa trenden.
        self.obi_confirm_samples = int(os.getenv("OBI_CONFIRM_SAMPLES", "3"))
        self.obi_confirm_interval = float(os.getenv("OBI_CONFIRM_INTERVAL_SECONDS", "3"))
        self.trend_filter = os.getenv("TREND_FILTER_ENABLED", "true").strip().lower() != "false"
        self.trend_bar = os.getenv("TREND_BAR", "1H")
        self.trend_ema = int(os.getenv("TREND_EMA_PERIOD", "50"))
        self.peer_exposure = []
        # Förlustrisk per affär (andel av verifierat saldo) vid SL-träff; 35 % behålls endast som TAK för marginal per affär.
        self.risk_per_trade = float(os.getenv("RISK_PER_TRADE_FRACTION", "0.01"))
        self.max_margin_frac = float(os.getenv("MAX_MARGIN_FRACTION_PER_TRADE", os.getenv("RISK_PER_TRADE_PERCENT", "0.35")))
        self.max_total_risk = float(os.getenv("MAX_TOTAL_RISK_FRACTION", "0.03"))
        self.max_book_age_s = float(os.getenv("MAX_BOOK_AGE_SECONDS", "2.0"))
        self.max_spread_pct = float(os.getenv("MAX_SPREAD_FRACTION", "0.0015"))
        self.trailing_activate_pct = float(os.getenv("TRAILING_ACTIVATE_FRACTION", "0.004"))
        self.fill_poll_tries = int(os.getenv("FILL_POLL_TRIES", "5"))
        self.fill_poll_interval = float(os.getenv("FILL_POLL_INTERVAL", "0.5"))
        self.kill_switch_file = os.getenv("KILL_SWITCH_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "KILL_SWITCH"))
        self.block_reasons: List[str] = []
        self.exchange_positions: Dict[str, Any] = {}
        self.exchange_sl: Dict[str, float] = {}
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

    # ------------------------------------------------------------------ saldo
    def get_equity(self) -> Optional[float]:
        """Verifierat USDC-saldo. None vid API-fel (=> inga nya affärer). Aldrig max(avail, eq)."""
        res = self.client.get_balance("USDC")
        if res.get("code") != "0" or not res.get("data"):
            logging.error(f"Balance API error: {res.get('code')} {res.get('msg')}")
            return None
        for d in res["data"][0].get("details", []):
            if d.get("ccy") == "USDC":
                vals = [float(d[k]) for k in ("availEq", "availBal", "eq") if d.get(k) not in (None, "")]
                return min(vals) if vals else None
        return None

    def _sleep(self, s: float):
        time.sleep(s)

    def compute_orderbook_imbalance(self, inst_id: str) -> float:
        """Bakåtkompatibel; används inte för beslut (se try_entry)."""
        return orderbook_imbalance(self.client.get_orderbook(inst_id, sz=15))

    # --------------------------------------------------------- reconciliation
    def preflight(self) -> bool:
        """Stäm av lokal state mot OKX. Blockerar nya riskökande affärer vid avvikelse eller API-fel.
        Stänger ALDRIG något automatiskt; avvikande positioner rapporteras bara."""
        self.block_reasons = []
        pos = self.client.get_positions(inst_type="FUTURES")
        algo = self.client.get_algo_orders_pending()
        if pos.get("code") != "0" or algo.get("code") != "0":
            self.block_reasons.append(f"RECONCILE_API_ERROR(pos={pos.get('code')},algo={algo.get('code')})")
            return False
        exch_live = {p["instId"] for p in pos.get("data", []) if float(p.get("pos") or 0) != 0}
        for inst in list(self.active_positions):
            lp = self.active_positions[inst]
            if lp.get("sl_attached") and inst not in exch_live:
                logging.info(f"{inst} finns inte längre hos OKX – stängd av exchange-SL/TP. Tas bort ur lokal state.")
                self._record_close(inst, lp, None, "EXCHANGE_SL_TP_OR_EXTERNAL")
        for o in algo.get("data", []):
            try:
                if o.get("slTriggerPx"):
                    self.exchange_sl[o["instId"]] = float(o["slTriggerPx"])
            except (TypeError, ValueError, KeyError):
                pass
        rec = reconcile(self.active_positions, pos.get("data", []), algo.get("data", []), list(CONTRACT_SPECS))
        self.exchange_positions = rec["exchange"]
        if not rec["ok"]:
            self.block_reasons.extend(rec["issues"])
            for i in rec["issues"]:
                logging.critical(f"RECONCILIATION: {i} – nya affärer blockerade, ingenting stängs automatiskt.")
        return rec["ok"]

    @property
    def risk_blocked(self) -> bool:
        return bool(self.block_reasons)

    # ------------------------------------------------------------------ entry
    def _position_risk_usd(self, inst: str, p: Dict[str, Any]) -> float:
        """Risk för en öppen position. Äldre poster saknar risk_usd -> räkna
        abs(entry - sl) * ctVal * contracts. SL tas från posten, annars från OKX
        algo-order, annars konservativt från STOP_LOSS_PERCENT. Aldrig 0 om entry finns."""
        if p.get("risk_usd") is not None:
            return float(p["risk_usd"])
        entry = float(p.get("entry_price") or 0)
        contracts = abs(float(p.get("num_contracts") or 0))
        ct_val = float(CONTRACT_SPECS.get(inst, {}).get("ctVal", 1.0))
        sl = p.get("sl_price") or self.exchange_sl.get(inst)
        if sl:
            dist = abs(entry - float(sl))
        else:
            dist = entry * self.stop_loss_pct
        return dist * ct_val * contracts

    def _open_risk_usd(self) -> float:
        return sum(self._position_risk_usd(i, p) for i, p in self.active_positions.items())

    def try_entry(self, symbol: str, equity: float):
        if self.risk_blocked or symbol in self.active_positions:
            return
        if len(self.active_positions) >= self.max_open_positions:
            return
        book = self.client.get_orderbook(symbol, sz=15)
        ok, why, q = validate_book(book, self.max_book_age_s, self.max_spread_pct)
        if not ok:
            logging.info(f"[{symbol}] Ingen entry: {why}")
            return
        obi = orderbook_imbalance(book)
        logging.info(f"[{symbol}] OBI={obi:+.2%} spread={q['spread_pct']:.4%} age={q['age_s']:.2f}s")
        if abs(obi) < self.imbalance_threshold:
            return
        side = "buy" if obi > 0 else "sell"
        # 1) Gemensamt exponeringstak med Anna
        own = [(crossbot.coin_of(inst), p.get("side") == "BUY") for inst, p in self.active_positions.items()]
        cb_ok, cb_why = crossbot.check(crossbot.coin_of(symbol), side == "buy", own, self.peer_exposure)
        if not cb_ok:
            logging.info(f"[{symbol}] Ingen entry: {cb_why}")
            return
        # 2) Trendfilter: long bara över EMA, short bara under
        if self.trend_filter:
            closes = closes_from_candles(self.client.get_candles(symbol, bar=self.trend_bar, limit=100))
            t_ok, t_why = trend_allows(side, closes, self.trend_ema)
            if not t_ok:
                logging.info(f"[{symbol}] Ingen entry: {t_why}")
                return
        # 3) OBI-bekräftelse: obalansen måste hålla i flera mätningar
        samples = [obi]
        for _ in range(max(0, self.obi_confirm_samples - 1)):
            self._sleep(self.obi_confirm_interval)
            book = self.client.get_orderbook(symbol, sz=15)
            ok, why, q = validate_book(book, self.max_book_age_s, self.max_spread_pct)
            if not ok:
                logging.info(f"[{symbol}] Ingen entry: bekräftelse misslyckades ({why})")
                return
            samples.append(orderbook_imbalance(book))
        if not obi_confirmed(samples, self.imbalance_threshold):
            logging.info(f"[{symbol}] Ingen entry: OBI höll inte ({', '.join(f'{x:+.2f}' for x in samples)})")
            return
        ref = q["ask"] if side == "buy" else q["bid"]
        sl_px, tp_px = sl_tp_prices(ref, side, self.stop_loss_pct, self.take_profit_pct)
        spec = CONTRACT_SPECS.get(symbol)
        if not spec:
            logging.warning(f"[{symbol}] Saknar kontraktsspec – hoppar över.")
            return
        lev, sz = choose_leverage(equity, ref, sl_px, spec["ctVal"], spec["minSz"], spec.get("lotSz", spec["minSz"]),
                                  self.min_leverage, min(self.max_leverage, 10), self.risk_per_trade,
                                  self.max_margin_frac, self._open_risk_usd(), self.max_total_risk)
        if not sz["ok"]:
            logging.info(f"[{symbol}] Ingen entry: {sz['reason']}")
            return
        lev_res = self.client.set_leverage(lever=str(lev), mgn_mode="cross", inst_id=symbol)
        if lev_res.get("code") != "0":
            logging.error(f"[{symbol}] set_leverage misslyckades ({lev_res.get('msg')}) – ingen order.")
            return
        cl_id = f"lena{int(time.time()*1000)}"
        attach = [{"attachAlgoClOrdId": cl_id + "sl",
                   "slTriggerPx": f"{sl_px:.10g}", "slOrdPx": "-1", "slTriggerPxType": "last",
                   "tpTriggerPx": f"{tp_px:.10g}", "tpOrdPx": "-1", "tpTriggerPxType": "last"}]
        logging.info(f"[{symbol}] {side.upper()} {sz['contracts']} ct @~{ref} lev={lev}x SL={sl_px:.6g} TP={tp_px:.6g} risk=${sz['risk_usd']:.2f}")
        res = self.client.place_order(inst_id=symbol, td_mode="cross", side=side, ord_type="market",
                                      sz=f"{sz['contracts']:g}", pos_side="net", cl_ord_id=cl_id, attach_algo_ords=attach)
        if res.get("code") != "0":
            logging.error(f"[{symbol}] Order avvisad: {res}")
            if res.get("code") in ("-1", "429"):
                self.block_reasons.append(f"ORDER_STATE_UNKNOWN:{symbol}:{cl_id}")
            return
        self._verify_fill(symbol, side, cl_id, lev, sl_px, tp_px, sz)

    def _verify_fill(self, symbol, side, cl_id, lev, sl_px, tp_px, sz):
        """En accepterad order är INTE en position. Kräv filled/partially_filled från OKX."""
        o = None
        for _ in range(self.fill_poll_tries):
            r = self.client.get_order(symbol, cl_ord_id=cl_id)
            if r.get("code") == "0" and r.get("data"):
                o = r["data"][0]
                if o.get("state") in ("filled", "canceled", "mmp_canceled"):
                    break
            self._sleep(self.fill_poll_interval)
        filled = float(o.get("accFillSz") or 0) if o else 0.0
        if not o or filled <= 0:
            state = o.get("state") if o else "unknown"
            logging.critical(f"[{symbol}] Fill ej verifierad (state={state}) – blockerar nya affärer.")
            if state not in ("canceled", "mmp_canceled"):
                self.block_reasons.append(f"FILL_UNVERIFIED:{symbol}:{cl_id}")
            return
        if o.get("state") != "filled":
            self.block_reasons.append(f"PARTIAL_FILL:{symbol}:{filled}/{sz['contracts']}")
        avg = float(o.get("avgPx") or 0)
        ct = CONTRACT_SPECS[symbol]["ctVal"]
        self.active_positions[symbol] = {
            "entry_price": avg, "side": side.upper(), "leverage": lev, "num_contracts": filled,
            "sl_price": sl_px, "tp_price": tp_px, "risk_usd": abs(avg - sl_px) * ct * filled,
            "high_price": avg, "low_price": avg, "time": time.time(), "ord_id": o.get("ordId"),
            "cl_ord_id": cl_id, "sl_attached": False,
        }
        self._ensure_exchange_sl(symbol)
        self.save_state()

    def _ensure_exchange_sl(self, symbol):
        """Verifiera att SL finns hos OKX; annars lägg fristående reduce-only SL/TP. Misslyckas det: blockera + larm."""
        pos = self.active_positions[symbol]
        for _ in range(self.fill_poll_tries):
            algo = self.client.get_algo_orders_pending()
            if algo.get("code") == "0" and any(a.get("instId") == symbol and a.get("slTriggerPx") for a in algo.get("data", [])):
                pos["sl_attached"] = True
                logging.info(f"[{symbol}] Exchange-SL verifierad hos OKX.")
                return
            self._sleep(self.fill_poll_interval)
        close_side = "sell" if pos["side"] == "BUY" else "buy"
        r = self.client.place_algo_order(symbol, "cross", close_side, f"{pos['num_contracts']:g}",
                                         sl_trigger_px=f"{pos['sl_price']:.10g}", tp_trigger_px=f"{pos['tp_price']:.10g}")
        if r.get("code") == "0":
            pos["sl_attached"] = True
            logging.warning(f"[{symbol}] attachAlgoOrds saknades – fristående SL/TP lagd.")
        else:
            logging.critical(f"[{symbol}] KUNDE INTE LÄGGA SL HOS OKX: {r} – blockerar, manuell åtgärd krävs.")
            self.block_reasons.append(f"NO_EXCHANGE_SL:{symbol}")

    # ------------------------------------------------------------------- exit
    def manage_position(self, symbol: str):
        """SL/TP ligger hos OKX. Här hanteras bara trailing (på pris, korrekt för short)."""
        pos = self.active_positions.get(symbol)
        if not pos or not pos.get("sl_attached") or self.risk_blocked:
            return
        t = self.client.get_ticker(symbol)
        if t.get("code") != "0" or not t.get("data"):
            return
        last = float(t["data"][0].get("last", 0))
        if last <= 0:
            return
        pos["high_price"] = max(pos.get("high_price", last), last)
        pos["low_price"] = min(pos.get("low_price", last), last)
        extreme = pos["high_price"] if pos["side"] == "BUY" else pos["low_price"]
        if trailing_hit(pos["side"], pos["entry_price"], extreme, last, self.trailing_stop_pct, self.trailing_activate_pct):
            logging.info(f"[{symbol}] Trailing stop (pris) – stänger reduce-only.")
            self.close_position(symbol, last, "TRAILING_STOP")

    def scan_and_execute(self):
        equity = self.get_equity()
        if equity is None:
            self.block_reasons.append("BALANCE_UNVERIFIED")
            logging.critical("Saldo kunde inte verifieras – inga nya affärer.")
        elif equity < self.kill_switch_min_equity:
            self.block_reasons.append("KILL_SWITCH_EQUITY")
            logging.critical(f"Equity ${equity:.2f} under kill switch ${self.kill_switch_min_equity:.2f}.")
        if self.kill_switch_file and os.path.exists(self.kill_switch_file):
            self.block_reasons.append("KILL_SWITCH_FILE")
        for symbol in list(self.active_positions):
            if symbol in CONTRACT_SPECS:
                try:
                    self.manage_position(symbol)
                except Exception as e:
                    logging.error(f"manage {symbol}: {e}")
        if self.risk_blocked:
            logging.warning(f"Nya affärer blockerade: {self.block_reasons}")
            return
        peer = crossbot.load_peer("anna")
        self.peer_exposure = peer
        for symbol in self.symbols:
            try:
                self.try_entry(symbol, equity)
            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}")
                self.block_reasons.append(f"EXCEPTION:{symbol}")
                return

    def close_position(self, symbol: str, last_price: Optional[float] = None, reason: str = "MANUAL_CLOSE"):
        """Stänger endast positioner som denna kod själv öppnat (reduce-only)."""
        pos = self.active_positions.get(symbol)
        if not pos or not pos.get("cl_ord_id"):
            logging.warning(f"Vägrar stänga {symbol}: inte öppnad av denna kod.")
            return
        close_side = "sell" if pos["side"] == "BUY" else "buy"
        cl_id = f"lenax{int(time.time()*1000)}"
        res = self.client.place_order(inst_id=symbol, td_mode="cross", side=close_side, ord_type="market",
                                      sz=f"{pos['num_contracts']:g}", pos_side="net", cl_ord_id=cl_id, reduce_only=True)
        if res.get("code") != "0":
            logging.error(f"Close {symbol} avvisad: {res}")
            self.block_reasons.append(f"CLOSE_FAILED:{symbol}")
            return
        r = self.client.get_order(symbol, cl_ord_id=cl_id)
        o = (r.get("data") or [{}])[0] if r.get("code") == "0" else {}
        if o.get("state") != "filled":
            self.block_reasons.append(f"CLOSE_UNVERIFIED:{symbol}")
            return
        self._record_close(symbol, pos, float(o.get("avgPx") or last_price or 0), reason)

    def _record_close(self, symbol, pos, exit_px, reason):
        entry = pos.get("entry_price", 0)
        pnl = None
        if exit_px and entry:
            move = (exit_px - entry) if pos.get("side") == "BUY" else (entry - exit_px)
            pnl = round(move * CONTRACT_SPECS.get(symbol, {"ctVal": 0})["ctVal"] * pos.get("num_contracts", 0), 4)
        self.trades_history.insert(0, {
            "id": f"TRD-LENA-{int(time.time()*1000)}", "bot": "Aggressiva Lena", "symbol": symbol,
            "side": pos.get("side"), "leverage": pos.get("leverage"), "entry_price": entry, "exit_price": exit_px,
            "size_units": pos.get("num_contracts"), "gross_pnl_est_excl_fees_funding": pnl,
            "exit_reason": reason, "status": "CLOSED",
            "exit_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        del self.active_positions[symbol]
        self.save_state()

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
        if not self.preflight():
            logging.critical(f"Preflight-reconciliation misslyckades: {self.block_reasons}. Inga nya affärer.")
        logging.info(f"Starting Aggressive Lena Futures Execution Loop (cycles={max_cycles or 'continuous'})...")
        cycle = 0
        while True:
            cycle += 1
            try:
                logging.info(f"--- SCALP SCAN CYCLE #{cycle} ---")
                if cycle > 1 and not self.risk_blocked:
                    self.preflight()
                self.scan_and_execute()
            except Exception as err:
                logging.error(f"Execution error in cycle {cycle}: {err}")
                self.block_reasons.append("CYCLE_EXCEPTION")
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
