import json, time
import pytest
import bot as botmod

SYM = "XRP-USD_UM_XPERP-310404"

class FakeOKX:
    def __init__(self, positions=None, algos=None, order_state="filled", fill_sz=None, balance_code="0",
                 attach_ok=True, algo_place_ok=True, order_code="0"):
        self.positions = positions or []
        self.algos = algos or []
        self.order_state, self.fill_sz = order_state, fill_sz
        self.balance_code, self.attach_ok, self.algo_place_ok, self.order_code = balance_code, attach_ok, algo_place_ok, order_code
        self.orders, self.closed, self.algo_placed, self.lev_calls = [], [], [], []
        self.cancels, self.open_orders, self.cancel_works = [], [], True
    def get_open_orders(self, inst_id=None):
        return {"code": "0", "data": self.open_orders}
    def cancel_order(self, inst_id, ord_id=None, cl_ord_id=None):
        self.cancels.append((inst_id, ord_id, cl_ord_id))
        if self.cancel_works and self.order_state in ("live", "partially_filled"):
            self.order_state = "canceled"
        return {"code": "0", "data": [{"sCode": "0"}]}
    def get_balance(self, ccy=None):
        if self.balance_code != "0":
            return {"code": self.balance_code, "data": []}
        return {"code": "0", "data": [{"details": [{"ccy": "USDC", "availEq": "50", "availBal": "60", "eq": "70"}]}]}
    def get_positions(self, inst_type="FUTURES"):
        return {"code": "0", "data": self.positions}
    def get_algo_orders_pending(self, *a, **k):
        return {"code": "0", "data": self.algos}
    def get_orderbook(self, inst_id, sz=15):
        return {"code": "0", "data": [{"ts": str(time.time() * 1000), "bids": [["0.5000", "900"]], "asks": [["0.5001", "100"]]}]}
    def get_ticker(self, inst_id):
        return {"code": "0", "data": [{"last": "0.5"}]}
    def set_leverage(self, **k):
        self.lev_calls.append(k); return {"code": "0"}
    def place_order(self, **k):
        self.orders.append(k)
        if self.order_code != "0":
            return {"code": self.order_code, "msg": "x"}
        if k.get("attach_algo_ords") and self.attach_ok:
            a = k["attach_algo_ords"][0]
            self.algos.append({"instId": k["inst_id"], "slTriggerPx": a["slTriggerPx"]})
        return {"code": "0", "data": [{"ordId": "1", "sCode": "0"}]}
    def get_order(self, inst_id, ord_id=None, cl_ord_id=None):
        sz = self.fill_sz if self.fill_sz is not None else self.orders[-1]["sz"]
        return {"code": "0", "data": [{"ordId": "1", "state": self.order_state, "accFillSz": sz, "avgPx": self.orders[-1].get("px") or "0.5001"}]}
    def place_algo_order(self, *a, **k):
        self.algo_placed.append((a, k)); return {"code": "0" if self.algo_place_ok else "51000"}
    def get_order_history(self, **k):
        return {"code": "0", "data": []}
    trend_up = True
    def get_candles(self, inst_id, bar="1H", limit=100):
        # nyast först; stigande (eller fallande) serie, alla stängda
        n = 80
        closes = [0.40 + 0.002 * i for i in range(n)] if self.trend_up else [0.60 - 0.002 * i for i in range(n)]
        return {"code": "0", "data": [[str(i), "0", "0", "0", f"{c:.4f}", "0", "0", "0", "1"] for i, c in reversed(list(enumerate(closes)))]}

@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.setattr(botmod, "POSITIONS_FILE", str(tmp_path / "pos.json"))
    monkeypatch.setattr(botmod, "HISTORY_FILE", str(tmp_path / "hist.json"))
    monkeypatch.setenv("SYMBOLS", SYM)
    monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "KILL"))
    def make(fake, local=None, short_only=False):
        if local is not None:
            (tmp_path / "pos.json").write_text(json.dumps(local))
        b = botmod.LenaAggressiveBot()
        b.short_only = short_only  # äldre tester använder long-flödet; short-only testas separat
        b.client = fake
        b._sleep = lambda s: None
        return b
    return make

