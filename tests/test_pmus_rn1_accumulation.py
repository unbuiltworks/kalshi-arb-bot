"""RN1 accumulation at two-contract size, with filled inventory and resting
commitments counted together (owner 2026-11-22)."""
from decimal import Decimal as D

import pytest

from src import pmus_rn1_replica as R
from src import pmus_sports_two_sided as M
from tests.test_pmus_sports_two_sided import FakeVenue

T = D("0.01")


def q(pos, cost, bid="0.45", ask="0.47", last=None, now=1000.0, **c):
    return R.quotes(yes_bid=D(bid), yes_ask=D(ask), tick=T, pos=pos,
                    cost=cost, last_fill_t=last, now=now,
                    c={"qty": "2", **c})


def test_default_size_is_two_contracts():
    assert R.QTY == D("2")


def test_favourite_side_is_quotable_at_two_contracts():
    """At 5 contracts a 0.70 bid broke the $3 unpaired cap; at 2 it fits."""
    out = q({"YES": 0, "NO": 0}, {"YES": 0, "NO": 0}, bid="0.71", ask="0.72")
    assert out["YES"] and out["YES"]["qty"] == D("2")
    assert out["YES"]["px"] * 2 <= D("3")


def test_after_a_fill_it_extends_the_same_side_and_bids_completion():
    """Accumulate and complete at once: YES 2 @0.45 held, the YES side
    replenishes after its cool-down while NO rests as the completion."""
    pos, cost = {"YES": 2, "NO": 0}, {"YES": "0.90", "NO": "0"}
    out = q(pos, cost, last={"YES": 900.0}, now=1000.0)   # 100 s > 60 s
    assert out["YES"]["role"] == "extend" and out["YES"]["qty"] == D("2")
    assert out["NO"]["role"] == "complete"
    assert out["NO"]["px"] + D("0.45") <= D("0.97")


def test_extension_waits_for_the_refill_cooldown():
    pos, cost = {"YES": 2, "NO": 0}, {"YES": "0.90", "NO": "0"}
    out = q(pos, cost, last={"YES": 980.0}, now=1000.0)
    assert out["YES"] is None and out["why_YES"] == "refill_cooldown"
    assert out["NO"]["role"] == "complete"


def test_extension_stops_at_the_unpaired_cap():
    """6 YES @0.45 = $2.70 unpaired; another 2 would be $3.60 > $3."""
    pos, cost = {"YES": 6, "NO": 0}, {"YES": "2.70", "NO": "0"}
    out = q(pos, cost)
    assert out["YES"] is None and out["why_YES"] == "residual_cap"
    assert out["NO"]["role"] == "complete"


def test_both_resting_bids_count_against_the_market_cap_together():
    """4 YES @0.45 vs 2 NO @0.50 ($2.80 spent): the NO completion (2 @0.47)
    is always bid; a YES extension would add 0.88 plus a reserve to pair
    it, which no longer fits a $4.50 market cap."""
    pos, cost = {"YES": 4, "NO": 2}, {"YES": "1.80", "NO": "1.00"}
    out = q(pos, cost, per_market_usd="4.50")
    assert out["NO"] and out["NO"]["role"] == "complete"   # never refused
    assert out["YES"] is None and out["why_YES"] == "per_market_cap"


def test_committed_counts_the_reserve_of_a_resting_extension():
    """YES 2 filled @0.44 + YES extension 2 resting @0.44: if it fills,
    4 YES unpaired need 4 x (0.97 - 0.44) to complete -> $3.88."""
    c = R.committed({"YES": 2, "NO": 0}, {"YES": "0.88", "NO": "0"},
                    [("YES", D("0.44"), D("2"), False)], pcap=D("0.97"))
    assert c["spend"] == D("3.88")
    assert c["unmatched"] == D("1.76")


def test_committed_lighter_side_bid_is_not_double_counted():
    """YES 2 filled @0.44 + NO 2 resting @0.52: worst case is the NO bid
    NOT filling (reserve 2 x 0.53) or filling (paired) -- $1.94 either way."""
    c = R.committed({"YES": 2, "NO": 0}, {"YES": "0.88", "NO": "0"},
                    [("NO", D("0.52"), D("2"), True)], pcap=D("0.97"))
    assert c["spend"] == D("1.94")
    assert c["unmatched"] == D("0.88")


def test_committed_flat_market_with_two_opening_bids():
    c = R.committed({"YES": 0, "NO": 0}, {"YES": "0", "NO": "0"},
                    [("YES", D("0.44"), D("2"), False),
                     ("NO", D("0.52"), D("2"), False)], pcap=D("0.97"))
    # worst spend: NO alone fills -> 1.04 + 2 x 0.45 = 1.94 (= both: 1.92)
    assert c["spend"] == D("1.94")
    assert c["unmatched"] == D("1.04")


