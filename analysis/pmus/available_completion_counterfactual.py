"""Was the 13% completion a MARKET limit or a POLICY limit?

For every PMUS first fill in the prospective set, reconstruct the subsequent
tape and ask a counterfactual that IGNORES what Model A actually quoted:

  after the first fill at cost c on side S, how much OPPOSITE-side inventory
  was economically acquirable before observation end, at pair basis <= X?

To buy the opposite side at price q the pair basis is c + q, so q <= X - c.
In YES terms, buying NO at q needs a print at YES price >= 1 - q. A print at
YES price p is therefore compatible with completing at basis <= X when:

   S == YES :  p >= 1 - (X - c)      (we buy NO at 1-p, basis c + 1 - p <= X)
   S == NO  :  p <= (X - c)          (we buy YES at p, basis c + p <= X)

OPTIMISTIC available volume counts every compatible print. QUEUE-AWARE requires
cumulative compatible volume to exceed the depth resting at the price when we
would have joined -- the same rule the shipped replay uses, so the two numbers
are comparable.

NO FUTURE INFORMATION IS USED TO CHOOSE ANYTHING. The counterfactual only
measures what was on the tape after the first fill; it never selects an entry,
a price or a market using later data, and nothing here is fitted to settlement.

PMUS flow is CAUSAL INFERRED from sharesTraded deltas and is never a real print.
Direction is NOT observable, so "compatible volume" is an UPPER BOUND on what
could have been taken from the other side.
"""
import sys, json, collections, statistics as stt
sys.path.insert(0, '.')
import cohorts as C

BASES = [1.00, 0.99, 0.98, 0.97]


def build():
    return C.books(), C.flow()


def episode_counterfactual(e, se, prints, basis_list=BASES):
    """se = book series for the slug, prints = inferred flow for the slug."""
    t0 = float(e["t_first"])
    end = float(e["t_end"])
    post = [p for p in prints if t0 < p["t"] <= end]
    book = [r for r in se if t0 < r["t"] <= end]
    yn, nn = e["yes_n"], e["no_n"]
    # side and average cost of the FIRST leg's side (the side we must complete)
    if yn >= nn and yn:
        side_yes, cost = True, float(e["yes_cost"]) / yn
    elif nn:
        side_yes, cost = False, float(e["no_cost"]) / nn
    else:
        return None
    out = {"post_prints": len(post), "post_volume": sum(p["size"] for p in post),
           "obs_s": end - t0, "side_yes": side_yes, "first_cost": cost,
           "book_rows": len(book)}
    for X in basis_list:
        lim = X - cost
        if side_yes:
            comp = [p for p in post if p["px"] >= 1.0 - lim]
        else:
            comp = [p for p in post if p["px"] <= lim]
        vol = sum(p["size"] for p in comp)
        # queue-aware: depth resting at the level when we would join, taken from
        # the nearest book row at or before the first compatible print
        qa = 0.0
        if comp:
            t_join = comp[0]["t"]
            prior = [r for r in book if r["t"] <= t_join]
            ref = prior[-1] if prior else (book[0] if book else None)
            depth = 0.0
            if ref:
                depth = float((ref["ask_qty"] if side_yes else ref["bid_qty"]) or 0.0)
            cum = 0.0
            for p in comp:
                cum += p["size"]
                if cum > depth:
                    qa = cum - depth
                    break
        out["avail_opt_%.2f" % X] = vol
        out["avail_qa_%.2f" % X] = qa
        out["n_comp_%.2f" % X] = len(comp)
    return out
