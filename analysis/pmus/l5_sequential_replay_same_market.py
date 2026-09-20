"""P0-B/P0-C: frozen L5 sequential replay, STRICTLY SAME-MARKET.

SHADOW ONLY. Nothing here submits, fills, settles or realizes anything.
EVIDENCED FILL here is a queue-aware or optimistic MODEL of an unpublished
queue, never venue confirmation.

P0-C. Every quantity is computed inside ONE slug's own series and its own
flow. No fill in one market can ever pair with a fill in another. The pooled
OPPOSITE_RECYCLING statistic is not read, imported or reproduced here; it is
preserved in the surface stream for provenance and is superseded for every
behavioral conclusion.

MODEL A IS UNCHANGED. The policy calls -- Inventory, _proposal, plan_action,
DECISION_MIN_GAP_S, MAX_RESIDUAL_UNITS -- are exactly the shipped ones. This
module only INSTRUMENTS the sequence; it never alters an action.

L5 SHAPE IS PRESERVED. An episode is not closed at the first matched pair and
sides are not forced equal: it runs OPEN -> EXTEND* -> COMPLETE* and ends only
when inventory returns to flat having traded both sides, or when observation
ends. Unequal leg counts are normal and pair basis uses the per-side AVERAGE
cost, so repeated EXTEND is represented rather than truncated.
"""
import sys, json, collections, statistics as stt
from decimal import Decimal as D
SCR = "/tmp/claude-0/-home-user-kalshi-arb-bot/cfee5a9a-5927-5460-9489-c565191b2d65/scratchpad"
sys.path.insert(0, "/home/user/kalshi-arb-bot"); sys.path.insert(0, SCR)
import cohorts as C
from src import pmus_sports_l5_decision as dec

assert dec.MAX_RESIDUAL_UNITS == 3, "shipped cap must be unchanged"
M = C.M
LOOKAHEAD = 600.0
FRAME_T = None   # set from the SAMPLING_FRAME_CHANGE marker

PMUS_MAKER_FEE = 0.0   # venue maker fee, confirmed 0
NEW_LEAGUES = {"odicr","odcup","itfwo","itfme","dota2","setkameua","setkamecz",
               "setkamemd","atp","atpdb","wta","utr","khl","tdp","bra","brb",
               "alsv","nor1","els","swsl","ligpor","lal2","nls","lg1","ere",
               "tsl","srb","sld","pvl","uzb1","etpl","bbl","bcl","lkl","atbl"}
CONTROLS = {"lal","cs2","lol","epl","sea","bun","flc","atpcq","nb1","mlb","nfl"}


def frame_epoch():
    t = None
    for fn in ("nfl_sunday.jsonl",):
        try: fh = open(SCR + "/" + fn)
        except OSError: continue
        for l in fh:
            if "SAMPLING_FRAME_CHANGE" not in l: continue
            try: d = json.loads(l)
            except Exception: continue
            if d.get("kind") == "SAMPLING_FRAME_CHANGE":
                t = max(t or 0.0, float(d["t"]))
    return t



def compatible_window(prints, t_decision, t_last_fill, px, side_yes,
                      lookahead=LOOKAHEAD):
    """Compatible prints usable by a decision at `t_decision`.

    CAUSALITY INVARIANT. The window starts at max(t_decision, t_last_fill), so
    a fill can never be credited before the fill that preceded it in the same
    sequence. Without this a later decision whose print lands early would be
    stamped ahead of an earlier decision's late print, producing an inventory
    path that runs backwards and a NEGATIVE recycle latency.
    """
    t0 = max(float(t_decision), float(t_last_fill))
    return C.compatible(prints, t0, t0 + lookahead, px, side_yes)


def fill_candidates(prints, t_decision, t_last_fill, px, side_yes, depth,
                    lookahead=LOOKAHEAD):
    """(compatible prints, queue-aware fill time or None) for one decision."""
    comp = compatible_window(prints, t_decision, t_last_fill, px, side_yes,
                             lookahead)
    cum = 0.0
    for p in comp:
        cum += p["size"]
        if cum > depth:
            return comp, p["t"]
    return comp, None


