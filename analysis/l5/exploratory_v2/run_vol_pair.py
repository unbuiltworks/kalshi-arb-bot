"""EXPLORATORY_V2 supporting studies: run length, |Dp|, and the >$1 COMPLETE.

Three questions the owner posed separately, sharing one per-fill attribution.

ATTRIBUTION RULE, stated once and used everywhere here. A window ends with
`up` and `dn` shares; `matched = min(up, dn)` of each side pairs off and the
remainder is residual. Each fill is attributed PROPORTIONALLY within its own
side: a fill of `sz` on side S contributes `sz * matched/qty_S` matched shares
and the rest residual. The lighter side therefore attributes entirely to
matched, which is correct -- every one of its shares is in a pair.

This reconciles EXACTLY, and that is why proportional was chosen over any
first-in-first-out story about which shares "are" the residual:

    sum over fills of matched_shares        = 2 * matched     (two per pair)
    sum over fills of residual_shares       = |residual|
    sum of attributed P&L                   = window matched_pnl + residual_pnl

No ordering assumption is smuggled in. It is an accounting split of an outcome
that is already determined, not a claim about which share settled.

NOTHING HERE CHANGES AUTHORITATIVE CASH ACCOUNTING. Costs stay `usdcSize`,
settlement stays the redemption-row rule, and the totals stay the ones
`analysis/l5/btc15m/decompose.py` reports.
"""
from __future__ import annotations

import collections
import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.l5.residual_exit import forensics as F

MIN_N = 200


