import pytest
from risk import validate_book, orderbook_imbalance, leverage_for_obi, sl_tp_prices, size_contracts, trailing_hit, reconcile

NOW = 1_000_000_000_000.0

def book(bid=100.0, ask=100.05, ts=NOW, bids=None, asks=None, code="0"):
    return {"code": code, "data": [{"ts": str(ts), "bids": bids or [[str(bid), "10"]], "asks": asks or [[str(ask), "10"]]}]}

def test_book_fresh_ok():
    ok, why, q = validate_book(book(), now_ms=NOW + 500)
    assert ok and q["spread_pct"] < 0.0015

def test_book_stale_rejected():
    ok, why, _ = validate_book(book(), now_ms=NOW + 2500)
    assert not ok and why.startswith("BOOK_STALE")

def test_book_wide_spread_rejected():
    ok, why, _ = validate_book(book(bid=100, ask=100.2), now_ms=NOW)
    assert not ok and why.startswith("SPREAD_TOO_WIDE")

def test_book_missing_ts_and_api_error():
    b = book(); del b["data"][0]["ts"]
    assert validate_book(b, now_ms=NOW)[1] == "BOOK_NO_TS"
    assert validate_book(book(code="50011"), now_ms=NOW)[1] == "BOOK_API_ERROR"

def test_book_crossed():
    assert validate_book(book(bid=100, ask=99), now_ms=NOW)[1] == "BOOK_CROSSED"

def test_obi():
    assert orderbook_imbalance(book(bids=[["1", "30"]], asks=[["2", "10"]])) == pytest.approx(0.5)

def test_leverage_ladder():
    assert leverage_for_obi(0.5) == 3
    assert leverage_for_obi(0.60) == 3
    assert leverage_for_obi(0.61) == 5
    assert leverage_for_obi(-0.8) == 5

def test_sl_tp_on_price_long_short():
    assert sl_tp_prices(100, "buy", 0.018, 0.04) == pytest.approx((98.2, 104.0))
    assert sl_tp_prices(100, "sell", 0.018, 0.04) == pytest.approx((101.8, 96.0))

def test_size_from_sl_distance():
    # equity 50, risk 1% = 0.5 USD; SL-avstånd 1.8 USD * ctVal 0.01 = 0.018 USD/kontrakt -> 27 kontrakt
    r = size_contracts(50, 100, 98.2, 0.01, 1, 1, 3, 0.01, 0.35)
    assert r["ok"] and r["contracts"] == 27
    assert r["risk_usd"] <= 0.5 + 1e-9

def test_size_capped_by_margin():
    r = size_contracts(50, 100, 99.99, 0.01, 1, 1, 3, 0.01, 0.35)
    assert r["ok"] and r["margin_usd"] <= 50 * 0.35 + 1e-9

def test_min_size_never_forces_up():
    # BTC-kontrakt med 50 USD: minSz får inte trycka upp risken
    r = size_contracts(50, 65000, 63830, 0.01, 1, 1, 5, 0.01, 0.35)
    assert not r["ok"] and r["reason"].startswith("BELOW_MIN_SIZE")

def test_total_risk_cap():
    r = size_contracts(50, 100, 98.2, 0.01, 1, 1, 3, 0.01, 0.35, open_risk_usd=1.5, max_total_risk=0.03)
    assert not r["ok"] and r["reason"] == "TOTAL_RISK_CAP"

def test_trailing_long():
    assert not trailing_hit("BUY", 100, 100.2, 100.1, 0.009, 0.004)  # ej aktiverad
    assert trailing_hit("BUY", 100, 101, 100.0, 0.009, 0.004)

def test_trailing_short_uses_low():
    assert trailing_hit("SELL", 100, 99, 100.0, 0.009, 0.004)
    assert not trailing_hit("SELL", 100, 99, 99.5, 0.009, 0.004)
    # gamla buggen: short som går emot får inte tolkas som trailing-vinst
    assert not trailing_hit("SELL", 100, 100, 101, 0.009, 0.004)

def test_reconcile_detects_orphans_and_missing_sl():
    local = {"PEPE-USDC": {"num_contracts": 1}, "XRP-X": {"num_contracts": 2}}
    exch = [{"instId": "XRP-X", "pos": "2"}, {"instId": "ETH-X", "pos": "1"}]
    r = reconcile(local, exch, [{"instId": "XRP-X", "slTriggerPx": "1"}], ["XRP-X"])
    assert not r["ok"]
    assert "LOCAL_NOT_ON_EXCHANGE:PEPE-USDC" in r["issues"]
    assert any(i.startswith("EXCHANGE_NOT_IN_LOCAL:ETH-X") for i in r["issues"])
    assert "NO_EXCHANGE_SL:ETH-X" in r["issues"]

def test_reconcile_clean():
    r = reconcile({"XRP-X": {"num_contracts": 2}}, [{"instId": "XRP-X", "pos": "-2"}],
                  [{"instId": "XRP-X", "slTriggerPx": "1"}], ["XRP-X"])
    assert r["ok"]
