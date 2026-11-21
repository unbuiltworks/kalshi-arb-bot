import sys, json, io, contextlib, importlib.util, multiprocessing as mp
spec = importlib.util.spec_from_file_location("rr", "/home/user/kalshi-arb-bot/analysis/l5/runner_replay_20261111.py"); RR = importlib.util.module_from_spec(spec); spec.loader.exec_module(RR)
def one(job):
    slug, base, ttl = job
    env = dict(RR.CONFIGS[base], L5_TS_ORDER_TTL_S=str(ttl), L5_LIVE_AUTH_MAX_REPLACE="400", L5_ACC_MAX_ATTEMPTS="40")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = RR.replay(RR.books()[slug]["rows"], env)
    except BaseException as e:
        return {"slug": slug, "base": base, "ttl": ttl, "fatal": str(e)[:100]}
    up, dn = res["filled"]["UP"], res["filled"]["DOWN"]; win = RR.books()[slug]["winner"]
    return {"slug": slug, "base": base, "ttl": ttl, "matched": float(min(up, dn)), "unmatched": float(abs(up - dn)), "orders": len(res["sent"]),
            "pnl": float((up if win == "UP" else dn) - res["paid"]["UP"] - res["paid"]["DOWN"])}
if __name__ == "__main__":
    slugs = sorted(RR.books())
    jobs = [(s, b, t) for b in ("as_deployed", "recovery_0.98") for t in (15, 30) for s in slugs]
    with mp.Pool(3) as pool: out = pool.map(one, jobs, chunksize=2)
    json.dump(out, open("/home/user/kalshi-arb-bot/data/l5/recent/live_20261111_1900z/ttl380.json", "w"))
    print("done", len(out), sum(1 for o in out if "fatal" in o))