def _med(xs):
    v = sorted(x for x in xs if x is not None)
    return v[len(v) // 2] if v else None


def per_fill(cohort: str = "BTC15m") -> List[dict]:
    """One row per fill, carrying its run position, |Dp|, what happened next,
    and its attributed share of the window's realized economics."""
    windows, capture_end = F.load_windows(cohort)
    rows: List[dict] = []
    for cid, w in windows.items():
        fills = w["fills"]
        if not fills:
            continue
        _a, horizon, end = w["meta"]
        won_up, src = F.settlement(w, capture_end)
        up_q = sum(x[3] for x in fills if x[2] == "Up")
        dn_q = sum(x[3] for x in fills if x[2] != "Up")
        up_u = sum(x[4] for x in fills if x[2] == "Up")
        dn_u = sum(x[4] for x in fills if x[2] != "Up")
        if up_q <= 0 or dn_q <= 0 or won_up is None:
            continue            # single-sided or indeterminate: excluded, named
        avg_up, avg_dn = up_u / up_q, dn_u / dn_q
        pair_cost = avg_up + avg_dn
        matched = min(up_q, dn_q)
        mf = {"Up": matched / up_q, "Down": matched / dn_q}

        up = dn = 0.0
        run_side, run_len = None, 0
        p_hist: List[Tuple[int, float]] = []
        last_px = {"Up": None, "Down": None}
        for i, (t, px, oc, sz, usd) in enumerate(fills):
            p_up = px if oc == "Up" else 1.0 - px
            dpa = None
            for tt, pp in reversed(p_hist):
                if t - tt >= 60:
                    dpa = abs(p_up - pp)
                    break
            run_len = (run_len + 1) if run_side == oc else 1
            run_side = oc
            other = "Down" if oc == "Up" else "Up"

            # instantaneous pair implied by this fill and the last print on the
            # other side. NOT a locked pair -- see the >$1 study below.
            inst_pair = (px + last_px[other]) if last_px[other] is not None else None

            # what happened next, within 60s, on the other side
            opp_dt = opp_px = None
            for j in range(i + 1, len(fills)):
                if fills[j][0] - t > 60:
                    break
                if fills[j][2] != oc:
                    opp_dt, opp_px = fills[j][0] - t, fills[j][1]
                    break
            res_after_60 = None
            u2, d2 = up + (sz if oc == "Up" else 0.0), dn + (0.0 if oc == "Up" else sz)
            for j in range(i + 1, len(fills)):
                if fills[j][0] - t > 60:
                    break
                if fills[j][2] == "Up":
                    u2 += fills[j][3]
                else:
                    d2 += fills[j][3]
            res_after_60 = abs(u2 - d2)

            m_sh = sz * mf[oc]
            r_sh = sz - m_sh
            payout = 1.0 if ((oc == "Up") == won_up) else 0.0
            avg = avg_up if oc == "Up" else avg_dn
            rows.append({
                "cid": cid, "t": t, "i": i, "side": oc, "px": px, "sz": sz,
                "usd": usd, "run_len": run_len, "abs_dp_60s": dpa,
                "p_up": p_up, "tte_s": end - t,
                "residual_before": up - dn,
                "inst_pair": inst_pair,
                "opp_within_60s": opp_dt is not None,
                "opp_dt_s": opp_dt,
                "pair_with_next_opp": (px + opp_px) if opp_px is not None else None,
                "abs_residual_after_60s": res_after_60,
                "matched_shares": m_sh, "residual_shares": r_sh,
                "matched_pnl": (m_sh / 2.0) * (1.0 - pair_cost),
                "residual_pnl": r_sh * (payout - avg),
                "window_pair_cost": pair_cost,
            })
            if oc == "Up":
                up += sz
            else:
                dn += sz
            last_px[oc] = px
            p_hist.append((t, p_up))
    rows.sort(key=lambda r: r["t"])
    return rows


def _split(rows: Sequence[dict], frac: float = 0.7):
    c = int(len(rows) * frac)
    return rows[:c], rows[c:]


def _agg(v: Sequence[dict]) -> dict:
    sh = sum(x["sz"] for x in v)
    return {
        "n_fills": len(v), "shares": sh,
        "usd": sum(x["usd"] for x in v),
        "matched_share_frac": round(sum(x["matched_shares"] for x in v) / sh, 4) if sh else None,
        "matched_pnl": sum(x["matched_pnl"] for x in v),
        "residual_pnl": sum(x["residual_pnl"] for x in v),
        "total_pnl": sum(x["matched_pnl"] + x["residual_pnl"] for x in v),
        "pnl_per_share": round(sum(x["matched_pnl"] + x["residual_pnl"] for x in v) / sh, 6) if sh else None,
        "matched_pnl_per_share": round(sum(x["matched_pnl"] for x in v) / sh, 6) if sh else None,
        "residual_pnl_per_share": round(sum(x["residual_pnl"] for x in v) / sh, 6) if sh else None,
        "bp_per_dollar": (round(1e4 * sum(x["matched_pnl"] + x["residual_pnl"] for x in v)
                                / sum(x["usd"] for x in v), 1)
                          if sum(x["usd"] for x in v) else None),
    }


# ---------------------------------------------------------------------------
# 1. run length -- precursor to matched inventory, source of residual, or both?

def run_length_study(rows: Sequence[dict]) -> dict:
    def b(r):
        n = r["run_len"]
        return "1" if n == 1 else "2" if n == 2 else "3" if n == 3 else \
               "4-5" if n <= 5 else "6-8" if n <= 8 else "9-12" if n <= 12 else "13+"
    out: Dict[str, Any] = {"question": (
        "are long same-side runs (1) necessary precursors to profitable matched "
        "inventory, (2) primarily sources of negative residual, or (3) both "
        "with a cutoff? The per-run-position split answers it directly: a "
        "precursor shows matched P&L per share holding up as the run extends; "
        "a residual source shows residual P&L per share falling.")}
    tr, oos = _split(rows)
    for name, S in (("train", tr), ("oos", oos)):
        g = collections.defaultdict(list)
        for r in S:
            g[b(r)].append(r)
        out[name] = {k: _agg(v) for k, v in g.items() if len(v) >= MIN_N}
    # cumulative: what is lost by refusing every fill at run position >= n
    for name, S in (("train_tail", tr), ("oos_tail", oos)):
        acc = {}
        for n in (2, 3, 4, 6, 8, 12):
            v = [r for r in S if r["run_len"] >= n]
            if len(v) >= MIN_N:
                acc["run>=%d" % n] = _agg(v)
        out[name] = acc
    return out


# ---------------------------------------------------------------------------
# 2. |Dp| -- more matching opportunity, or only more residual risk?

def volatility_study(rows: Sequence[dict]) -> dict:
    def b(r):
        v = r["abs_dp_60s"]
        if v is None:
            return None
        return "<=0.01" if v <= 0.01 else "0.01-0.02" if v <= 0.02 else \
               "0.02-0.05" if v <= 0.05 else "0.05-0.10" if v <= 0.10 else ">0.10"
    out: Dict[str, Any] = {"question": (
        "does high |Dp| buy enough extra matching opportunity to justify L5's "
        "higher EXTEND rate there, or only more residual? MAGNITUDE ONLY -- "
        "sign(Dp) was falsified OOS and is not used.")}
    tr, oos = _split(rows)
    for name, S in (("train", tr), ("oos", oos)):
        g = collections.defaultdict(list)
        for r in S:
            k = b(r)
            if k:
                g[k].append(r)
        d = {}
        for k, v in g.items():
            if len(v) < MIN_N:
                continue
            a = _agg(v)
            a["p_opposite_within_60s"] = round(
                sum(1 for x in v if x["opp_within_60s"]) / len(v), 4)
            a["median_pair_with_next_opp"] = _med(
                [x["pair_with_next_opp"] for x in v])
            a["median_abs_residual_after_60s"] = _med(
                [x["abs_residual_after_60s"] for x in v])
            d[k] = a
        out[name] = d
    return out


# ---------------------------------------------------------------------------
# 3. the >$1 instantaneous COMPLETE, re-read under the oscillator model

def over_par_study(rows: Sequence[dict]) -> dict:
    """A fill whose INSTANTANEOUS pair exceeds 1.00 has been treated as locking
    in a losing pair. Under the oscillator model that assignment is wrong by
    construction: inventory keeps moving, and the pair a share ends up in is
    settled by the whole window, not by the one print before it.

    So the fill is scored against what its shares ACTUALLY earned -- the same
    proportional attribution used above -- rather than against the pair implied
    at that instant."""
    def b(r):
        v = r["inst_pair"]
        if v is None:
            return None
        return "<=0.96" if v <= 0.96 else "0.96-1.00" if v <= 1.00 else \
               "1.00-1.02" if v <= 1.02 else "1.02-1.05" if v <= 1.05 else ">1.05"
    out: Dict[str, Any] = {"question": (
        "is an over-par instantaneous pair a locked loss, or just a fill during "
        "inventory transition? Scored by realized attribution, not by the pair "
        "implied at the instant of the fill.")}
    tr, oos = _split(rows)
    for name, S in (("train", tr), ("oos", oos)):
        g = collections.defaultdict(list)
        for r in S:
            k = b(r)
            if k:
                g[k].append(r)
        d = {}
        for k, v in g.items():
            if len(v) < MIN_N:
                continue
            a = _agg(v)
            a["median_inst_pair"] = _med([x["inst_pair"] for x in v])
            a["median_window_pair_cost"] = _med([x["window_pair_cost"] for x in v])
            a["share_in_windows_that_ended_sub_par"] = round(
                sum(1 for x in v if x["window_pair_cost"] < 1.0) / len(v), 4)
            d[k] = a
        out[name] = d
    return out


# ---------------------------------------------------------------------------
# 4. incentives, kept strictly beside the trading economics

def incentive_context(rows: Sequence[dict]) -> dict:
    """Account-level incentive cashflow beside BTC15m trading economics.

    NEVER ATTRIBUTED. These credits are account-wide and cover every cohort L5
    traded; there is no per-market reward row for another account. They are
    placed next to the monthly trading numbers so the question "is the
    oscillator less attractive on trading economics alone when incentives are
    large?" can be answered, and for no other purpose."""
    import datetime as dt
    from analysis.l5.redemption import audit as A
    inc: Dict[str, float] = collections.defaultdict(float)
    kinds: Dict[str, Dict[str, float]] = collections.defaultdict(
        lambda: collections.defaultdict(float))
    for r in A.iter_activity():
        t = str(r.get("type") or "").upper()
        if t in A.INCOME_TYPES:
            m = dt.datetime.utcfromtimestamp(
                int(float(r.get("timestamp") or 0))).strftime("%Y-%m")
            usd = float(r.get("usdcSize") or 0)
            inc[m] += usd
            kinds[m][t] += usd
    trade: Dict[str, List[dict]] = collections.defaultdict(list)
    for r in rows:
        trade[dt.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m")].append(r)
    out = {"label": incentive_context.__doc__.strip(), "by_month": {}}
    for m in sorted(set(inc) | set(trade)):
        a = _agg(trade[m]) if trade.get(m) else {}
        out["by_month"][m] = {
            "btc15m_matched_pnl": a.get("matched_pnl"),
            "btc15m_residual_pnl": a.get("residual_pnl"),
            "btc15m_trading_pnl_after_fees": a.get("total_pnl"),
            "btc15m_spend_usd": a.get("usd"),
            "btc15m_bp_per_dollar": a.get("bp_per_dollar"),
            "ACCOUNT_LEVEL_incentive_usd": round(inc.get(m, 0.0), 2),
            "ACCOUNT_LEVEL_incentive_breakdown": {
                k: round(v, 2) for k, v in kinds.get(m, {}).items()},
        }
    return out


def report(cohort: str = "BTC15m") -> dict:
    rows = per_fill(cohort)
    return {
        "cohort": cohort, "n_fills_attributed": len(rows),
        "attribution": __doc__.split("ATTRIBUTION RULE", 1)[1].strip(),
        "run_length": run_length_study(rows),
        "volatility": volatility_study(rows),
        "over_par_complete": over_par_study(rows),
        "incentives": incentive_context(rows),
    }
