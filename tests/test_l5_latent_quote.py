"""Proof that the latent-quote shadow is read-only, and that its fill rules hold."""
from __future__ import annotations
import ast, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.l5_latent_quote import config as C, arms, lifecycle as L, models

PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "l5_latent_quote")
FORBIDDEN_NAMES = {"submit", "place_order", "create_order", "cancel_order",
                   "sign", "sign_order", "arm", "transport", "post", "delete", "put"}
FORBIDDEN_IMPORTS = {"requests", "websocket", "websockets", "web3", "eth_account"}
CRED_HINTS = {"API_KEY", "SECRET", "PRIVATE_KEY", "PASSPHRASE", "MNEMONIC", "TOKEN_SECRET"}


def _sources():
    return sorted(glob.glob(os.path.join(PKG, "*.py")))


def test_no_order_path_and_no_credentials():
    for p in _sources():
        src = open(p).read()
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr.lower() in FORBIDDEN_NAMES:
                raise AssertionError(f"{p}: forbidden attribute .{n.attr}")
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mods = ([x.name for x in n.names] if isinstance(n, ast.Import)
                        else [n.module or ""])
                for m in mods:
                    root = (m or "").split(".")[0]
                    assert root not in FORBIDDEN_IMPORTS, f"{p}: forbidden import {m}"
        for c in CRED_HINTS:
            assert f'environ["{c}' not in src and f"environ.get('{c}" not in src, \
                f"{p}: reads a credential-shaped env var"


def test_only_get_requests():
    """urlopen is called with a Request that never sets a method or a body."""
    src = open(os.path.join(PKG, "feed.py")).read()
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == "Request":
            for kw in n.keywords:
                assert kw.arg in ("headers",), f"Request got unexpected kwarg {kw.arg}"
            assert len(n.args) == 1, "Request must take only a URL positionally"


def test_no_recorder_or_gemini_or_production_paths():
    for p in _sources():
        src = open(p).read().lower()
        for bad in ("pmus_ws", "pmus-ws", "/state/", "gemini", "live_mandate",
                    "pmus_q1", "railway"):
            assert bad not in src, f"{p}: references {bad!r}"
        assert C.OUT_DIR.startswith("data/l5_latent_quote")


def test_arms_never_cross():
    s = {"up_bid": 0.30, "up_ask": 0.31, "dn_bid": 0.69, "dn_ask": 0.70,
         "l5_obs_dn_basis": 0.60, "l5_obs_up_basis": 0.30}
    bk = {"bids": [{"p": 0.30, "q": 7.0}], "asks": [{"p": 0.31, "q": 5.0}]}
    out = arms.build("up", s, bk)
    for arm, q in out.items():
        if q and q.get("status") == "PLACEABLE":
            assert q["price"] < s["up_ask"], f"{arm} would cross"
    assert out["P3_IMPROVE_BID"]["status"] == "REFUSED_CROSS", \
        "improving into a one-tick spread must be refused, not crossed"


def test_no_touch_equals_fill():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": 10.0,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [{"p": 0.30, "q": 5.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", q, {})
    o = L.observe(o, 101.0, [{"t": 100.5, "p": 0.30, "q": 4.0, "up": True, "buy": False}], bk)
    assert o["state"] == L.RESTING and o["filled_q"] == 0.0


def test_unknown_queue_never_fills():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": None,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [], "asks": []}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", q, {})
    o = L.observe(o, 101.0, [{"t": 100.5, "p": 0.30, "q": 999.0, "up": True, "buy": False}], bk)
    assert o["filled_q"] == 0.0 and o["evidence"] == L.UNKNOWN


def test_trade_through_is_defensible():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": 10.0,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [{"p": 0.29, "q": 1.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", q, {})
    o = L.observe(o, 101.0, [{"t": 100.5, "p": 0.28, "q": 6.0, "up": True, "buy": False}], bk)
    assert o["state"] == L.FULL and o["evidence"] == "TRADE_THROUGH"


