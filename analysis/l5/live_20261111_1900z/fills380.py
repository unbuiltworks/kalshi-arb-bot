"""Per window, through the real runner (recovery at 0.98): the first-leg fill and what the book did around it."""
import sys, json, io, contextlib, importlib.util, multiprocessing as mp
from decimal import Decimal as D
spec = importlib.util.spec_from_file_location("rr", "/home/user/kalshi-arb-bot/analysis/l5/runner_replay_20261111.py"); RR = importlib.util.module_from_spec(spec); spec.loader.exec_module(RR)
ENV = dict(RR.CONFIGS["recovery_0.98"])
def one(slug):
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = RR.replay(RR.books()[slug]["rows"], ENV)
    except BaseException as e:
        return {"slug": slug, "fatal": str(e)[:100]}
    log = res["log"]; rows = RR.books()[slug]["rows"]
    first = None; t_open = None; open_px = None
    for i, r in enumerate(log):
        if t_open is None and r["up_rest"] and r["dn_rest"]:
            t_open = r["t"]; open_px = (r["up_rest"][0], r["dn_rest"][0]); open_mid = (float(r["bid"]) + float(r["ask"])) / 2
        up, dn = D(r["up"]), D(r["dn"])
        if first is None and (up > 0 or dn > 0):
            side = "UP" if up > 0 else "DOWN"
            first = {"t": r["t"], "side": side, "i": i}
            break
    if first is None or t_open is None:
        return {"slug": slug, "filled": False, "opened": t_open is not None}
    i = first["i"]; side = first["side"]
    paid = float(open_px[0] if side == "UP" else open_px[1])
    mid = lambda r: (float(r["bid"]) + float(r["ask"])) / 2
    held = lambda r: mid(r) if side == "UP" else 1 - mid(r)
    at_fill = held(log[i]); 
    def at(t):
        c = [r for r in log if r["t"] >= t]
        return held(c[0]) if c else None
    # the cheapest pair quotable AT the other side's bid after the fill (maker), and whether the pair completed
    best_pair = min((paid + ((1 - float(r["ask"])) if side == "UP" else float(r["bid"]))) for r in log[i:])
    up, dn = res["filled"]["UP"], res["filled"]["DOWN"]
    win = RR.books()[slug]["winner"]
    pnl = float((up if win == "UP" else dn) - res["paid"]["UP"] - res["paid"]["DOWN"])
    return {"slug": slug, "filled": True, "t_open": t_open, "t_fill": first["t"], "since_open": first["t"] - t_open, "side": side, "paid": paid,
            "move_at_fill": round(at_fill - (open_mid if side == "UP" else 1 - open_mid), 4),      # held side's mid move from posting to the fill tick (negative = against us)
            "after_30": None if at(first["t"] + 30) is None else round(at(first["t"] + 30) - paid, 4), "after_300": None if at(first["t"] + 300) is None else round(at(first["t"] + 300) - paid, 4),
            "best_pair_at_touch": round(best_pair, 4), "matched": float(min(up, dn)), "unmatched": float(abs(up - dn)), "pnl": pnl, "orders": len(res["sent"])}
if __name__ == "__main__":
    slugs = sorted(RR.books())
    with mp.Pool(4) as pool: out = pool.map(one, slugs, chunksize=2)
    json.dump(out, open("/home/user/kalshi-arb-bot/data/l5/recent/live_20261111_1900z/fills380.json", "w"))
    print("done", len(out), sum(1 for o in out if o.get("filled")), sum(1 for o in out if "fatal" in o))
