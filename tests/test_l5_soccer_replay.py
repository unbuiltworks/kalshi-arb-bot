"""Soccer replay harness (scripts/pmus_soccer_replay.py): the production
three-way gate on recorded tapes, the per-market tape statistics, the
match-level portfolio, and the tape loader. Offline; every figure is
stated with its arithmetic."""
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

from src import pmus_directional_backtest as B

_P = Path(__file__).resolve().parents[1] / "scripts" / "pmus_soccer_replay.py"
_S = importlib.util.spec_from_file_location("soccer_replay_t", _P)
R = importlib.util.module_from_spec(_S)
_S.loader.exec_module(R)

M = "atc-lal-bet-ray-2026-11-28-"
H, DR, A = M + "bet", M + "draw", M + "ray"
OPEN = "MARKET_STATE_OPEN"


def fr(t, ub, ua, ib, ia, ti=None):
    return {"t": float(t), "yes_bid": D(str(ub)), "yes_ask": D(str(ua)),
            "bid_q": 100, "ask_q": 100, "state": OPEN,
            "ib": None if ib is None else D(str(ib)),
            "ia": None if ia is None else D(str(ia)),
            "ti": float(t) if ti is None else ti}


def tape(n=8, step=3.0, bet=(0.50, 0.52), draw=(0.24, 0.26),
         ray=(0.23, 0.25)):
    """Three legs every `step` s; mids 0.51 + 0.25 + 0.24 = 1.00."""
    out = {H: [], DR: [], A: []}
    for i in range(n):
        for s, (b, a) in ((H, bet), (DR, draw), (A, ray)):
            out[s].append(fr(i * step, b, a, b, a))
    return out


# ------------------------------------------------------------ the loader
def test_loader_skips_a_truncated_last_line(tmp_path):
    rows = [{"slug": "lal-bet-ray-2026-11-28-bet", "ti": 1, "tu": 1,
             "ib": 0.5, "ia": 0.52, "ub": 0.5, "ua": 0.52, "us": OPEN}]
    f = tmp_path / "x.jsonl"
    f.write_text(json.dumps({"games": []}) + "\n" + json.dumps(rows[0]) +
                 "\n" + '{"slug": "lal-bet-ray-2026-11-28-bet", "ti": 4, "t')
    got = B.load_cross_venue([str(f)])           # tape still being written
    assert list(got) == ["aec-lal-bet-ray-2026-11-28-bet"]
    assert len(got["aec-lal-bet-ray-2026-11-28-bet"]) == 1
    assert set(R.load([str(f)])) == {H}
    assert len(R.raw_rows([str(f)])[H]) == 1
    f.write_text(json.dumps({"games": [
        {"slug": "lal-bet-ray-2026-11-28-bet",
         "start": "2026-11-28 13:00:00+00"}, {"slug": "x"}]}) + "\n{")
    # 2026-11-28T13:00:00Z = 1795870800
    assert R.starts([str(f)]) == {H: 1795870800.0}


# ------------------------------------------------------- three-way gate
def test_gate_grants_fair_only_once_settled():
    g, st = R.three_way_gate(tape())
    # stable_s = 10 s at 3 s cadence: frames at 0,3,6,9 are unsettled
    # (history shorter than 10 s), 12 s onward has a fair
    why = [x.get("gate") for x in g[H]]
    assert why[:4] == ["leg_missing"] + ["unsettled"] * 3  # t=0: siblings
    assert why[4:] == [None] * 4                           # not yet seen
    assert g[H][5]["ib"] == D("0.5") and g[H][0]["ib"] is None
    assert st["lal-bet-ray-2026-11-28"]["frames"] == 24


def test_gate_refuses_all_three_legs_on_a_sum_break():
    tp = tape(n=10)
    # goal moment at t=18 (i=6): the favourite's book is pulled wide
    # 0.10/0.99 -> mid 0.545... use 0.30/0.99 = 0.645; sum = 0.645 + 0.25 +
    # 0.24 = 1.135 > 1.04
    tp[H][6] = fr(18, 0.50, 0.52, 0.30, 0.99)
    g, st = R.three_way_gate(tp)
    for s in (H, DR, A):
        assert g[s][6]["gate"] == "sum_inconsistent", s
        assert g[s][6]["ib"] is None and g[s][6]["ia"] is None
        assert g[s][5].get("gate") is None           # the frame before
    # the leg whose mid jumped is then unsettled for 10 s; the siblings,
    # whose own mids never moved, have a fair again at once
    assert g[H][7]["gate"] == "unsettled"
    assert g[DR][7].get("gate") is None
    assert st["lal-bet-ray-2026-11-28"]["sum_inconsistent"] == 3