def test_happy_path_entry_attaches_sl_and_verifies_fill(mk):
    f = FakeOKX(); b = mk(f)
    assert b.preflight()
    b.scan_and_execute()
    assert len(f.orders) == 1
    o = f.orders[0]
    assert o["attach_algo_ords"][0]["slTriggerPx"] and o["cl_ord_id"]
    p = b.active_positions[SYM]
    assert p["sl_attached"] and p["entry_price"] == 0.5000
    # SL på pris: 1.8 % under entry-referens
    # post-only entry (köp) ligger på bästa bid 0.5000 -> SL räknas från limitpriset
    assert o["ord_type"] == "post_only" and o["px"] == "0.5000"
    assert float(o["attach_algo_ords"][0]["slTriggerPx"]) == pytest.approx(0.5000 * 0.982, rel=1e-6)
    # risk <= 1 % av verifierat saldo (min av availEq/availBal/eq = 50)
    assert p["risk_usd"] <= 0.5 + 1e-6

def test_accepted_but_unfilled_is_not_position(mk):
    f = FakeOKX(order_state="live", fill_sz="0"); b = mk(f)
    b.entry_ord_type = "market"   # gamla market-flödet: overifierad fyllnad blockerar
    b.preflight(); b.scan_and_execute()
    assert SYM not in b.active_positions
    assert any(r.startswith("FILL_UNVERIFIED") for r in b.block_reasons)

def test_partial_fill_records_filled_qty_and_blocks(mk):
    f = FakeOKX(order_state="partially_filled", fill_sz="3"); b = mk(f)
    b.entry_ord_type = "market"
    b.preflight(); b.scan_and_execute()
    assert b.active_positions[SYM]["num_contracts"] == 3
    assert any(r.startswith("PARTIAL_FILL") for r in b.block_reasons)

def test_missing_attached_sl_places_standalone(mk):
    f = FakeOKX(attach_ok=False); b = mk(f)
    b.preflight(); b.scan_and_execute()
    assert f.algo_placed and b.active_positions[SYM]["sl_attached"]

def test_sl_cannot_be_placed_blocks(mk):
    f = FakeOKX(attach_ok=False, algo_place_ok=False); b = mk(f)
    b.preflight(); b.scan_and_execute()
    assert f"NO_EXCHANGE_SL:{SYM}" in b.block_reasons

def test_balance_error_blocks_new_trades(mk):
    f = FakeOKX(balance_code="50113"); b = mk(f)
    b.preflight(); b.scan_and_execute()
    assert f.orders == [] and "BALANCE_UNVERIFIED" in b.block_reasons

def test_orphan_positions_block_and_are_never_closed(mk):
    local = {"PEPE-USDC": {"entry_price": 4e-6, "qty": 1}, "INJ-USDC": {"entry_price": 7.7}}
    f = FakeOKX(positions=[{"instId": SYM, "pos": "1"}]); b = mk(f, local)
    assert not b.preflight()
    b.scan_and_execute()
    assert f.orders == []                 # varken köp eller stängning
    assert "PEPE-USDC" in b.active_positions and "INJ-USDC" in b.active_positions
    b.close_position("PEPE-USDC")         # vägrar: ej öppnad av ny kod
    assert f.orders == []

def test_kill_switch_file(mk, tmp_path):
    (tmp_path / "KILL").write_text("1")
    f = FakeOKX(); b = mk(f)
    b.preflight(); b.scan_and_execute()
    assert f.orders == [] and "KILL_SWITCH_FILE" in b.block_reasons

def test_network_error_on_order_blocks(mk):
    f = FakeOKX(order_code="-1"); b = mk(f)
    b.preflight(); b.scan_and_execute()
    assert any(r.startswith("ORDER_STATE_UNKNOWN") for r in b.block_reasons)

