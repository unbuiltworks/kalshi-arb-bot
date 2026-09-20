"""Does compatible opposing flow predict L5-style recycling on PMUS?

EXPLORATORY and PROSPECTIVE cohorts are split on the preregistered boundary
(complement-quote timestamp vs stamped_epoch) and are NEVER pooled. Bins are
the five preregistered ones; flow is also tested CONTINUOUS so nothing depends
on the bin edges. No selector is fitted and none is proposed.
"""
import sys, json, collections, statistics as st
sys.path.insert(0, "/home/user/kalshi-arb-bot")
sys.path.insert(0, "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad")
import survival as S   # re-runs the cohort-A replay on current data

PRE = json.load(open("/home/user/kalshi-arb-bot/analysis/pmus/"
                     "PREREGISTRATION_compatible_flow.json"))
T0 = PRE["stamped_epoch"]
BINS = [(b["bin"], b["lo"], b["hi"]) for b in PRE["FIXED_BINS_CHOSEN_NOW"]]
HOR = [60.0, 120.0, 300.0, 600.0]

def binof(x):
    for name, lo, hi in BINS:
        if x >= lo and (hi is None or x < hi): return name
    return "?"

def km_at(chains, key, h):
    """KM cumulative incidence at horizon h; right-censored leave the risk set."""
    obs = []
    for c in chains:
        if c["t_quote"] is None: continue
        end = max(c["end_t"] - c["t_quote"], 0.0)
        if c.get(key) is not None:
            obs.append((max(c[key] - c["t_quote"], 0.0), 1))
        else:
            obs.append((end, 0))
    if not obs: return None, 0
    obs.sort(); n = len(obs); Sv = 1.0; risk = n; i = 0
    while i < len(obs):
        t = obs[i][0]; d = 0; cn = 0; j = i
        while j < len(obs) and obs[j][0] == t:
            d += obs[j][1]; cn += (1 - obs[j][1]); j += 1
        if t > h: break
        if d and risk: Sv *= (1 - d / risk)
        risk -= (d + cn); i = j
    return round(100.0 * (1 - Sv), 1), n

def spearman(xs, ys):
    n = len(xs)
    if n < 6: return None
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); r = [0.0]*len(v); i = 0
        while i < len(o):
            j = i
            while j+1 < len(o) and v[o[j+1]] == v[o[i]]: j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j+1): r[o[k]] = avg
            i = j + 1
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx)/n, sum(ry)/n
    num = sum((a-mx)*(b-my) for a, b in zip(rx, ry))
    den = (sum((a-mx)**2 for a in rx) * sum((b-my)**2 for b in ry)) ** 0.5
    return round(num/den, 3) if den else None

for rule in ("QUEUE_AWARE", "OPTIMISTIC"):
    cs = [c for c in S.res[rule]["PMUS"] if c["eligible"] and c["t_quote"] is not None]
    expl = [c for c in cs if c["t_quote"] <= T0]
    prosp = [c for c in cs if c["t_quote"] > T0]
    print("=" * 100)
    print("PMUS %s  COMPLETE-eligible: %d total  |  EXPLORATORY %d  PROSPECTIVE %d"
          % (rule, len(cs), len(expl), len(prosp)))
    print("  checkpoint >=25 queue-aware eligible: %s"
          % ("MET (%d)" % len(cs) if rule == "QUEUE_AWARE" and len(cs) >= 25
             else ("NOT MET (%d of 25)" % len(cs) if rule == "QUEUE_AWARE" else "n/a")))
    print("=" * 100)
    for lab, coh in (("EXPLORATORY", expl), ("PROSPECTIVE", prosp)):
        if not coh:
            print("  %s: n=0 -- nothing to report yet" % lab); continue
        print("  -- %s cohort, n=%d --" % (lab, len(coh)))
        H = ("     %-12s %4s %9s %8s %8s %8s %8s %9s %9s"
             % ("flow bin", "n", "flow/min", "fl@60", "fl@120", "fl@300", "fl@600",
                "qafill@600", "match@600"))
        print(H)
        for name, lo, hi in BINS:
            g = [c for c in coh if binof(c["flow_per_min"] or 0.0) == name]
            if not g: continue
            f = [km_at(g, "t_flow", h)[0] for h in HOR]
            qa = km_at(g, "t_qafill", 600.0)[0]
            mt = km_at(g, "t_matched", 600.0)[0]
            print("     %-12s %4d %9.3f %8s %8s %8s %8s %9s %9s"
                  % (name, len(g), st.median([c["flow_per_min"] or 0 for c in g]),
                     f[0], f[1], f[2], f[3], qa, mt))
        xs = [c["flow_per_min"] or 0.0 for c in coh]
        print("     CONTINUOUS (Spearman rho vs flow/min, no bins):")
        for oname, key in (("flow arrived", "t_flow"), ("qa opposite fill", "t_qafill"),
                           ("qa matched", "t_matched")):
            ys = [1.0 if c.get(key) is not None else 0.0 for c in coh]
            print("       %-18s rho=%-7s (events %d/%d)"
                  % (oname, spearman(xs, ys), int(sum(ys)), len(ys)))
        lat = [(c["t_flow"] - c["t_quote"], c["flow_per_min"] or 0.0)
               for c in coh if c["t_flow"] is not None]
        if len(lat) >= 6:
            print("       %-18s rho=%-7s (n=%d)  median latency %.0fs"
                  % ("latency vs flow", spearman([x[1] for x in lat], [x[0] for x in lat]),
                     len(lat), st.median([x[0] for x in lat])))
        pb = [(c["pair_basis"], c["flow_per_min"] or 0.0) for c in coh
              if c["matched"] and c["pair_basis"]]
        if pb:
            print("       %-18s median=%.4f n=%d  (MODELED, not realized)"
                  % ("pair basis", st.median([x[0] for x in pb]), len(pb)))
        # covariates retained, reported, NOT used to select
        print("     covariates (median, matched vs not) -- retained, NOT a selector:")
        m = [c for c in coh if c["matched"]]; nm = [c for c in coh if not c["matched"]]
        for cov in ("depth_med", "spread_med", "move_min"):
            if m and nm:
                print("       %-14s matched %-10.4f  not %-10.4f"
                      % (cov, st.median([c[cov] for c in m]),
                         st.median([c[cov] for c in nm])))
        print()
