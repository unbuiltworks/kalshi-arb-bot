"""One record per market episode, from candidate to realized net.

OWNER 2026-09-11: "For every new campaign, I want us accumulating:
candidate -> quote -> submitted -> fill/no-fill -> complement action ->
matched quantity -> residual -> effective pair cost -> maker/taker ->
settlement -> actual venue incentive -> realized net."

WHY THIS IS A JOINER AND NOT NEW ARITHMETIC
-------------------------------------------
Every link already exists somewhere. `session_report` rows in the durable
ledger carry the decisions (state, recommended vs acted relation, refusal,
inventory before) and the orders; `settlement_breakdown` carries matched
pairs, pair cost, residual, maker/taker split and the venue's realized
number; `pmus_incentive_audit.credited_on_fills` carries what the venue
ACTUALLY credited, per trade, keyed by market. What did not exist was one
row that spans them, so nothing could answer "of the markets we took a
first leg in, how many paired, at what cost, and what did the venue
finally pay us."

So this module computes no economics of its own that another module owns.
Settled rows delegate to l5_r5f_cohort (its reconciliation guard and its
class vocabulary); incentives come from the venue's own commission fields;
realized comes from the venue or it is absent.

THE GAP THAT ONLY THIS VIEW CAN SEE
-----------------------------------
Every settled-cohort builder is keyed on settlement, so an episode that
never filled is invisible to all of them. Those are not nothing: they are
the denominator. On 2026-09-11 the engine opened three markets in seven
minutes and exactly one of them filled both legs; a table that can only
see the one that settled would report a 100% pair rate.

THE ONE RULE THAT MAKES THIS TABLE SAFE TO READ
-----------------------------------------------
A field belonging to a stage the episode has NOT reached is None. Never
zero. Zero is a measurement; None is the absence of one, and an episode
that never filled has not "realized $0.00" -- it has no realized figure at
all. Summing None is an error you notice; summing a fabricated 0.00 is a
number you believe. `realized_net_usd` is populated from the venue's own
settlement figure and from nothing else, ever.

`pair_gross_usd` is the one DERIVED figure here, and it is derived under
the same guard l5_r5f_cohort applies: a zero-residual market is
arithmetically locked before resolution, but only if our ledger saw every
fill, so the venue's traded quantity must reconcile against the ledger's
or the derivation is refused. It is pre-fee and pre-incentive, it is
labelled as derived on every row, and it is never added to a realized
total.
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from src import l5_r5f_cohort as cohort

ONE = D("1")

# The lifecycle, in order. An episode's stage is the furthest point it
# reached; PAIRED and RESIDUAL_HELD are the two ways a filled episode
# leaves the working phase.
STAGES = (
    "CANDIDATE",          # selected out of the admitted slate
    "QUOTED",             # the model produced an action for it
    "SUBMITTED",          # at least one order went to the venue
    "NO_FILL",            # terminal: submitted, nothing filled
    "FILLED",             # at least one fill
    # RESIDUAL_HELD sorts BEFORE paired deliberately. The first ordering
    # here put it after, so a position with -11.67 residual compared as
    # "at least as far as PAIRED" and was handed a locked pair value -- a
    # directional position reported as arithmetically riskless, which is
    # the worst error this table could make. The ordering is now right AND
    # _pair_gross checks the residual itself; the check is what carries
    # the meaning, the ordering is just no longer a trap.
    "RESIDUAL_HELD",      # filled, residual outstanding, held to settlement
    "PAIRED",             # filled and residual is exactly zero
    "SETTLED",            # the venue resolved the position
    "REALIZED",           # the venue published a realized number
)
_ORDER = {s: i for i, s in enumerate(STAGES)}

# fields that may only be populated at or beyond a given stage. The test
# suite walks this table, so adding a field without placing it fails.
STAGE_OF_FIELD = {
    "candidate": "CANDIDATE",
    "quote": "QUOTED",
    "submitted": "SUBMITTED",
    "fills": "SUBMITTED",           # a no-fill episode reports zero fills
    "complement_action": "SUBMITTED",
    "matched_qty": "FILLED",
    "residual_units": "FILLED",
    "effective_pair_cost": "FILLED",
    "maker_taker": "FILLED",
    "pair_gross_usd": "PAIRED",
    "settlement": "SETTLED",
    "venue_incentive_usd": "SUBMITTED",   # credited on fills, if any
    "realized_net_usd": "REALIZED",
}


def _d(v: Any) -> Optional[D]:
    return cohort._d(v)


# ------------------------------------------------------------ ingestion
def iter_campaign_rows(paths: Iterable[str]):
    """Yield (timestamp, object) for every PMUS_L5_CAMPAIGN row in the
    dumps. Same dump shape the rest of the analysis layer reads."""
    for path in paths:
        try:
            doc = json.loads(Path(path).read_text())
        except Exception:                              # noqa: BLE001
            continue
        # dumps come in two real shapes: {"deploy": [...]} from the Railway
        # tool and a bare list of rows from older captures. Reading only the
        # first silently produced an empty table on half the evidence.
        rows = doc.get("deploy") if isinstance(doc, Mapping) else doc
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            msg = row.get("message") or ""
            if not isinstance(msg, str) or "{" not in msg:
                continue
            try:
                obj = json.loads(msg[msg.index("{"):])
            except Exception:                          # noqa: BLE001
                continue
            if isinstance(obj, Mapping) and obj.get("kind"):
                yield row.get("timestamp") or "", obj


# --------------------------------------------------------------- links
def _candidate(start: Optional[Mapping[str, Any]]) -> Optional[dict]:
    if not start:
        return None
    return {"rank": start.get("rank"), "state": start.get("state"),
            "state_n": start.get("state_n"), "p_buy": start.get("p_buy"),
            "source": "session_start"}


def _quote(start: Optional[Mapping[str, Any]],
           decisions: list) -> Optional[dict]:
    """The action the model produced: from the entry decision if the
    session logged one, else the probe's own entry action."""
    if decisions:
        d = decisions[0]
        return {"relation": d.get("acted") or d.get("v2_recommended"),
                "recommended": d.get("v2_recommended"),
                "refusal": d.get("refusal"),
                "state": d.get("state"), "source": "session_report.decisions"}
    act = (start or {}).get("probe_entry_action")
    if not act:
        return None
    return {"relation": act.get("relation"), "recommended": act.get("relation"),
            "refusal": None, "side": act.get("side"),
            "target_price": act.get("target_price"),
            "source": "session_start.probe_entry_action"}