# ------------------------------------------------ engine: campaign caps
@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "ON", True)
    monkeypatch.setattr(R, "QTY", D("2"))
    monkeypatch.setattr(M, "TOTAL_CAP", D("50"))
    monkeypatch.setenv(M.ENABLED_ENV, "YES")
    v = FakeVenue()
    v.bid, v.ask = D("0.45"), D("0.47")
    e = M.Engine(v, M.Book(tmp_path / "s.json"))
    e._sleep = lambda s: None
    return e, v


def _hold(e, slug, yes, cost, stage="rn1_open"):
    mk = e.b.m(slug)
    mk["pos"]["YES"], mk["cost"]["YES"] = str(yes), str(cost)
    mk["orders"]["h-" + slug] = {"side": "YES", "px": "0.45",
                                 "qty": str(yes), "filled": str(yes),
                                 "open": False, "stage": stage,
                                 "placed_at": 2e9}
    return mk


def test_campaign_unmatched_cap_blocks_openings_not_completions(eng,
                                                                monkeypatch):
    e, v = eng
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("6"))
    _hold(e, "aec-a", 6, "2.70")                         # $2.70 unpaired
    _hold(e, "aec-b", 6, "2.70")                         # $5.40 total
    mk = _hold(e, "aec-c", 0, "0")
    mk["pos"]["YES"] = "0"
    e.step_market("aec-c")                               # flat: openings only
    assert not any(o["open"] for o in v.orders.values()
                   if o["slug"] == "aec-c")
    e.step_market("aec-a")                               # holder: completion
    sides = {o["side"] for o in v.orders.values()
             if o["open"] and o["slug"] == "aec-a"}
    assert sides == {"NO"}