def test_gate_missing_or_stale_leg_and_missing_sibling():
    tp = tape(n=8)
    tp[DR][5] = fr(15, 0.24, 0.26, None, 0.26)        # empty bid side
    g, _ = R.three_way_gate(tp)
    assert g[DR][5]["gate"] == "leg_missing"
    assert g[A][5]["gate"] == "leg_missing"           # sibling: no fair
    # at t=18 the first leg is read before the draw's new sample (same
    # pass): the draw's latest reading is still the empty one
    assert g[H][6]["gate"] == "leg_missing"
    assert g[H][7].get("gate") is None                # draw is back
    # a sibling last sampled 30 s ago (> intl_max_age_s 20) is not fresh
    tp = tape(n=8)
    tp[DR] = tp[DR][:5]                               # draw stops at t=12
    tp[H].append(fr(45, 0.50, 0.52, 0.50, 0.52))
    g, _ = R.three_way_gate(tp)
    assert g[H][-1]["gate"] == "leg_missing"
    # only two of the three outcome markets recorded: never a fair
    tp = tape()
    del tp[A]
    g, st = R.three_way_gate(tp)
    assert {x["gate"] for x in g[H] + g[DR]} == {"siblings_missing"}
    assert st["lal-bet-ray-2026-11-28"]["fair_ok_share"] == 0.0


def test_gate_audit_counts_far_frames_left_with_a_fair():
    tp = tape(n=8)
    # US book 0.30/0.32 (mid 0.31) against intl 0.50/0.52 (mid 0.51): 20c
    # apart, sum still 1.00 and settled -> the gate keeps the fair
    tp[H][6] = fr(18, 0.30, 0.32, 0.50, 0.52)
    g, _ = R.three_way_gate(tp)
    a = R.gate_audit(tp, g)["lal-bet-ray-2026-11-28"]
    assert a["far"] == 1 and a["far_with_fair"] == 1
    assert a["far_with_fair_us_tight"] == 1


# ----------------------------------------------------------- tape stats
def raw(t, ub, ua, ib, ia, ush=None):
    return {"slug": "lal-bet-ray-2026-11-28-bet", "ti": t, "tu": t,
            "ub": ub, "ua": ua, "ib": ib, "ia": ia, "us": OPEN,
            "ubq": 50, "uaq": 70, "ush": ush}


def test_tape_stats_band_edge_and_fillable_moves():
    rows = [raw(0, 0.50, 0.51, 0.50, 0.51, "100"),
            raw(3, 0.50, 0.51, 0.52, 0.53),
            # ask falls to 0.49 = the bid-1tick quoted two frames earlier
            # (0.50 - 0.01): fillable at touch, not through
            raw(6, 0.48, 0.49, 0.48, 0.49),
            # ask 0.47 < 0.49 quoted at t=3: touch AND through
            raw(9, 0.46, 0.47, 0.46, 0.47, "130.5"),
            # ask 0.16 < 0.47 quoted at t=6: touch AND through; its own
            # YES bid-1tick is 0.14, below the price band
            raw(12, 0.15, 0.16, 0.15, 0.16),
            raw(15, None, None, 0.15, 0.16)]     # empty US book
    st = R.tape_stats(rows, start_ts=6)
    assert st["frames"] == 6 and st["two_sided_us"] == 5
    assert st["band_yes"] == 0.8                 # 4 of 5 (0.14 < 0.20)
    assert st["band_no"] == 1.0                  # NO px 0.48..0.83
    # edge 2c YES: mid - (bid - 0.01) >= 0.02 only at t=3 (0.525 - 0.49)
    assert st["edge2c_yes"] == 0.2
    # NO at t=3: (1 - 0.525) - (1 - 0.51 - 0.01) = -0.005; others 0.015
    assert st["edge2c_no"] == 0.0
    assert st["fillable_yes_touch"] == 3 and st["fillable_yes_through"] == 2
    assert st["fillable_no_touch"] == 0
    assert st["us_spread_med"] == 0.01 and st["gap_med"] == 0.0
    assert st["us_shares_traded_in_tape"] == 30.5
    assert st["inplay_share"] == round(4 / 6, 3)
    assert st["lead"] == {"intl_first": 1, "us_first": 0, "same_frame": 0}
    assert R.tape_stats([raw(0, None, None, None, None)])["two_sided_us"] == 0


