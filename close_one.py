"""Engångsstängning (reduce-only) av en Lena-position + städning av SL-algos. Körs via close_position.yml."""
import json, os, sys, time
from okx_client import OKXClient

inst = sys.argv[1]
c = OKXClient(os.environ["OKX_API_KEY"], os.environ["OKX_API_SECRET"], os.environ["OKX_API_PASSPHRASE"],
              base_url=os.environ.get("OKX_BASE_URL", "https://eea.okx.com"))
pos = [p for p in c.get_positions("FUTURES").get("data", []) if p.get("instId") == inst and float(p.get("pos") or 0) != 0]
if not pos:
    print(f"Ingen öppen position i {inst} på OKX.")
else:
    sz = float(pos[0]["pos"]); side = "sell" if sz > 0 else "buy"
    print(f"Position {inst}: pos={sz} avgPx={pos[0].get('avgPx')} upl={pos[0].get('upl')}")
    cl = f"lenaclose{int(time.time()*1000)}"
    r = c.place_order(inst_id=inst, td_mode="cross", side=side, ord_type="market", sz=f"{abs(sz):g}",
                      pos_side="net", cl_ord_id=cl, reduce_only=True)
    print("Close order:", r.get("code"), r.get("msg"), [(d.get("sCode"), d.get("sMsg")) for d in r.get("data", [])])
    if r.get("code") != "0":
        sys.exit(1)
    time.sleep(2)
    o = (c.get_order(inst, cl_ord_id=cl).get("data") or [{}])[0]
    print(f"Fill: state={o.get('state')} avgPx={o.get('avgPx')} accFillSz={o.get('accFillSz')} pnl={o.get('pnl')} fee={o.get('fee')}")
    # uppdatera lokal state
    try:
        ap = json.load(open("active_positions.json"))
        if inst in ap:
            p = ap.pop(inst)
            hist = json.load(open("trades_history.json")) if os.path.exists("trades_history.json") else []
            hist.insert(0, {"id": f"TRD-LENA-{int(time.time()*1000)}", "bot": "Aggressiva Lena", "symbol": inst,
                            "side": p.get("side"), "leverage": p.get("leverage"), "entry_price": p.get("entry_price"),
                            "exit_price": float(o.get("avgPx") or 0), "size_units": p.get("num_contracts"),
                            "realized_pnl": float(o.get("pnl") or 0), "exit_reason": "MANUAL_CLOSE_ROGER",
                            "status": "CLOSED", "exit_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            json.dump(ap, open("active_positions.json", "w"), indent=2)
            json.dump(hist, open("trades_history.json", "w"), indent=2)
    except Exception as e:
        print("State-uppdatering misslyckades:", e)
# städa SL/TP-algos för instrumentet
algos = [a for a in c.get_algo_orders_pending("conditional", "FUTURES").get("data", []) if a.get("instId") == inst]
algos += [a for a in c.get_algo_orders_pending("oco", "FUTURES").get("data", []) if a.get("instId") == inst]
if algos:
    r = c._request("POST", "/api/v5/trade/cancel-algos", data=[{"algoId": a["algoId"], "instId": inst} for a in algos])
    print("Cancel algos:", r.get("code"), r.get("msg"), [(d.get("algoId"), d.get("sCode")) for d in r.get("data", [])])
time.sleep(1)
left = [p for p in c.get_positions("FUTURES").get("data", []) if p.get("instId") == inst and float(p.get("pos") or 0) != 0]
la = [a for t in ("conditional", "oco") for a in c.get_algo_orders_pending(t, "FUTURES").get("data", []) if a.get("instId") == inst]
print(f"VERIFY: open_pos={len(left)} pending_algos={len(la)}")
sys.exit(0 if not left and not la else 1)
