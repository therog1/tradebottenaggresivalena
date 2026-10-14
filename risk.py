"""Deterministiska riskfunktioner för Lena (rena funktioner, testbara utan OKX)."""
import math
import time
from typing import Any, Dict, List, Optional, Tuple


def validate_book(book: Dict[str, Any], max_age_s: float = 2.0, max_spread_pct: float = 0.0015,
                  now_ms: Optional[float] = None) -> Tuple[bool, str, Optional[Dict[str, float]]]:
    """Avvisa orderbok som är för gammal, tom, korsad eller har för bred spread.
    Returnerar (ok, skäl, {bid, ask, mid, spread_pct, age_s})."""
    if book.get("code") != "0" or not book.get("data"):
        return False, "BOOK_API_ERROR", None
    d = book["data"][0]
    bids, asks = d.get("bids") or [], d.get("asks") or []
    if not bids or not asks:
        return False, "BOOK_EMPTY", None
    try:
        ts = float(d.get("ts"))
    except (TypeError, ValueError):
        return False, "BOOK_NO_TS", None
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    age_s = (now_ms - ts) / 1000.0
    if age_s > max_age_s or age_s < -max_age_s:
        return False, f"BOOK_STALE({age_s:.2f}s)", None
    bid, ask = float(bids[0][0]), float(asks[0][0])
    if bid <= 0 or ask <= bid:
        return False, "BOOK_CROSSED", None
    mid = (bid + ask) / 2
    spread = (ask - bid) / mid
    if spread > max_spread_pct:
        return False, f"SPREAD_TOO_WIDE({spread:.4%})", None
    return True, "OK", {"bid": bid, "ask": ask, "mid": mid, "spread_pct": spread, "age_s": age_s}


def orderbook_imbalance(book: Dict[str, Any], levels: int = 10) -> float:
    d = book["data"][0]
    bv = sum(float(b[1]) for b in d.get("bids", [])[:levels])
    av = sum(float(a[1]) for a in d.get("asks", [])[:levels])
    tot = bv + av
    return 0.0 if tot == 0 else (bv - av) / tot


def leverage_for_obi(obi: float, min_lev: int = 3, max_lev: int = 5) -> int:
    """Instruktion: |OBI| > 0.60 -> 5x; 0.45–0.60 -> 3x (konservativ ände av 3–4x)."""
    return max_lev if abs(obi) > 0.60 else min_lev


def sl_tp_prices(entry: float, side: str, sl_pct: float, tp_pct: float) -> Tuple[float, float]:
    """SL/TP på PRIS (inte hävstångs-PnL)."""
    if side == "buy":
        return entry * (1 - sl_pct), entry * (1 + tp_pct)
    return entry * (1 + sl_pct), entry * (1 - tp_pct)


def size_contracts(equity: float, entry: float, sl_price: float, ct_val: float, min_sz: float, lot_sz: float,
                   leverage: int, risk_per_trade: float, max_margin_frac: float,
                   open_risk_usd: float = 0.0, max_total_risk: float = 0.03) -> Dict[str, Any]:
    """Storlek från verifierat saldo och verkligt SL-avstånd.
    Avrundar NEDÅT; minSz tvingar aldrig upp storleken över risken."""
    if equity <= 0 or entry <= 0 or ct_val <= 0 or lot_sz <= 0:
        return {"ok": False, "reason": "INVALID_INPUT"}
    loss_per_ct = abs(entry - sl_price) * ct_val
    if loss_per_ct <= 0:
        return {"ok": False, "reason": "ZERO_SL_DISTANCE"}
    risk_budget = min(equity * risk_per_trade, equity * max_total_risk - open_risk_usd)
    if risk_budget <= 0:
        return {"ok": False, "reason": "TOTAL_RISK_CAP"}
    by_risk = risk_budget / loss_per_ct
    by_margin = (equity * max_margin_frac * leverage) / (entry * ct_val)
    raw = min(by_risk, by_margin)
    sz = math.floor(raw / lot_sz + 1e-9) * lot_sz
    if sz < min_sz:
        return {"ok": False, "reason": f"BELOW_MIN_SIZE(raw={raw:.4f},min={min_sz})"}
    return {"ok": True, "contracts": round(sz, 8), "risk_usd": sz * loss_per_ct,
            "notional_usd": sz * ct_val * entry, "margin_usd": sz * ct_val * entry / leverage,
            "margin_limited": by_margin < by_risk}


def choose_leverage(equity: float, entry: float, sl_price: float, ct_val: float, min_sz: float, lot_sz: float,
                    min_lev: int, max_lev: int, risk_per_trade: float, max_margin_frac: float,
                    open_risk_usd: float = 0.0, max_total_risk: float = 0.03) -> Tuple[int, Dict[str, Any]]:
    """Lägsta hävstång i [min_lev..max_lev] där marginaltaket inte längre krymper
    den riskbaserade storleken. Risken per affär ändras inte av hävstången."""
    last: Dict[str, Any] = {"ok": False, "reason": "BAD_LEVERAGE_RANGE"}
    for lev in range(int(min_lev), int(max_lev) + 1):
        last = size_contracts(equity, entry, sl_price, ct_val, min_sz, lot_sz, lev, risk_per_trade,
                              max_margin_frac, open_risk_usd, max_total_risk)
        if last.get("ok") and not last.get("margin_limited"):
            return lev, last
        if not last.get("ok") and not str(last.get("reason", "")).startswith("BELOW_MIN_SIZE"):
            return lev, last  # risk/indata-fel: mer hävstång hjälper inte
    return int(max_lev), last


def trailing_hit(side: str, entry: float, extreme: float, last: float, trail_pct: float,
                 activate_pct: float) -> bool:
    """Trailing på pris. Long: högsta pris; short: lägsta pris."""
    if side in ("buy", "BUY"):
        if (extreme - entry) / entry < activate_pct:
            return False
        return (extreme - last) / extreme >= trail_pct
    if (entry - extreme) / entry < activate_pct:
        return False
    return (last - extreme) / extreme >= trail_pct


def reconcile(local: Dict[str, Dict[str, Any]], exch_positions: List[Dict[str, Any]],
              algo_orders: List[Dict[str, Any]], managed: List[str]) -> Dict[str, Any]:
    """Jämför lokal state med OKX. Varje avvikelse => blockera nya riskökande affärer.
    Stänger aldrig något själv."""
    issues: List[str] = []
    exch = {p["instId"]: p for p in exch_positions if float(p.get("pos") or 0) != 0}
    for inst in local:
        if inst not in exch:
            issues.append(f"LOCAL_NOT_ON_EXCHANGE:{inst}")
    for inst, p in exch.items():
        if inst not in local:
            issues.append(f"EXCHANGE_NOT_IN_LOCAL:{inst}" + ("" if inst in managed else "(unmanaged)"))
        else:
            lsz = float(local[inst].get("num_contracts", 0))
            if abs(abs(float(p["pos"])) - lsz) > 1e-9:
                issues.append(f"SIZE_MISMATCH:{inst}(local={lsz},okx={p['pos']})")
        has_sl = any(o.get("instId") == inst and o.get("slTriggerPx") for o in algo_orders)
        if not has_sl:
            issues.append(f"NO_EXCHANGE_SL:{inst}")
    return {"ok": not issues, "issues": issues, "exchange": exch}
