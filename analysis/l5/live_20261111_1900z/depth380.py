import sys, json, io, contextlib, importlib.util, multiprocessing as mp
from decimal import Decimal as D
spec = importlib.util.spec_from_file_location("rr", "/home/user/kalshi-arb-bot/analysis/l5/runner_replay_20261111.py"); RR = importlib.util.module_from_spec(spec); spec.loader.exec_module(RR)
BASE = dict(RR.CONFIGS["recovery_0.98"])
def one(job):
    slug, depth = job
    env = dict(BASE, L5_TS_DEPTH_TICKS=str(depth))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = RR.replay(RR.books()[slug]["rows"], env)
    except BaseException as e:
        return {"slug": slug, "depth": depth, "fatal": str(e)[:100]}
    up, dn = res["filled"]["UP"], res["filled"]["DOWN"]; win = RR.books()[slug]["winner"]
    return {"slug": slug, "depth": depth, "matched": float(min(up, dn)), "unmatched": float(abs(up - dn)), "orders": len(res["sent"]),
            "pnl": float((up if win == "UP" else dn) - res["paid"]["UP"] - res["paid"]["DOWN"])}
if __name__ == "__main__":
    slugs = sorted(RR.books()); depths = [4, 6, 8]
    with mp.Pool(4) as pool: out = pool.map(one, [(s, d) for d in depths for s in slugs], chunksize=2)
    json.dump(out, open("/home/user/kalshi-arb-bot/data/l5/recent/live_20261111_1900z/depth380.json", "w"))
    print("done", len(out), sum(1 for o in out if "fatal" in o))
