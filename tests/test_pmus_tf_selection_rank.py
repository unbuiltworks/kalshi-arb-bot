"""Trading-first selection ranking (44123c91c, 3f941d850, corrected):
eligibility = independent fair source (unchanged); order by listing edge;
ranking never touches the fair-stability history; stale fair = unknown tier;
rewards do not outrank trading edge."""
import time
from decimal import Decimal as D

from src import pmus_sports_two_sided as M
from tests.test_pmus_enabled_mode_safety import en  # noqa: F401


class _G:
    max_age = 20.0

    def __init__(self, toks, mids):
        self.toks, self.mids = toks, mids
        outer = self

        class _I:
            def fresh(self, tok, max_age):
                m = outer.mids.get(tok)
                return None if m is None else {
                    "bid": m - 0.005, "ask": m + 0.005}
        self.intl = _I()

    def token(self, slug):
        return self.toks.get(slug)


def _sc(*slugs):
    return [(D("0.01"), D("0"), s) for s in slugs]


def _setup(e, monkeypatch, toks, mids, quotes, mode="only"):
    monkeypatch.setattr(M, "RN1_TRADING_FIRST", True)
    monkeypatch.setattr(M, "RN1_TF_SELECT", mode)
    monkeypatch.setattr(M, "RN1_DIR_SHARP", False)
    monkeypatch.setattr(e, "guard", _G(toks, mids))
    e._tf_listing_quotes = {k: (D(b), D(a)) for k, (b, a) in quotes.items()}


def test_order_positive_then_unknown_then_negative(en, monkeypatch):
    e, v, _ = en
    _setup(e, monkeypatch,
           {"big": "t1", "small": "t2", "unk": "t3", "neg": "t4"},
           {"t1": 0.60, "t2": 0.53, "t3": None, "t4": 0.50},
           {"big": ("0.50", "0.52"), "small": ("0.50", "0.52"),
            "unk": ("0.50", "0.52"), "neg": ("0.49", "0.51"),
            "nosrc": ("0.50", "0.52")})
    out = [r[2] for r in e.rn1_tf_select(_sc("neg", "nosrc", "unk",
                                             "small", "big"))]
    assert out == ["big", "small", "unk", "neg"]          # nosrc dropped
    monkeypatch.setattr(M, "RN1_TF_SELECT", "first")
    assert [r[2] for r in e.rn1_tf_select(_sc("neg", "nosrc", "unk",
                                              "small", "big"))][-1] == "nosrc"


def test_eligible_candidate_without_live_quote_is_kept(en, monkeypatch):
    """Regression for 3f941d850: candidates are not subscribed, so they have
    a token but no fresh quote; 'only' must not drop them."""
    e, v, _ = en
    _setup(e, monkeypatch, {"a": "t1", "b": "t2"}, {"t1": None, "t2": None},
           {"a": ("0.50", "0.52"), "b": ("0.50", "0.52")})
    assert [r[2] for r in e.rn1_tf_select(_sc("a", "b"))] == ["a", "b"]


def test_ranking_never_touches_the_stability_history(en, monkeypatch):
    e, v, _ = en
    _setup(e, monkeypatch, {"a": "t1"}, {"t1": 0.60},
           {"a": ("0.50", "0.52")})
    e._fair_hist = {"a": [(time.time() - 30, D("0.60"))]}
    snap = [tuple(x) for x in e._fair_hist["a"]]
    calls = []
    monkeypatch.setattr(e, "intl_fair_band",
                        lambda slug: calls.append(slug) or {})
    for _ in range(5):
        e.rn1_tf_select(_sc("a"))
    assert calls == []
    assert [tuple(x) for x in e._fair_hist["a"]] == snap
    # a missing fair during ranking must not clear it either
    e.guard.mids["t1"] = None
    e.rn1_tf_select(_sc("a"))
    assert [tuple(x) for x in e._fair_hist["a"]] == snap


def test_stale_sportsbook_fair_is_unknown_not_ranked(en, monkeypatch):
    e, v, _ = en
    _setup(e, monkeypatch, {"a": "t1"}, {"t1": None},
           {"a": ("0.50", "0.52")})
    monkeypatch.setattr(M, "RN1_DIR_SHARP", True)
    e.b.d["sharp_shadow"] = {"a": {"fair_yes": "0.70",
                                   "t": time.time() - 99999,
                                   "odds_age_s": 0}}
    assert e.rn1_tf_peek_fair("a") is None
    assert e.rn1_tf_listing_edge("a") is None
    e.b.d["sharp_shadow"]["a"]["t"] = time.time()
    assert e.rn1_tf_listing_edge("a") == D("0.70") - D("0.50") - D("0.02")


def test_min_edge_drops_only_known_negative(en, monkeypatch):
    e, v, _ = en
    _setup(e, monkeypatch, {"neg": "t1", "unk": "t2"},
           {"t1": 0.50, "t2": None},
           {"neg": ("0.49", "0.51"), "unk": ("0.50", "0.52")})
    # neg: both sides .50 - .49 - .02 = -.01
    assert [r[2] for r in e.rn1_tf_select(_sc("neg", "unk"))] == \
        ["unk", "neg"]
    monkeypatch.setattr(M, "RN1_TF_SELECT_MIN_EDGE", D("-0.02"))
    assert [r[2] for r in e.rn1_tf_select(_sc("neg", "unk"))] == \
        ["unk", "neg"]                        # -.01 >= -.02: kept, last
    monkeypatch.setattr(M, "RN1_TF_SELECT_MIN_EDGE", D("0"))
    assert [r[2] for r in e.rn1_tf_select(_sc("neg", "unk"))] == ["unk"]


def test_underdog_extra_edge_used_in_ranking(en, monkeypatch):
    e, v, _ = en
    _setup(e, monkeypatch, {"a": "t1"}, {"t1": 0.45},
           {"a": ("0.40", "0.60")})
    monkeypatch.setattr(M, "RN1_TF_DOG_EXTRA_USD", D("0.02"))
    # YES is the underdog: .45-.40-.02-.02 = .01 ; NO: .55-.40-.02 = .13
    assert e.rn1_tf_listing_edge("a") == D("0.13")


def test_error_in_edge_keeps_the_candidate_as_unknown(en, monkeypatch):
    e, v, _ = en
    _setup(e, monkeypatch, {"a": "t1"}, {"t1": 0.6}, {"a": ("0.5", "0.52")})
    monkeypatch.setattr(e, "rn1_tf_listing_edge", lambda s: 1 / 0)
    assert [r[2] for r in e.rn1_tf_select(_sc("a"))] == ["a"]
