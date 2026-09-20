"""Right-censored event-time estimation for the post-quote chain. SHADOW ONLY.

FIXES A REAL ESTIMATOR BUG. The previous table put two different denominators
in one row set: the fixed-horizon cells restricted the denominator to chains
observed at least that long, while the "market end" cell used every eligible
chain. That let a cumulative curve FALL (International compatible flow 87.0% at
600s, 78.1% at "end"), which is impossible for one cohort under one estimator.

Now:
  * KAPLAN-MEIER cumulative incidence, 1 - S(t), over ALL eligible chains.
    A chain that has not had the event by the time observation stops is
    RIGHT-CENSORED at end_t - t_quote and leaves the risk set there. It is
    never scored a miss and never silently dropped.
  * n_at_risk, events and censored are printed at every horizon.
  * MARKET_END_OBSERVED_RATE is reported SEPARATELY, clearly labelled, because
    it uses a different denominator (all eligible chains, regardless of how
    long they were watched). It is NOT appended to the cumulative table.

Clock is the COMPLEMENT QUOTE. Identical rules for both venues.
"""
import sys, json, collections

SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
HOR = [10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
EVENTS = [("compat flow", "t_flow"), ("qa fill", "t_qafill"), ("matched", "t_matched")]

def km(chains, key):
    """(time, observed) pairs -> Kaplan-Meier cumulative incidence."""
    obs = []
    for c in chains:
        if c["t_quote"] is None: continue
        end = c["end_t"] - c["t_quote"]
        if c.get(key) is not None:
            t = c[key] - c["t_quote"]
            # an event recorded at or before the quote is degenerate; clamp to 0
            obs.append((max(t, 0.0), 1))
        else:
            obs.append((max(end, 0.0), 0))
    if not obs: return None
    obs.sort()
    n = len(obs); S = 1.0; at_risk = n
    curve = []            # (time, S_after, events_here, censored_here, at_risk)
    i = 0
    while i < len(obs):
        t = obs[i][0]
        d = 0; cens = 0; j = i
        while j < len(obs) and obs[j][0] == t:
            if obs[j][1] == 1: d += 1
            else: cens += 1
            j += 1
        risk_here = at_risk
        if d > 0 and risk_here > 0:
            S *= (1.0 - d / risk_here)
        curve.append((t, S, d, cens, risk_here))
        at_risk -= (d + cens)
        i = j
    out = {}
    for h in HOR:
        S_h = 1.0; ev = 0; cn = 0; risk_at = n
        for (t, s, d, c2, r) in curve:
            if t <= h:
                S_h = s; ev += d; cn += c2
            else:
                risk_at = r; break
        else:
            risk_at = 0
        out["by_%ds" % int(h)] = {"km_cum_incidence_pct": round(100.0 * (1.0 - S_h), 1),
                                  "events_by_t": ev, "censored_by_t": cn,
                                  "n_at_risk_after_t": risk_at, "n_eligible": n}
    ev_tot = sum(1 for c in chains if c["t_quote"] is not None and c.get(key) is not None)
    out["_MARKET_END_OBSERVED_RATE"] = {
        "pct": round(100.0 * ev_tot / n, 1), "events": ev_tot, "n_eligible": n,
        "NOTE": "DIFFERENT DENOMINATOR from the cumulative table: every eligible "
                "chain regardless of how long it was observed. Reported "
                "separately and NOT comparable cell-by-cell with the KM row."}
    return out

ch = json.load(open(SCR + "/survival_chains.json"))
report = {"estimator": "Kaplan-Meier cumulative incidence, 1 - S(t), "
                       "right-censored at end_t - t_quote",
          "clock": "COMPLEMENT QUOTE",
          "note": "MARKET_END_OBSERVED_RATE uses a different denominator and is "
                  "reported separately, never appended to the cumulative table."}
for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
    for venue in ("PMUS", "INTL"):
        cs = [c for c in ch[rule].get(venue, []) if c["eligible"] and c["t_quote"] is not None]
        if not cs: continue
        for lab, k in EVENTS:
            r = km(cs, k)
            if r: report["%s|%s|%s" % (venue, rule, lab)] = r

H = ("  %-26s %-12s" % ("cohort", "event")
     + "".join("%18s" % ("by%ds" % int(h)) for h in HOR))
print("KAPLAN-MEIER CUMULATIVE INCIDENCE  (cum%% / events / censored / at-risk-after)")
print(H); print("  " + "-" * (len(H) - 2))
for venue in ("PMUS", "INTL"):
    for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
        for lab, k in EVENTS:
            r = report.get("%s|%s|%s" % (venue, rule, lab))
            if not r: continue
            line = "  %-26s %-12s" % ("%s|%s n=%d" % (venue, rule[:4],
                    r["by_10s"]["n_eligible"]), lab)
            for h in HOR:
                c = r["by_%ds" % int(h)]
                line += "%18s" % ("%.1f/%d/%d/%d" % (c["km_cum_incidence_pct"],
                        c["events_by_t"], c["censored_by_t"], c["n_at_risk_after_t"]))
            print(line)
    print()
print("MARKET_END_OBSERVED_RATE  -- SEPARATE STATISTIC, DIFFERENT DENOMINATOR")
for venue in ("PMUS", "INTL"):
    for rule in ("OPTIMISTIC", "QUEUE_AWARE"):
        for lab, k in EVENTS:
            r = report.get("%s|%s|%s" % (venue, rule, lab))
            if not r: continue
            m = r["_MARKET_END_OBSERVED_RATE"]
            print("  %-26s %-12s %5.1f%%  (%d events / %d eligible)"
                  % ("%s|%s" % (venue, rule[:4]), lab, m["pct"], m["events"], m["n_eligible"]))
json.dump(report, open(SCR + "/km.json", "w"), indent=1, default=str)