def test_exchange_closed_position_removed_after_sl(mk):
    local = {SYM: {"entry_price": 0.5, "side": "BUY", "num_contracts": 2, "sl_attached": True, "cl_ord_id": "x"}}
    f = FakeOKX(positions=[]); b = mk(f, local)
    assert b.preflight()
    assert SYM not in b.active_positions


def test_open_risk_uses_risk_usd_when_present(mk):
    b = mk(FakeOKX(), local={SYM: {"entry_price": 1.0, "num_contracts": 10, "risk_usd": 0.42}})
    assert b._open_risk_usd() == pytest.approx(0.42)

def test_open_risk_legacy_record_with_sl_price(mk):
    b = mk(FakeOKX(), local={SYM: {"entry_price": 1.3686, "sl_price": 1.30, "num_contracts": 51}})
    assert b._open_risk_usd() == pytest.approx(abs(1.3686 - 1.30) * 1.0 * 51)

def test_open_risk_legacy_record_uses_exchange_sl(mk):
    local = {SYM: {"entry_price": 1.3686, "num_contracts": 51}}
    f = FakeOKX(positions=[{"instId": SYM, "pos": "51"}], algos=[{"instId": SYM, "slTriggerPx": "1.3025"}])
    b = mk(f, local=local)
    b.preflight()
    assert b._open_risk_usd() == pytest.approx(abs(1.3686 - 1.3025) * 51)

def test_open_risk_legacy_record_without_any_sl_is_not_zero(mk):
    b = mk(FakeOKX(), local={SYM: {"entry_price": 1.3686, "num_contracts": 51}})
    assert b._open_risk_usd() == pytest.approx(1.3686 * b.stop_loss_pct * 51)
    assert b._open_risk_usd() > 0


# ------------------------------------------------------- signalkvalitet + cross-bot
def test_trend_filter_blocks_long_in_downtrend(mk):
    f = FakeOKX(); f.trend_up = False; b = mk(f)
    assert b.preflight()
    b.try_entry(SYM, 70.0)
    assert f.orders == [] and SYM not in b.active_positions

def test_obi_must_persist(mk):
    f = FakeOKX(); b = mk(f)
    books = iter([{"code": "0", "data": [{"ts": str(time.time() * 1000), "bids": [["0.5000", "900"]], "asks": [["0.5001", "100"]]}]},
                  {"code": "0", "data": [{"ts": str(time.time() * 1000), "bids": [["0.5000", "100"]], "asks": [["0.5001", "900"]]}]}])
    f.get_orderbook = lambda inst_id, sz=15: next(books)
    assert b.preflight()
    b.try_entry(SYM, 70.0)
    assert f.orders == []

def test_peer_same_coin_blocks_entry(mk):
    f = FakeOKX(); b = mk(f)
    assert b.preflight()
    b.peer_exposure = [("XRP", False)]
    b.try_entry(SYM, 70.0)
    assert f.orders == []

def test_direction_cap_blocks_entry(mk):
    f = FakeOKX(); b = mk(f)
    assert b.preflight()
    b.peer_exposure = [("BTC", True), ("ETH", True), ("SOL", True), ("SUI", True)]
    b.try_entry(SYM, 70.0)
    assert f.orders == []


# ------------------------------------------------------- short-only + max 3x (Roger 2026-10-22)
SELL_BOOK = {"bids": [["0.5000", "100"]], "asks": [["0.5001", "900"]]}

def _sell_book(f):
    f.get_orderbook = lambda inst_id, sz=15: {"code": "0", "data": [dict(ts=str(time.time() * 1000), **SELL_BOOK)]}

def test_default_is_short_only_and_max_3x(mk, monkeypatch):
    monkeypatch.delenv("SHORT_ONLY", raising=False)
    monkeypatch.setenv("MAX_LEVERAGE", "10")
    b = botmod.LenaAggressiveBot()
    assert b.short_only is True and b.max_leverage <= 3

def test_short_only_blocks_long_entry(mk):
    f = FakeOKX(); b = mk(f, short_only=True)   # köpsidig bok => long-signal
    assert b.preflight()
    b.scan_and_execute()
    assert f.orders == [] and f.lev_calls == [] and SYM not in b.active_positions