def _orders(ends: list) -> list:
    out: list = []
    for e in ends:
        for o in e.get("orders") or []:
            if isinstance(o, Mapping):
                out.append(o)
    return out


def _complement_action(orders: list) -> Optional[dict]:
    """The COMPLETE leg, by the stage name the executor writes."""
    for o in orders:
        stage = str(o.get("stage") or "")
        if "complete" in stage or "complement" in stage:
            return {"stage": stage, "side": o.get("side"),
                    "qty": o.get("qty"),
                    "filled_qty": o.get("filled_qty"),
                    "order_id": o.get("order_id"),
                    "source": "session_end.orders"}
    return None


def _inventory(ends: list) -> Optional[dict]:
    """The last inventory the sessions reported for this market."""
    for e in reversed(ends):
        inv = e.get("inventory") or e.get("resumed_inventory")
        if isinstance(inv, Mapping) and inv.get("up") is not None:
            return dict(inv)
    return None


def _effective_pair_cost(inv: Optional[Mapping[str, Any]],
                         breakdown: Optional[Mapping[str, Any]] = None
                         ) -> Optional[str]:
    """What the matched pair actually cost.

    THE AUTHORITATIVE FIGURE IS THE SETTLEMENT BREAKDOWN'S. `avg_up +
    avg_dn` is only a pair cost when the two sides were bought in equal
    quantity: on a position carrying a large residual the averages are
    over unequal quantities and their sum is a diagnostic, not a price
    anything was paid. The first version of this used the sum
    unconditionally and reported a pair cost of 1.110705 on a market with
    a 10.35-unit residual -- a pair bought above par, which never
    happened. So the breakdown wins wherever it exists, and the fallback
    says which one you are reading."""
    b = breakdown or {}
    # THE GUARD IS SOURCE-INDEPENDENT, and that took two passes to get
    # right. The settlement breakdown's own `matched_pair_cost` is built as
    # avg_up + avg_dn too, so refusing the ledger averages on a residual
    # position and then accepting the breakdown's identical number just
    # laundered the same artifact through a more authoritative-looking
    # field. Seventeen markets reported a pair cost above 1.00 that way.
    #
    # It is not a ceiling breach and no total is wrong: where residual is
    # zero, up == dn and the sum IS exactly the pair cost, which is why the
    # locked derivation is sound; and the settled decomposition defines
    # residual_contribution = venue_realized - matched_contribution, so the
    # venue's number always survives however the split lands. But on a
    # position with a residual the two averages are over unequal
    # quantities, so their sum is not a price anything was paid, and this
    # table will not print it as one.
    resid = _d((inv or {}).get("residual"))
    if resid is None or resid != 0:
        return None
    cost = _d(b.get("matched_pair_cost"))
    if cost is not None:
        return str(cost.quantize(D("0.000001")))
    if not inv:
        return None
    up, dn = _d(inv.get("avg_up")), _d(inv.get("avg_dn"))
    if up is None or dn is None:
        return None
    return str((up + dn).quantize(D("0.000001")))