def test_campaign_spend_cap_counts_completion_reserve(eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(R, "TOTAL_SPEND_USD", D("4"))
    # $2.70 spent + 6 x (0.97 - 0.45) = $3.12 reserve -> $5.82 committed
    _hold(e, "aec-a", 6, "2.70")
    e.b.m("aec-new")
    e.step_market("aec-new")
    assert not any(o["open"] for o in v.orders.values()
                   if o["slug"] == "aec-new")


def test_within_caps_both_sides_quote(eng):
    e, v = eng
    e.b.m("aec-x")
    e.step_market("aec-x")
    sides = {o["side"] for o in v.orders.values() if o["open"]}
    assert sides == {"YES", "NO"}
    assert all(o["qty"] == D("2") for o in v.orders.values())


# ------------- owner 2026-11-22: caps across >= 3 simultaneously tracked
def _venue_commitments(v, e):
    """Ground truth from the FAKE VENUE (not the book): filled cost +
    completion reserve + every resting bid, per market, summed."""
    tot_spend, tot_unm = D(0), D(0)
    for slug, mk in e.b.d["markets"].items():
        resting = [(o["side"], o["px"], o["qty"] - o["filled"],
                    oid in {k for k, x in mk["orders"].items()
                            if (x.get("stage") or "") == "two_sided_complete"})
                   for oid, o in v.orders.items()
                   if o["open"] and o["slug"] == slug]
        c = R.committed(mk["pos"], mk["cost"], resting)
        tot_spend += c["spend"]
        tot_unm += c["unmatched"]
    return tot_spend, tot_unm


@pytest.mark.parametrize("cap_spend,cap_unm", [
    ("16", "6"),      # the pilot's caps (uncapped this run reaches $7.68 unm)
    ("5", "6"),       # spend binds
    ("16", "3"),      # unmatched binds hard
])
def test_campaign_caps_hold_across_four_markets_resting_and_filled(
        eng, monkeypatch, cap_spend, cap_unm):
    e, v = eng
    cs, cu = D(cap_spend), D(cap_unm)
    monkeypatch.setattr(R, "TOTAL_SPEND_USD", cs)
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", cu)
    monkeypatch.setattr(M, "MAX_MARKETS", 4)
    slugs = ["aec-a", "aec-b", "aec-c", "aec-d"]
    for s in slugs:
        e.b.m(s)
    import itertools
    for rnd in range(6):
        # changing market order each round = market selection changing
        order = list(itertools.permutations(slugs))[rnd * 5 % 24]
        for s in order:
            e.step_market(s)
            spend, unm = _venue_commitments(v, e)
            assert spend <= cs, (rnd, s, spend)
            assert unm <= cu, (rnd, s, unm)
        # fill every resting bid in two of the markets, then continue
        for oid, o in v.orders.items():
            if o["open"] and o["slug"] in order[:2]:
                o.update(filled=o["qty"], open=False)
    spend, unm = _venue_commitments(v, e)
    assert spend <= cs and unm <= cu
    held = sum(D(e.b.m(s)["pos"]["YES"]) + D(e.b.m(s)["pos"]["NO"])
               for s in slugs)
    assert held > 0                     # it did trade, inside the caps


def test_new_exposure_in_at_most_max_markets(eng, monkeypatch):
    """Live (locked) 2026-11-22 01:59Z: three markets bid openings at once
    with MAX_MARKETS=2. Now a third market waits; completions never do."""
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    for s in ("aec-a", "aec-b", "aec-c"):
        e.b.m(s)
    e.step_market("aec-a")
    e.step_market("aec-b")
    e.step_market("aec-c")
    bidding = {o["slug"] for o in v.orders.values() if o["open"]}
    assert bidding == {"aec-a", "aec-b"}
    # a lone leg in the third market still gets its completion bid
    mk = e.b.m("aec-c")
    mk["pos"]["YES"], mk["cost"]["YES"] = "2", "0.90"
    e.step_market("aec-c")
    c = [o for o in v.orders.values() if o["open"] and o["slug"] == "aec-c"]
    assert c and all(o["side"] == "NO" for o in c)


# ---- carried inventory and quoting slots (owner 2026-11-25)
def _carry(e, slug, side="YES", cost="0.51"):
    mk = e.b.m(slug)
    mk["pos"][side], mk["cost"][side] = "1", cost
    return mk


def test_by_default_a_carried_leg_still_holds_a_quoting_slot(eng,
                                                             monkeypatch):
    """Unchanged since 2026-11-22 unless the owner switches it."""
    e, v = eng
    assert M.RN1_SLOTS_COUNT_INVENTORY is True
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    _carry(e, "aec-old")
    for s in ("aec-a", "aec-b"):
        e.b.m(s)
        e.step_market(s)
    assert {o["slug"] for o in v.orders.values() if o["open"]} == {"aec-a"}


def test_a_carried_leg_need_not_take_a_quoting_slot(eng, monkeypatch):
    """LIVE 2026-11-25: a carried unpaired leg held one of four slots for
    68 minutes. With the setting off, only a market with a resting opening
    or extension bid holds a slot."""
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", False)
    old = _carry(e, "aec-old")
    assert e._rn1_slot_holder(old) is False and e._rn1_exposed(old) is True
    for s in ("aec-a", "aec-b", "aec-c"):
        e.b.m(s)
        e.step_market(s)
    bidding = {o["slug"] for o in v.orders.values() if o["open"]}
    assert bidding == {"aec-a", "aec-b"}          # two slots, both used
    assert e.rn1_market_slots_full("aec-c", e.b.m("aec-c")) is True


def test_carried_inventory_is_still_counted_in_every_dollar_limit(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", False)
    _hold(e, "aec-old", 1, "0.51")                     # this campaign's leg
    assert e._rn1_slot_holder(e.b.m("aec-old")) is False
    assert D(e.b.total_unmatched()) == D("0.51")       # unpaired total
    assert e.own_exposure_usd() == D("0.51")           # at-risk and lane
    assert e.own_resting_worst() == D("0")             # no bid resting
    # the final gate's campaign unpaired ceiling counts it against a new
    # bid elsewhere (0.51 held + 0.47 asked > 0.60)
    monkeypatch.setattr(R, "TOTAL_UNMATCHED_USD", D("0.60"))
    e.since = None
    why = e.final_gate("aec-a", "YES", D("0.47"), D("1"), "rn1_open")
    assert why and why.startswith("campaign_unpaired")


def test_a_carried_leg_keeps_its_completion_bid_and_can_add_when_free(
        eng, monkeypatch):
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 1)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", False)
    _carry(e, "aec-old")
    e.b.m("aec-a")
    e.step_market("aec-a")                        # takes the one slot
    e.step_market("aec-old")
    mine = [o for o in v.orders.values() if o["open"]
            and o["slug"] == "aec-old"]
    assert mine and all(o["side"] == "NO" for o in mine)   # completion only
    assert e._rn1_slot_holder(e.b.m("aec-old")) is False


def test_tracked_markets_stay_bounded(eng, monkeypatch):
    """Passive inventory cannot grow the book without limit: MAX_TRACKED
    still stops new selections."""
    e, v = eng
    monkeypatch.setattr(M, "MAX_MARKETS", 2)
    monkeypatch.setattr(M, "MAX_TRACKED", 3)
    monkeypatch.setattr(M, "RN1_SLOTS_COUNT_INVENTORY", False)
    for s in ("aec-o1", "aec-o2", "aec-o3"):
        _carry(e, s)
    assert e.full() is True