# ----------------------------------------------------- match portfolio
def test_match_portfolio_dutch_and_result():
    def row(slug, fills, pnl):
        return {"slug": slug, "fill_list": fills, "trading_pnl": pnl}
    f = lambda t, side, px, q: {"t": t, "side": side, "px": D(px),  # noqa
                                "qty": D(q)}
    rows = [row(H, [f(1, "YES", "0.50", "2")], "1.00"),      # bet wins
            row(DR, [f(2, "YES", "0.24", "2")], "-0.48"),
            row(A, [f(3, "YES", "0.22", "2")], "-0.44")]
    meta = {H: {"outcome_yes": 1}, DR: {"outcome_yes": 0},
            A: {"outcome_yes": 0}}
    p = R.match_portfolio(rows, meta)["lal-bet-ray-2026-11-28"]
    # cost 1.00 + 0.48 + 0.44 = 1.92; any result pays 2 -> +0.08 locked
    assert p["by_result"] == {"bet": "0.08", "draw": "0.08", "ray": "0.08"}
    assert p["dutch_sets"] == "2" and D(p["dutch_locked_pnl"]) == D("0.08")
    assert p["result"] == "bet" and p["pnl_at_result"] == "0.08"
    assert D(p["sum_of_markets"]) == D("0.08")
    # after two fills (bet 2 @ 0.50, draw 2 @ 0.24) a ray win loses 1.48
    assert p["peak_loss"] == "1.48" and p["worst"] == "0.08"
    assert R.match_portfolio([row(H, [], "0")], meta) == {}


def test_alignment_never_flips_a_soccer_outcome():
    tp = tape(n=12)
    al = R._alignment(tp)
    assert al[H] == {"flip": False, "generic_test": True}
    # books 30c apart on average (post-match garbage): dropped, not flipped
    tp[H] = [fr(i * 3, 0.10, 0.90, 0.80, 0.82) for i in range(12)]
    assert R._alignment(tp)[H]["flip"] is None
    # mirrored book (US 0.20, intl 0.80): the generic test says "flip";
    # a soccer outcome is the same instrument, so it is never flipped
    tp[H] = [fr(i * 3, 0.19, 0.21, 0.79, 0.81) for i in range(12)]
    assert R._alignment(tp)[H] == {"flip": False, "generic_test": False}


# -------------------------------------------- the harness CAN trade
def test_replay_fills_when_the_ask_trades_through_a_resting_bid():
    """US 0.50/0.51 with the international fair at 0.53: the YES bid rests
    at 0.49 (one tick behind the touch; edge 0.53 - 0.49 = 0.04 >= 0.02).
    When the US ask drops to 0.48 the bid fills under both fill models."""
    tp = {H: [], DR: [], A: []}
    for i in range(12):
        t = i * 3.0
        ub, ua = (0.47, 0.48) if i == 10 else (0.50, 0.51)
        tp[H].append(fr(t, ub, ua, 0.52, 0.54))
        tp[DR].append(fr(t, 0.23, 0.24, 0.23, 0.24))
        tp[A].append(fr(t, 0.22, 0.24, 0.225, 0.235))   # sum 0.53+.235+.23
    meta = {s: {"tick": "0.01", "outcome_yes": int(s == H), "flip": False}
            for s in tp}
    g, _ = R.three_way_gate(tp)
    for fill in R.FILLS:
        for name, pol in R.POLICIES:
            src = g if pol.get("require_fair") else tp
            r = B.run(src, meta, dict(pol, directional=False),
                      {"pauses": False, "fill": fill, "keep_fills": True})
            row = [x for x in r["rows"] if x["slug"] == H][0]
            assert row["fills"] >= 1, (fill, name, row["decisions"])
            assert row["fill_list"][0]["px"] == D("0.49")
            port = R.match_portfolio(r["rows"], meta)
            assert port["lal-bet-ray-2026-11-28"]["result"] == "bet"