def _incentive_for(slug: str, credited: Mapping[str, list]) -> Optional[dict]:
    """What the venue ACTUALLY credited on this market's fills. Absent --
    not zero -- when no incentive evidence has been collected at all:
    "we have not looked" and "the venue paid nothing" are different facts
    and this table refuses to merge them."""
    if not credited:
        return None
    rows = credited.get(slug)
    if rows is None:
        return {"credited_usd": None, "fills_with_a_credit": None,
                "note": "no incentive evidence for this market in the input",
                "source": "pmus_incentive_audit.credited_on_fills"}
    total = sum((_d(r.get("our_commission")) or D(0) for r in rows), D(0))
    # the venue's sign convention: negative commission = credited to us
    return {"credited_usd": str((-total).quantize(D("0.000001"))),
            "fills_with_a_credit": sum(1 for r in rows
                                       if r.get("credited_to_us")),
            "source": "pmus_incentive_audit.credited_on_fills"}


# ------------------------------------------------------------- episodes
def _stage(inv: Optional[Mapping[str, Any]], orders: list,
           settle: Optional[Mapping[str, Any]],
           quote: Optional[dict], candidate: Optional[dict]) -> str:
    filled = sum((_d(o.get("filled_qty")) or D(0) for o in orders), D(0))
    if settle:
        if settle.get("realized_confirmed") or settle.get("realized_usd") is not None:
            return "REALIZED"
        return "SETTLED"
    if filled > 0:
        resid = _d((inv or {}).get("residual"))
        if resid is not None and resid == 0:
            return "PAIRED"
        return "RESIDUAL_HELD" if resid is not None else "FILLED"
    if orders:
        return "NO_FILL"
    if quote:
        return "QUOTED"
    return "CANDIDATE" if candidate else "CANDIDATE"


