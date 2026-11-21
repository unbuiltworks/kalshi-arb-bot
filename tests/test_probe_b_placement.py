"""Probe B puts a placement rule on the LIVE path. The property that makes
that acceptable is that it can never cross or touch the ask, so that property
is tested from three directions: the rule, the live re-check, and the spec that
has to declare it before either runs.
"""
import os
import sys
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.gemini_execution import ab_arms as AB                 # noqa: E402
from src.gemini_execution import bundle                        # noqa: E402
from src.gemini_execution import continuous_probe as CP        # noqa: E402
from src.gemini_execution import live_probe_spec as S          # noqa: E402
from src.gemini_execution import ws_continuous as W            # noqa: E402

BOOK = {"yes": {"bid": "0.40", "ask": "0.46"},
        "no": {"bid": "0.54", "ask": "0.60"}}


# ---- the rule itself ------------------------------------------------------

def test_improvement_never_reaches_the_ask():
    """One tick below the ask is the last improvable price; at the ask, None."""
    assert AB.maker_only_improve(BOOK, "YES", "0.40") == D("0.41")
    assert AB.maker_only_improve(BOOK, "YES", "0.44") == D("0.45")
    assert AB.maker_only_improve(BOOK, "YES", "0.45") is None   # would be 0.46
    assert AB.maker_only_improve(BOOK, "YES", "0.46") is None
    assert AB.maker_only_improve(BOOK, "YES", "0.50") is None


def test_improvement_is_refused_when_the_ask_is_unreadable():
    assert AB.maker_only_improve({"yes": {}}, "YES", "0.40") is None
    assert AB.maker_only_improve({}, "YES", "0.40") is None


def test_apply_A_leaves_the_price_untouched_when_it_cannot_improve():
    out = AB.apply_A([{"leg": "YES", "price": "0.45", "qty": "1"}], BOOK)
    assert out[0]["price"] == "0.45"
    assert out[0]["improved"] is False
    assert "native_price" not in out[0]


def test_apply_A_never_changes_side_size_or_action():
    a = {"leg": "NO", "price": "0.54", "qty": "1", "action": "OPEN"}
    out = AB.apply_A([a], BOOK)[0]
    assert (out["leg"], out["qty"], out["action"]) == ("NO", "1", "OPEN")
    assert out["price"] == "0.55"          # improved, still under the 0.60 ask


def test_every_improvable_price_stays_strictly_below_the_ask():
    """Exhaustive over the tick grid, both legs."""
    for side, leg in (("yes", "YES"), ("no", "NO")):
        ask = D(BOOK[side]["ask"])
        p = D("0.01")
        while p < D("1.00"):
            imp = AB.maker_only_improve(BOOK, leg, str(p))
            assert imp is None or imp < ask, (leg, p, imp, ask)
            p += D("0.01")


# ---- the independent live re-check ---------------------------------------

def test_live_recheck_catches_a_price_at_the_ask():
    assert W._crossing([{"leg": "YES", "price": "0.46"}], BOOK) is not None


def test_live_recheck_catches_a_price_through_the_ask():
    assert W._crossing([{"leg": "NO", "price": "0.75"}], BOOK) is not None


def test_live_recheck_passes_a_maker_price():
    assert W._crossing([{"leg": "YES", "price": "0.45"},
                        {"leg": "NO", "price": "0.59"}], BOOK) is None


def test_live_recheck_refuses_rather_than_assumes_when_ask_is_unreadable():
    """'We could not tell' and 'it is safe' are different answers."""
    bad = W._crossing([{"leg": "YES", "price": "0.41"}], {"yes": {"ask": None}})
    assert bad is not None and bad["ask"] == "UNREADABLE"


def _code_only(text):
    """Source with comments and string literals stripped.

    The prose trap has now caught this programme six times: a docstring
    explaining that something is independent contains the very name the test
    greps for.
    """
    import io
    import tokenize
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return " ".join(out)