def episodes(venue, slug, se, prints, fill_rule):
    """One slug. Returns closed and open episodes with full sequence detail."""
    inv = dec.Inventory(); last_rel = ""; t_prev = 0.0
    out = []; cur = None
    # CAUSALITY. Decisions are sequential, but a fill is timestamped at the
    # PRINT that filled it, and a later decision's print can land earlier than
    # an earlier decision's. Crediting both would build an inventory path that
    # runs backwards in time and can report a negative recycle latency. A fill
    # may therefore never be credited before the previous fill in the same
    # sequence: the compatible-print window starts at max(decision, last fill).
    t_last_fill = 0.0
    for r in se:
        t = r["t"]
        if t - t_prev < dec.DECISION_MIN_GAP_S: continue
        t_prev = t
        bk = {"bid": D(str(r["bid"])), "ask": D(str(r["ask"])), "t": t}
        prop = dec._proposal(M, float(r["bid"]), inv, last_rel)
        if prop.get("off_distribution"): continue
        rel = prop["recommended_relation"]
        if cur is not None and rel == "complete": cur["complete_eligible"] = True
        plan = dec.plan_action(rel, inv, bk)
        if plan["decision"] != "quote": continue
        px = float(plan["_px"]); side_yes = plan["_side_yes"]; rl = plan["relation"]
        depth = float((r["bid_qty"] if side_yes else r["ask_qty"]) or 0.0)
        comp, t_qa = fill_candidates(prints, t, t_last_fill, px, side_yes,
                                     depth)
        if cur is not None and rl == "complete":
            if cur["t_complete_quote"] is None:
                cur["t_complete_quote"] = t; cur["quote_depth"] = depth
                cur["quote_spread"] = round(r["ask"] - r["bid"], 4)
            if comp and cur["t_compat_flow"] is None:
                cur["t_compat_flow"] = comp[0]["t"]
            if t_qa is not None and cur["t_qa_opposite_fill"] is None:
                cur["t_qa_opposite_fill"] = t_qa
        t_fill = (comp[0]["t"] if comp else None) if fill_rule == "OPTIMISTIC" else t_qa
        if t_fill is None:
            if cur is not None and rl == "extend": cur["extend_quotes"] += 1
            last_rel = rl; continue
        # --- an EVIDENCED FILL under this fill model ---
        assert t_fill >= t_last_fill, "fill credited before the previous fill"
        t_last_fill = t_fill
        inv.record(side_yes, D(str(px))); last_rel = rl
        if cur is None and rl == "open":
            cur = {"venue": venue, "slug": slug, "league": r.get("league"),
                   "sports_type": r.get("sports_type"), "t_first": t_fill,
                   "first_side_yes": side_yes, "fills": 0,
                   "yes_cost": 0.0, "yes_n": 0, "no_cost": 0.0, "no_n": 0,
                   "extend_fills_before_opposite": 0, "extend_quotes": 0,
                   "complete_eligible": False, "t_complete_quote": None,
                   "t_compat_flow": None, "t_qa_opposite_fill": None,
                   "t_first_opposite": None, "t_matched": None,
                   "quote_depth": None, "quote_spread": None,
                   "matched_qty": 0, "pair_basis": None, "closed": False}
        if cur is None: continue
        cur["fills"] += 1
        if side_yes: cur["yes_cost"] += px; cur["yes_n"] += 1
        else: cur["no_cost"] += px; cur["no_n"] += 1
        if cur["t_first_opposite"] is None:
            if side_yes != cur["first_side_yes"]:
                cur["t_first_opposite"] = t_fill
            elif rl == "extend":
                cur["extend_fills_before_opposite"] += 1
        if cur["yes_n"] and cur["no_n"]:
            cur["matched_qty"] = min(cur["yes_n"], cur["no_n"])
            cur["pair_basis"] = round(cur["yes_cost"]/cur["yes_n"]
                                      + cur["no_cost"]/cur["no_n"], 4)
            if cur["t_matched"] is None: cur["t_matched"] = t_fill
        if inv.residual == 0 and cur["yes_n"] and cur["no_n"]:
            cur["closed"] = True; cur["t_end"] = t_fill
            cur["residual_qty"] = 0; cur["residual_s"] = 0.0
            cur["end_t_obs"] = se[-1]["t"]; out.append(cur); cur = None
    if cur is not None:
        # OBSERVATION END. A fill is stamped at the PRINT that filled it and the
        # flow tape can run past the last book observation, so the book series
        # end alone is not the end of what we have seen about this episode.
        # Taking the later of the two keeps residual duration non-negative --
        # the same defect family as the negative recycle latency, and it showed
        # up as resDur = -183s on an ekst episode.
        t_obs_end = max(se[-1]["t"], t_last_fill)
        cur["t_end"] = t_obs_end
        cur["residual_qty"] = abs(int(inv.residual))
        anchor = cur["t_matched"] or cur["t_first"]
        cur["residual_s"] = round(max(0.0, t_obs_end - anchor), 1)
        assert cur["residual_s"] >= 0.0, "negative residual duration"
        cur["end_t_obs"] = t_obs_end; out.append(cur)
    return out