def _pair_gross(inv: Optional[Mapping[str, Any]], stage: str,
                settle: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """The locked value of a zero-residual position, under the SAME guard
    l5_r5f_cohort applies: derived only when the venue's traded quantity
    reconciles against the ledger's, because a fill the ledger missed
    makes a directional position look flat."""
    if _ORDER[stage] < _ORDER["PAIRED"]:
        return None
    # the predicate that actually carries the meaning: locked means the
    # residual is EXACTLY zero. A settled episode sorts past PAIRED even
    # when it stranded a residual, so the stage alone must never authorise
    # this derivation.
    resid = _d((inv or {}).get("residual"))
    if resid is None or resid != 0:
        return None
    matched = _d((inv or {}).get("matched"))
    cost = _effective_pair_cost(inv, (settle or {}).get("breakdown"))
    if matched is None or cost is None or matched == 0:
        return None
    rec = cohort.qty_reconciliation(settle or {})
    if settle and rec.get("reconciles") is False:
        return {"value_usd": None, "derived": True,
                "refused": "venue and ledger quantities do not reconcile",
                "qty_reconciliation": rec}
    return {
        "value_usd": str((matched * (ONE - D(cost))).quantize(D("0.0001"))),
        "derived": True,
        "qty_reconciliation": rec if settle else {
            "reconciles": None,
            "note": "not settled yet: no venue quantity to reconcile against"},
        "labels": ("DERIVED, PRE-FEE, PRE-INCENTIVE. A zero-residual "
                   "position pays matched x (1.00 - pair cost) whichever "
                   "side wins, so it is locked before resolution -- but "
                   "locked is not realized and is never summed into one."),
    }


def build(paths: Iterable[str],
          credited: Optional[Mapping[str, list]] = None) -> dict:
    """One record per market episode across the dumps."""
    starts: dict = {}
    ends: dict = {}
    settles: dict = {}
    seen_order: list = []
    for ts, obj in iter_campaign_rows(paths):
        kind, slug = obj.get("kind"), obj.get("slug")
        if not slug:
            continue
        if slug not in starts and slug not in seen_order:
            seen_order.append(slug)
        if kind == "session_start":
            starts.setdefault(slug, (ts, obj))
        elif kind == "session_end":
            ends.setdefault(slug, []).append(obj)
        elif kind == "settlement" and obj.get("breakdown"):
            prev = settles.get(slug)
            if prev is None or ts > prev[0]:
                settles[slug] = (ts, obj)

    episodes: list = []
    for slug in seen_order:
        start = (starts.get(slug) or (None, None))[1]
        my_ends = ends.get(slug) or []
        settle = (settles.get(slug) or (None, None))[1]
        decisions = [d for e in my_ends for d in (e.get("decisions") or [])
                     if isinstance(d, Mapping)]
        orders = _orders(my_ends)
        inv = _inventory(my_ends)
        cand = _candidate(start)
        quote = _quote(start, decisions)
        if not (cand or quote or orders or settle):
            continue                      # nothing about this market to say
        stage = _stage(inv, orders, settle, quote, cand)
        b = (settle or {}).get("breakdown") or {}
        filled = [o for o in orders if (_d(o.get("filled_qty")) or D(0)) > 0]
        # A session that filled nothing still reports an inventory block, and
        # every figure in it is "0". Copying those onto a NO_FILL episode
        # would book a matched quantity of zero and a residual of zero as if
        # they had been MEASURED -- the exact fabricated zero this table
        # exists to refuse. The build-time assertion caught this; the fix is
        # to read the inventory only once the episode has actually filled.
        inv_reached = _ORDER[stage] >= _ORDER["FILLED"]
        inv_fields = inv if inv_reached else None
        row = {
            "slug": slug,
            "stage": stage,
            "status": (my_ends[-1].get("status") if my_ends else None),
            # 1-2
            "candidate": cand,
            "quote": quote,
            # 3-5
            "submitted": {"orders": len(orders),
                          "stages": [o.get("stage") for o in orders]}
                         if orders else None,
            "fills": {"filled_orders": len(filled),
                      "filled_qty": str(sum(
                          (_d(o.get("filled_qty")) or D(0) for o in orders),
                          D(0))),
                      "order_ids": [o.get("order_id") for o in filled]}
                     if orders else None,
            "complement_action": _complement_action(orders),
            # 6-9
            "matched_qty": (inv_fields or {}).get("matched"),
            "residual_units": (inv_fields or {}).get("residual"),
            "effective_pair_cost": _effective_pair_cost(inv_fields, b),
            "maker_taker": ({"maker_fills": b.get("maker_fills"),
                             "taker_fills": b.get("taker_fills")}
                            if b and inv_reached else None),
            # the one derived figure, guarded and labelled
            "pair_gross_usd": _pair_gross(inv, stage, settle),
            # 10-12
            "settlement": ({"settled_utc": (settles.get(slug) or ("",))[0],
                            "status": settle.get("status"),
                            "fees_usd": b.get("fees_usd")}
                           if settle else None),
            "venue_incentive_usd": _incentive_for(slug, credited or {}),
            "realized_net_usd": (str(_d(settle.get("realized_usd")))
                                 if settle and settle.get("realized_usd")
                                 is not None else None),
        }
        _assert_stage_discipline(row)
        episodes.append(row)

    return {"episodes": episodes, "counts": _counts(episodes),
            "labels": LABELS}


def _assert_stage_discipline(row: Mapping[str, Any]) -> None:
    """A field belonging to a stage this episode never reached must be
    absent, not zero. Enforced at build time, not only in tests: a table
    that quietly books 0.00 for an episode that never filled is worse than
    no table."""
    reached = _ORDER[row["stage"]]
    for field, need in STAGE_OF_FIELD.items():
        if reached < _ORDER[need] and row.get(field) is not None:
            raise AssertionError(
                f"{row['slug']}: {field} is populated at stage "
                f"{row['stage']}, which is before {need}")


def _counts(episodes: list) -> dict:
    by_stage: dict = {}
    for e in episodes:
        by_stage[e["stage"]] = by_stage.get(e["stage"], 0) + 1
    submitted = [e for e in episodes if e.get("submitted")]
    filled = [e for e in episodes
              if _ORDER[e["stage"]] >= _ORDER["FILLED"]]
    paired = [e for e in episodes if e["stage"] in
              ("PAIRED", "SETTLED", "REALIZED")
              and _d(e.get("residual_units")) == 0]
    return {
        "episodes": len(episodes),
        "by_stage": by_stage,
        # the conversions the settled-cohort builders structurally cannot
        # see, because an episode that never filled never settles
        "submitted_episodes": len(submitted),
        "filled_episodes": len(filled),
        "paired_to_zero_residual": len(paired),
        "fill_rate_of_submitted": (round(len(filled) / len(submitted), 4)
                                   if submitted else None),
        "pair_rate_of_filled": (round(len(paired) / len(filled), 4)
                                if filled else None),
    }


LABELS = {
    "stages": list(STAGES),
    "realized": ("realized_net_usd comes from the venue's settlement "
                 "figure and from nothing else. It is absent, not zero, "
                 "for every episode that has not settled."),
    "pair_gross": ("pair_gross_usd is DERIVED, pre-fee and pre-incentive, "
                   "and is refused when venue and ledger quantities do not "
                   "reconcile. Locked is not realized."),
    "incentive": ("venue_incentive_usd is what the venue actually credited "
                  "on our fills. Absent means no incentive evidence was "
                  "supplied, never that the venue paid nothing."),
    "absent_vs_zero": ("A field belonging to a stage the episode never "
                       "reached is null. Zero is a measurement; null is "
                       "the absence of one."),
}


# ------------------------------------------------------------- rendering
def render_markdown(res: Mapping[str, Any]) -> str:
    c = res["counts"]
    out = ["# L5 episode ledger", "",
           "candidate -> quote -> submitted -> fill/no-fill -> complement "
           "-> matched -> residual -> pair cost -> maker/taker -> "
           "settlement -> venue incentive -> realized net", "",
           f"- episodes: **{c['episodes']}**",
           f"- submitted: {c['submitted_episodes']}  |  "
           f"filled: {c['filled_episodes']}  |  "
           f"paired to zero residual: {c['paired_to_zero_residual']}",
           f"- fill rate of submitted: {c['fill_rate_of_submitted']}  |  "
           f"pair rate of filled: {c['pair_rate_of_filled']}",
           f"- by stage: {c['by_stage']}", "",
           "| market | stage | quote | fills | matched | resid | pair cost "
           "| derived pair $ | incentive $ | realized $ |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for e in res["episodes"]:
        q = (e.get("quote") or {}).get("relation") or "-"
        f = (e.get("fills") or {}).get("filled_qty") or "-"
        pg = (e.get("pair_gross_usd") or {}).get("value_usd")
        inc = (e.get("venue_incentive_usd") or {}).get("credited_usd")
        out.append(
            f"| `{e['slug']}` | {e['stage']} | {q} | {f} | "
            f"{e.get('matched_qty') or '-'} | "
            f"{e.get('residual_units') or '-'} | "
            f"{e.get('effective_pair_cost') or '-'} | "
            f"{pg if pg is not None else '—'} | "
            f"{inc if inc is not None else '—'} | "
            f"{e.get('realized_net_usd') or '—'} |")
    out += ["", "`—` means the episode has not reached that stage. It is "
            "not zero.", "",
            "Derived pair $ is pre-fee and pre-incentive and is never a "
            "realized figure."]
    return "\n".join(out) + "\n"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--logs", nargs="+", required=True,
                    help="Railway log dump files or globs")
    ap.add_argument("--credited", default=None,
                    help="optional JSON of credited_on_fills our_rows")
    ap.add_argument("--out", default=None,
                    help="directory for l5_episode_ledger.json / .md")
    args = ap.parse_args(argv)
    paths: list[str] = []
    for pat in args.logs:
        paths.extend(sorted(_glob.glob(pat)) or [pat])
    credited: dict = {}
    if args.credited:
        rows = json.loads(Path(args.credited).read_text())
        for r in (rows.get("our_rows") if isinstance(rows, Mapping) else rows):
            credited.setdefault(str(r.get("market") or ""), []).append(r)
    res = build(paths, credited=credited)
    md = render_markdown(res)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "l5_episode_ledger.json").write_text(
            json.dumps(res, indent=2, default=str) + "\n")
        (out / "L5_EPISODE_LEDGER.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":                             # pragma: no cover
    raise SystemExit(main())