def test_short_only_allows_short_with_sl_and_max_3x(mk):
    f = FakeOKX(); f.trend_up = False; _sell_book(f)
    b = mk(f, short_only=True); b.max_leverage = 10  # även om env är fel
    assert b.preflight()
    b.try_entry(SYM, 70.0)
    assert len(f.orders) == 1 and f.orders[0]["side"] == "sell"
    assert f.orders[0]["attach_algo_ords"][0]["slTriggerPx"]
    assert all(int(c["lever"]) <= 3 for c in f.lev_calls)
    assert b.active_positions[SYM]["leverage"] <= 3


# ------------------------------------------------------- post-only limit-entry (Roger 2026-10-31)
def _short(mk, f):
    f.trend_up = False; _sell_book(f)
    b = mk(f, short_only=True)
    assert b.preflight()
    return b

def test_postonly_short_rests_at_best_ask_with_sl_tp(mk):
    f = FakeOKX(); b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    o = f.orders[0]
    assert o["ord_type"] == "post_only" and o["side"] == "sell" and o["px"] == "0.5001"
    a = o["attach_algo_ords"][0]
    assert float(a["slTriggerPx"]) == pytest.approx(0.5001 * 1.018, rel=1e-6) and a["tpTriggerPx"]
    assert b.active_positions[SYM]["sl_attached"] and b.active_positions[SYM]["entry_ord_type"] == "post_only"

def test_postonly_unfilled_is_cancelled_no_position_no_block(mk):
    f = FakeOKX(order_state="live", fill_sz="0"); b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    assert f.cancels and f.cancels[0][2] == f.orders[0]["cl_ord_id"]
    assert SYM not in b.active_positions
    assert b.block_reasons == []

def test_postonly_cancel_unverified_blocks(mk):
    f = FakeOKX(order_state="live", fill_sz="0"); f.cancel_works = False; b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    assert SYM not in b.active_positions
    assert any(r.startswith("ENTRY_CANCEL_UNVERIFIED") for r in b.block_reasons)

def test_postonly_partial_cancels_remainder_and_records_filled_with_sl(mk):
    f = FakeOKX(order_state="partially_filled", fill_sz="3"); b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    assert f.cancels
    p = b.active_positions[SYM]
    assert p["num_contracts"] == 3 and p["sl_attached"]
    assert any(r.startswith("PARTIAL_FILL") for r in b.block_reasons)

def test_postonly_filled_but_sl_missing_places_standalone_sl(mk):
    f = FakeOKX(attach_ok=False); b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    assert f.algo_placed and b.active_positions[SYM]["sl_attached"]

def test_postonly_rejected_leaves_nothing(mk):
    f = FakeOKX(order_code="1"); b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    assert SYM not in b.active_positions and b.block_reasons == [] and f.cancels == []

def test_postonly_unknown_state_blocks_and_tries_cancel(mk):
    f = FakeOKX(order_code="-1"); b = _short(mk, f)
    b.try_entry(SYM, 70.0)
    assert any(r.startswith("ORDER_STATE_UNKNOWN") for r in b.block_reasons) and f.cancels

def test_market_rollback_switch(mk):
    f = FakeOKX(); b = _short(mk, f); b.entry_ord_type = "market"
    b.try_entry(SYM, 70.0)
    assert f.orders[0]["ord_type"] == "market" and f.orders[0].get("px") is None

def test_preflight_cancels_stale_postonly_entries_only(mk):
    f = FakeOKX()
    f.open_orders = [{"instId": SYM, "ordId": "7", "clOrdId": "lena123", "ordType": "post_only"},
                     {"instId": SYM, "ordId": "8", "clOrdId": "lenax123", "ordType": "market"},
                     {"instId": SYM, "ordId": "9", "clOrdId": "other", "ordType": "post_only"}]
    b = mk(f); b.preflight()
    assert f.cancels == [(SYM, "7", "lena123")]