def run(fill_rule, since=None, venue="PMUS"):
    bks, fls = C.books(), C.flow()
    eps = []
    for (v, slug), se in bks.items():
        if v != venue: continue
        if since is not None:
            se = [r for r in se if r["t"] > since]
        if len(se) < 3: continue
        eps += episodes(v, slug, se, fls.get((v, slug), []), fill_rule)
    return eps


def pct(a, b): return (100.0 * a / b) if b else None

def summarize(eps, label):
    n = len(eps)
    first = n
    opp = [e for e in eps if e["t_first_opposite"] is not None]
    mat = [e for e in eps if e["matched_qty"] > 0]
    lat = sorted(e["t_first_opposite"] - e["t_first"] for e in opp)
    basis = sorted(e["pair_basis"] for e in mat if e["pair_basis"] is not None)
    ext = sorted(e["extend_fills_before_opposite"] for e in eps)
    unres = [e for e in eps if not e["closed"] and e["residual_qty"] > 0]
    res_s = sorted(e["residual_s"] for e in unres)
    q = lambda xs, p: xs[min(len(xs)-1, int(p*len(xs)))] if xs else None
    return {
        "cohort": label, "first_fill_episodes": first,
        "same_market_opposite_fills": len(opp),
        "matched_episodes": len(mat),
        "matched_per_100_first_fills": round(pct(len(mat), first), 1) if first else None,
        "opposite_per_100_first_fills": round(pct(len(opp), first), 1) if first else None,
        "recycle_latency_p50_s": round(q(lat, 0.5), 1) if lat else None,
        "recycle_latency_p90_s": round(q(lat, 0.9), 1) if lat else None,
        "extend_fills_before_opposite_p50": q(ext, 0.5),
        "extend_fills_before_opposite_max": (ext[-1] if ext else None),
        "matched_qty_total": sum(e["matched_qty"] for e in mat),
        "pair_basis_median": (round(stt.median(basis), 4) if basis else None),
        "pair_basis_min": (basis[0] if basis else None),
        "pair_basis_max": (basis[-1] if basis else None),
        "known_fees_usd_per_pair": PMUS_MAKER_FEE,
        "modeled_gross_per_pair_before_rebates": (
            round(1.0 - stt.median(basis), 4) if basis else None),
        "episodes_with_unresolved_residual": len(unres),
        "residual_qty_median": (stt.median([e["residual_qty"] for e in unres])
                                if unres else None),
        "residual_duration_p50_s": round(q(res_s, 0.5), 1) if res_s else None,
        "residual_duration_p90_s": round(q(res_s, 0.9), 1) if res_s else None,
    }