def test_reprice_is_explicit():
    q = {"price": 0.30, "size": 5.0, "queue_ahead": 1.0,
         "bid_at_place": 0.30, "ask_at_place": 0.32}
    bk = {"bids": [{"p": 0.30, "q": 1.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R4_REPRICE_5S", "up", q, {})
    o, g = L.cancel(o, 105.0, "CONTROL_EVERY_DECISION", _st(), bk)
    assert o["state"] == L.REPRICED
    assert [e[1] for e in o["events"]] == [L.PLACED, L.REPRICED]
    assert g is not None, "a reprice must preserve a ghost"


def test_abstain_is_available_and_models_bounded():
    s = {"taker_up_share_60": None, "up_ask": None, "dn_ask": None,
         "l5_obs_up_basis": None, "l5_obs_dn_basis": None, "up_bid": None,
         "dn_bid": None, "l5_prev_up": None, "l5_run": None, "secs_to_settle": 400}
    p = models.predict_all(s)
    assert set(p) == set(C.SIDE_MODELS)
    for v in p.values():
        assert 0.0 <= v["p_up"] <= 1.0 and 0.0 <= v["confidence"] <= 1.0
        assert isinstance(v["abstain"], bool)


# ---------------------------------------------------------------- lifetime arms
def _q(px=0.30, qa=10.0, ask=0.32):
    return {"price": px, "size": 5.0, "queue_ahead": qa,
            "bid_at_place": px, "ask_at_place": ask}


def _st(**kw):
    base = {"up_bid": 0.30, "up_ask": 0.32, "dn_bid": 0.68, "dn_ask": 0.70,
            "l5_obs_pair_cost": 0.95, "l5_obs_dn_basis": 0.60,
            "l5_obs_up_basis": 0.30, "_pred_p_up": 0.6, "_pred_conf": 0.6}
    base.update(kw)
    return base


def test_r1_sticky_does_not_reprice_on_cadence():
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", _q(), _st())
    rep, why = L.should_reprice(o, 100.0 + 600, "up", _st(), True, True)
    assert rep is False and why is None, "R1 must rest; it must not reprice on cadence"


def test_r1_still_reprices_on_side_reversal_and_loss_of_passivity():
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", _q(), _st())
    assert L.should_reprice(o, 101.0, "dn", _st(), True, True)[1] == "SIDE_REVERSAL"
    assert L.should_reprice(o, 101.0, "up", _st(), False, True)[1] == "NO_LONGER_PASSIVE"


def test_r2_reprices_only_on_rule_violation():
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R2_MATERIAL_CHANGE", "up", _q(), _st())
    assert L.should_reprice(o, 100.0 + 999, "up", _st(), True, True)[0] is False
    assert L.should_reprice(o, 101.0, "up", _st(), True, False)[1] == \
        "PAIR_COST_RULE_VIOLATION"


def test_r3_fixed_rest_durations_are_the_l5_derived_ones():
    assert C.R3_DURATIONS == {"R3_FIXED_8": 8.0, "R3_FIXED_20": 20.0,
                              "R3_FIXED_120": 120.0}
    for arm, d in C.R3_DURATIONS.items():
        o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", arm, "up", _q(), _st())
        assert L.should_reprice(o, 100.0 + d - 0.1, "up", _st(), True, True)[0] is False
        assert L.should_reprice(o, 100.0 + d + 0.1, "up", _st(), True, True)[0] is True


def test_r4_control_reprices_every_decision():
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R4_REPRICE_5S", "up", _q(), _st())
    assert L.should_reprice(o, 100.1, "up", _st(), True, True)[0] is True


# ------------------------------------------------------------------- cancel/ghost
def test_cancel_record_has_every_required_field():
    bk = {"bids": [{"p": 0.30, "q": 3.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R4_REPRICE_5S", "up", _q(), _st())
    o, g = L.cancel(o, 140.0, "CONTROL_EVERY_DECISION", _st(up_bid=0.31), bk)
    c = o["cancel"]
    for k in ("reason", "cancel_t", "placed_t", "order_age_s",
              "queue_ahead_at_place", "observable_queue_before_cancel",
              "bid_at_place", "ask_at_place", "bid_at_cancel", "ask_at_cancel",
              "price_move_bid", "side_model_state", "pair_cost_at_place",
              "pair_cost_at_cancel"):
        assert k in c, f"cancellation record missing {k}"
    assert c["order_age_s"] == 40.0
    assert c["observable_queue_before_cancel"] == 3.0
    assert c["price_move_bid"] == 0.01


def test_ghost_is_preserved_observed_and_never_becomes_inventory():
    bk = {"bids": [{"p": 0.30, "q": 3.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R4_REPRICE_5S", "up", _q(), _st())
    o, g = L.cancel(o, 110.0, "CONTROL_EVERY_DECISION", _st(), bk)
    assert g is not None and g["is_ghost"] and g["parent_oid"] == o["oid"]
    # a later trade-through would have filled it
    L.observe(g, 120.0, [{"t": 115.0, "p": 0.28, "q": 50.0, "up": True, "buy": False}], bk)
    assert g["ghost_outcome"] == "WOULD_HAVE_FILLED:TRADE_THROUGH"
    assert g["state"] == "GHOST_OBSERVING", "a ghost must never enter a fill state"


# ----------------------------------------------------------------- queue/censor
def test_depletion_alone_is_never_a_fill():
    """Displayed size vanishing is not execution: an upstream cancel looks the same."""
    bk = {"bids": [], "asks": [{"p": 0.32, "q": 1.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", _q(), _st())
    L.observe(o, 101.0, [], bk)          # whole bid level gone, no prints at all
    assert o["filled_q"] == 0.0 and o["state"] == L.RESTING


def test_censored_queue_cannot_fill_afterwards():
    bk = {"bids": [{"p": 0.30, "q": 3.0}], "asks": [{"p": 0.32, "q": 9.0}]}
    o = L.place(100.0, "s", "S5_FULL", "P1_AT_BID", "R1_STICKY", "up", _q(), _st())
    L.censor(o, 105.0, "restart")
    assert o["queue_ahead_at_place"] is None and o["queue_censored"] is True
    L.observe(o, 110.0, [{"t": 106.0, "p": 0.28, "q": 99.0, "up": True, "buy": False}], bk)
    assert o["filled_q"] == 0.0, "a censored queue must never fill"


# ------------------------------------------------------------------ label gating
def test_label_receipt_time_cannot_leak_backwards():
    """A label whose recv_t is after tau must be excluded from features."""
    import src.l5_latent_quote.run as R
    raw = [{"timestamp": "100", "price": "0.3", "size": "5", "outcome": "Up",
            "side": "BUY", "slug": "btc-updown-15m-900", "type": "TRADE"}]
    late = R._norm_l5(raw, 900, recv_t=200.0)       # event at 100, received at 200
    tau = 150.0
    feat = [x for x in late if x["t"] < tau and x["recv_t"] <= tau]
    assert feat == [], "a label received after tau must not enter the state at tau"
    assert late[0]["t"] == 100.0 and late[0]["recv_t"] == 200.0, \
        "both timestamps must be retained so it can still SCORE the prediction"
