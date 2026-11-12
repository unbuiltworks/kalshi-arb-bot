"""Same-side extension (the engine's existing BALANCE steer) against the same engine without it, on WIDER, NOT APPROVED limits
($7 cash, 8 live orders, 4 rounds at once) because an extension cannot happen inside $0.90. Real runner, recorded books, replay."""
import sys, json, io, contextlib, importlib.util, multiprocessing as mp
sys.path.insert(0, "/home/user/kalshi-arb-bot")
spec = importlib.util.spec_from_file_location("rr", "/home/user/kalshi-arb-bot/analysis/l5/runner_replay_20261111.py"); RR = importlib.util.module_from_spec(spec); spec.loader.exec_module(RR)
REC = {"L5_INVENTORY_RECOVERY": "ON", "L5_TS_COMPLETION_MAX_PAIR": "0.98", "L5_TS_REQUOTE_KEEPS_ROUND": "ON", "L5_LIVE_AUTH_REDUCING_REPLACE_USES_COMPLETE": "ON"}
def one(job):
    slug, name = job
    import tests.test_l5_continuous_config as CC
    env = dict(CC.FOUR, **REC)
    if name == "wide+extend": env.update(CC.STEER)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            res = RR.replay(RR.books()[slug]["rows"], env, limits=CC.limits)
    except BaseException as e:
        return {"slug": slug, "name": name, "fatal": "%s %s" % (type(e).__name__, str(e)[:120])}
    up, dn = res["filled"]["UP"], res["filled"]["DOWN"]; win = RR.books()[slug]["winner"]
    same = sum(v for k, v in res["stats"].items() if "portfolio" in k.lower() and ("add" in k.lower() or "ADD" in k))
    return {"slug": slug, "name": name, "matched": float(min(up, dn)), "unmatched": float(abs(up - dn)), "orders": len(res["sent"]), "paid": float(res["paid"]["UP"] + res["paid"]["DOWN"]),
            "pnl": float((up if win == "UP" else dn) - res["paid"]["UP"] - res["paid"]["DOWN"]), "adds": same}
if __name__ == "__main__":
    slugs = sorted(RR.books())[::3]
    with mp.Pool(3) as pool: out = pool.map(one, [(s, n) for n in ("wide", "wide+extend") for s in slugs], chunksize=2)
    json.dump(out, open("/home/user/kalshi-arb-bot/data/l5/recent/live_20261111_1900z/extend380.json", "w"))
    print("done", len(out), sum(1 for o in out if "fatal" in o), [o["fatal"] for o in out if "fatal" in o][:2])
