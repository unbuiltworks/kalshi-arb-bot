from decimal import Decimal as D

from src.l5_capital_allocator import (Allocator, VenueLimits, HEALTH_OUTAGE,
                                      default_allocator)


def _alloc():
    return Allocator(
        {"PMUS": VenueLimits("PMUS", D("0.15"), 3, D("-1.00"), D("0.05")),
         "GEMINI": VenueLimits("GEMINI", D("0.60"), 2, D("-1.00"), D("0.30"))},
        total_exposure_cap_usd=D("0.60"), total_daily_loss_stop_usd=D("-1.50"))


def test_reserve_once_per_market_and_release():
    a = _alloc()
    assert a.reserve("PMUS", "m1", "0.05").granted
    d = a.reserve("PMUS", "m1", "0.05")
    assert not d.granted and d.reason == "already_reserved"
    assert a.release("PMUS", "m1") and not a.release("PMUS", "m1")
    assert a.reserve("PMUS", "m1", "0.05").granted


def test_per_market_cap_concurrency_and_venue_exposure():
    a = _alloc()
    assert a.reserve("PMUS", "m1", "0.06").reason == "per_market_cap"
    for m in ("m1", "m2", "m3"):
        assert a.reserve("PMUS", m, "0.05").granted
    assert a.reserve("PMUS", "m4", "0.05").reason == "venue_concurrency"
    b = _alloc()
    assert b.reserve("GEMINI", "g1", "0.30").granted
    assert b.reserve("GEMINI", "g2", "0.30").granted
    assert b.reserve("GEMINI", "g3", "0.01").reason == "venue_concurrency"


def test_total_exposure_cap_binds_across_venues():
    a = _alloc()
    assert a.reserve("GEMINI", "g1", "0.30").granted
    assert a.reserve("GEMINI", "g2", "0.30").granted
    d = a.reserve("PMUS", "m1", "0.05")
    assert d.reason == "total_exposure_cap"


def test_one_venue_outage_does_not_freeze_the_other():
    a = _alloc()
    a.set_health("PMUS", HEALTH_OUTAGE, "orders/open 500")
    assert a.reserve("PMUS", "m1", "0.05").reason == "venue_health:OUTAGE"
    assert a.reserve("GEMINI", "g1", "0.30").granted


def test_daily_loss_stops_venue_then_total():
    a = _alloc()
    a.record_realized("PMUS", "-1.00")
    assert a.reserve("PMUS", "m1", "0.05").reason == "venue_daily_loss_stop"
    assert a.reserve("GEMINI", "g1", "0.30").granted
    a.record_realized("GEMINI", "-0.50")
    assert a.reserve("GEMINI", "g2", "0.30").reason == "total_daily_loss_stop"
    assert a.reserve("PMUS", "m2", "0.05").reason == "total_daily_loss_stop"


def test_default_allocator_keeps_gemini_disarmed_and_pmus_r1():
    a = default_allocator()
    assert a.reserve("GEMINI", "g1", "0.01").granted is False
    assert a.reserve("PMUS", "m1", "0.05").granted
    snap = a.snapshot()
    assert snap["venues"]["GEMINI"]["cap"] == "0"
    assert snap["total"]["cap"] == "0.15"