def test_recheck_does_not_call_the_rule_it_checks():
    """A defect in the improvement rule must not also disable its check."""
    import inspect
    import textwrap
    src = _code_only(textwrap.dedent(inspect.getsource(W._crossing)))
    assert "maker_only_improve" not in src
    assert "apply_A" not in src


def test_improve_book_maps_up_down_to_yes_no():
    st = {"outcomes": {"Up": {"bid": "0.40", "ask": "0.46"},
                       "Down": {"bid": "0.54", "ask": "0.60"}}}
    assert W._improve_book(st) == BOOK


# ---- it cannot run unless a declared spec asked for it --------------------

def test_placement_must_be_declared_not_defaulted():
    try:
        W.Driver("SENTINEL", "GEMI-BTC05M2609230030-UP",
                 ledger_path="/tmp/x.jsonl", placement="aggressive",
                 envelope=S.get("btc5m_q1_improve").envelope_obj)
    except CP.ProbeStop as exc:
        assert "not declared" in str(exc)
        return
    raise AssertionError("an undeclared placement was accepted")


def test_no_module_default_and_no_environment_variable():
    src = _code_only(open(os.path.join(ROOT, "src", "gemini_execution",
                                       "live_probe_spec.py")).read())
    assert "getenv" not in src and "environ" not in src
    assert S.BTC5M_Q1.placement is None          # the frozen BTC5m spec


def test_probe_b_is_probe_a_plus_exactly_one_field():
    a, b = S.get("btc5m_q1"), S.get("btc5m_q1_improve")
    fa, fb = dict(a.invocation_fingerprint()), dict(b.invocation_fingerprint())
    assert fb.pop("placement") == "maker_improve"
    assert "placement" not in fa
    fa.pop("spec_id"), fb.pop("spec_id")
    assert fa == fb, "probe B differs from probe A by more than placement"


def test_probe_b_has_its_own_hash():
    assert S.get("btc5m_q1_improve").spec_sha() != S.get("btc5m_q1").spec_sha()


def test_probe_a_btc15m_hashes_are_unchanged_by_all_of_this():
    """Probe A is frozen. Its reviewed identities must not have moved."""
    assert S.get("btc15m_q1_cap4_w60_880").spec_sha().startswith("223b81ed")
    assert S.get("btc15m_q1_cap4_w60_880_c2").spec_sha().startswith("ba64b54c")
    assert S.get("btc15m_q1_cap4").spec_sha().startswith("7fda21b1")


def test_the_placement_rule_is_inside_the_reviewed_bundle():
    """Membership is the point: the rule that decides whether an order is a
    maker order at all cannot be edited without invalidating the approval."""
    assert "ab_arms.py" in bundle.MEMBERS
    assert "fill_quality.py" in bundle.MEMBERS


def test_an_authorized_bundle_still_hashes_to_what_was_reviewed():
    """Whether or not an authorization is installed, the invariant is the
    same: if one IS installed it must equal the bundle actually running.

    This replaces an earlier assertion that AUTHORIZED_BUNDLE is None, which
    was only true before the owner's GO and would have failed the suite for
    the entirely expected reason that the probe was authorized.
    """
    if bundle.AUTHORIZED_BUNDLE is None:
        assert not bundle.authorized()
    else:
        assert bundle.AUTHORIZED_BUNDLE == bundle.bundle_sha()
        assert bundle.authorized()
        assert bundle.state()["why_not"] == []


def test_the_two_probes_use_different_envelopes_and_prefixes():
    a = S.get("btc15m_q1_cap4_w60_880_c2").envelope_obj
    b = S.get("btc5m_q1_improve").envelope_obj
    assert a.cohort_prefix == "GEMI-BTC15M" and b.cohort_prefix == "GEMI-BTC05M"
    assert a.max_concurrent_markets == b.max_concurrent_markets == 1
    assert (a.max_committed_usd, b.max_committed_usd) == (D("4.00"), D("2.50"))
