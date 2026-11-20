"""The owner-listed venue-superset correction.

It writes to a ledger that gates money, so the tests are about refusal: it
must correct exactly the listed markets at exactly the listed figures, and
nothing else, under an approval bound to one manifest.
"""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from src import l5_live_execution as LX
from src import l5_venue_cost_attestation as VCA
from src import l5_venue_superset_correction as VSC

from tests.test_l5_venue_cost_attestation import (
    Feed, _no_retry_spacing, deposit, fill, retire, run_until_idle,  # noqa: F401
)

ROOT = Path(__file__).resolve().parents[1]
MA = "aec-nba-det-sa-2026-11-17"            # ledger 5 UP, venue 10 + 10
MB = "aec-cs2-vp-priv-2026-11-16"           # ledger 1 DOWN, venue 3 + 3
MC = "aec-nba-hou-mia-2026-11-16"           # not listed
REBASE = "2026-11-16T00:58:25.162832+00:00"


def fills():
    return [
        fill(MA, "UP", "10", "0.46", "TA1", "VA1", t="2026-11-17T20:00:00Z"),
        fill(MA, "DOWN", "10", "0.48", "TA2", "VA2",
             t="2026-11-17T20:01:00Z"),
        fill(MB, "UP", "3", "0.50", "TB1", "VB1", t="2026-11-16T10:00:00Z"),
        fill(MB, "DOWN", "2", "0.48", "TB2", "VB2", t="2026-11-16T10:01:00Z"),
        fill(MB, "DOWN", "1", "0.49", "TB3", "VB3", t="2026-11-16T10:02:00Z"),
        fill(MC, "UP", "5", "0.50", "TC1", "VC1", t="2026-11-16T11:00:00Z"),
        fill(MC, "DOWN", "5", "0.53", "TC2", "VC2", t="2026-11-16T11:01:00Z"),
        deposit("2026-11-10T05:00:00Z"),
    ]


LISTED = {
    MA: {"venue_up": "10", "venue_down": "10", "cost_up": "4.60",
         "cost_down": "4.80", "ledger_up": "5", "ledger_down": "0",
         "yes_value": "0", "booked_pnl": "-5", "corrected_pnl": "0.60"},
    MB: {"venue_up": "3", "venue_down": "3", "cost_up": "1.50",
         "cost_down": "1.45", "ledger_up": "0", "ledger_down": "1",
         "yes_value": "0", "booked_pnl": "0", "corrected_pnl": "0.05"},
}


def manifest(markets=None, **over):
    m = {"ok": True, "id": "test-manifest", "sha256": "a" * 64,
         "markets": copy.deepcopy(markets if markets is not None else LISTED)}
    m.update(over)
    return m


@pytest.fixture
def store(tmp_path):
    """A real ledger in the production state: three rows at the $1 bound
    that the REAL attestation gave up on, settled after a re-anchor."""
    s = LX.InventoryStore(str(tmp_path / "inv.json"))
    for k in ("realized_unattributed",):
        s.state[k] = "0"
    s.state["realized_attributed"] = "0"
    s.state["realized_today"] = "0"
    s.state["accounting_rebase_log"] = [{"t": REBASE, "id": "r"}]
    retire(s, MA, up="5", yes="0", t="2026-11-18T03:00:00+00:00")
    retire(s, MB, dn="1", yes="0", t="2026-11-16T14:00:00+00:00")
    retire(s, MC, up="2", yes="0", t="2026-11-16T15:00:00+00:00")
    run_until_idle(s, Feed([fills()]))
    book = s.state[VCA.STATE_KEY]["markets"]
    assert {book[m]["status"] for m in (MA, MB, MC)} == {"UPPER_BOUND_KEPT"}
    assert s.realized_today() == D("-7")
    return s


def on(monkeypatch, mode, approval=None):
    monkeypatch.setenv(VSC.MODE_ENV, mode)
    if approval is None:
        monkeypatch.delenv(VSC.APPROVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(VSC.APPROVAL_ENV, approval)


def drive(store, feed, man=None, steps=80):
    job = VSC.Correction(man if man is not None else manifest())
    now, outs = 9_000.0, []
    for _ in range(steps):
        now += VCA.STEP_INTERVAL_S + 1
        out = job.step(store, feed, now)
        if not out.get("idle") and not out.get("walking"):
            outs.append(out)
    return outs


def snapshot(store, tmp_path):
    return (copy.deepcopy(store.state), (tmp_path / "inv.json").read_bytes())


# --- off, inspect, apply -----------------------------------------------------

def test_unset_it_reads_nothing_and_writes_nothing(store, tmp_path,
                                                   monkeypatch):
    monkeypatch.delenv(VSC.MODE_ENV, raising=False)
    monkeypatch.setenv(VSC.APPROVAL_ENV, "a" * 64)      # approval alone: no
    before = snapshot(store, tmp_path)
    feed = Feed([fills()])
    assert drive(store, feed) == []
    assert feed.reads == [] and snapshot(store, tmp_path) == before


def test_inspect_reports_what_apply_would_do_and_changes_nothing(
        store, tmp_path, monkeypatch):
    on(monkeypatch, "INSPECT")
    before = snapshot(store, tmp_path)
    out, = drive(store, Feed([fills()]))
    assert snapshot(store, tmp_path) == before
    assert out["mode"] == "INSPECT" and out["would_apply"] == 2
    assert out["ledger_written"] is False
    assert D(out["delta_usd"]) == D("5.65")
    assert D(out["realized_attributed_before"]) == D("-7")
    assert D(out["realized_attributed_after"]) == D("-1.35")
    assert store.realized_today() == D("-7")


def test_apply_without_approval_is_refused(store, tmp_path, monkeypatch):
    on(monkeypatch, "APPLY")
    before = snapshot(store, tmp_path)
    feed = Feed([fills()])
    out, = drive(store, feed)
    assert out == {"refused": "APPLY_WITHOUT_APPROVAL", "applied": 0}
    assert feed.reads == [] and snapshot(store, tmp_path) == before


@pytest.mark.parametrize("approval", ["b" * 64, "yes", "APPROVED", "a" * 63])
def test_an_approval_for_any_other_manifest_is_refused(
        store, tmp_path, monkeypatch, approval):
    on(monkeypatch, "APPLY", approval)
    before = snapshot(store, tmp_path)
    out, = drive(store, Feed([fills()]))
    assert out["refused"] == "APPROVAL_IS_FOR_A_DIFFERENT_MANIFEST"
    assert snapshot(store, tmp_path) == before


@pytest.mark.parametrize("word", ["apply", "YES", "1", "INSPECT_AND_APPLY"])
def test_an_unknown_mode_is_refused(store, tmp_path, monkeypatch, word):
    on(monkeypatch, word, "a" * 64)
    before = snapshot(store, tmp_path)
    out, = drive(store, Feed([fills()]))
    assert out["refused"] == "MODE_NOT_RECOGNISED"
    assert snapshot(store, tmp_path) == before


def test_apply_corrects_exactly_the_listed_markets(store, monkeypatch):
    on(monkeypatch, "APPLY", "a" * 64)
    out, = drive(store, Feed([fills()]))
    assert out["mode"] == "APPLY" and out["applied"] == 2
    rows = store.state["retired_markets"]
    assert D(rows[MA]["settlement_pnl"]) == D("0.60")
    assert D(rows[MB]["settlement_pnl"]) == D("0.05")
    assert rows[MA]["venue_qty_form"] == "VENUE_SUPERSET_OWNER_LISTED"
    assert D(rows[MA]["cost_superseded"]["settlement_pnl"]) == D("-5")
    # the unlisted market is exactly as it was: still at the bound
    assert D(rows[MC]["settlement_pnl"]) == D("-2")
    assert rows[MC]["pnl_provenance"] == VCA.UPPER_BOUND
    assert store.realized_today() == D("-1.35")
    assert D(store.state["realized_today"]) == D("-1.35")
    assert D(store.state["realized_unattributed"]) == 0
    ids = [r["id"] for r in store.state["cost_basis_corrections"]]
    assert ids == ["venue-superset|test-manifest|" + MB,
                   "venue-superset|test-manifest|" + MA]


def test_inspect_and_apply_agree_to_the_cent(store, monkeypatch):
    on(monkeypatch, "INSPECT")
    seen, = drive(store, Feed([fills()]))
    on(monkeypatch, "APPLY", "a" * 64)
    done, = drive(store, Feed([fills()]))
    assert seen["delta_usd"] == done["delta_usd"]
    assert seen["realized_attributed_after"] \
        == done["realized_attributed_after"]
    assert [r["market"] for r in seen["results"] if r["attested"]] \
        == [r["market"] for r in done["results"] if r["attested"]]


def test_a_second_pass_and_a_restart_apply_nothing_more(store, tmp_path,
                                                        monkeypatch):
    on(monkeypatch, "APPLY", "a" * 64)
    drive(store, Feed([fills()]))
    after = snapshot(store, tmp_path)
    again, = drive(store, Feed([fills()]))                 # a new process
    assert again["applied"] == 0
    assert {r["reason"] for r in again["results"]} \
        == {"CORRECTION_ALREADY_APPLIED"}
    reborn = LX.InventoryStore(str(tmp_path / "inv.json"))
    third, = drive(reborn, Feed([fills()]))
    assert third["applied"] == 0
    assert reborn.realized_today() == D("-1.35")
    assert snapshot(store, tmp_path)[1] == after[1]


# --- every figure in the listing binds ---------------------------------------

def _listed(market, **over):
    m = copy.deepcopy(LISTED)
    m[market].update(over)
    return m


@pytest.mark.parametrize("field, value", [
    ("venue_up", "9"), ("venue_down", "11"), ("cost_up", "4.61"),
    ("cost_down", "4.79"), ("ledger_up", "4"), ("ledger_down", "1"),
    ("yes_value", "1"), ("booked_pnl", "-4.99"), ("corrected_pnl", "0.61"),
])
def test_one_wrong_digit_in_the_listing_refuses_that_market(
        store, monkeypatch, field, value):
    on(monkeypatch, "APPLY", "a" * 64)
    out, = drive(store, Feed([fills()]),
                 manifest(_listed(MA, **{field: value})))
    by = {r["market"]: r for r in out["results"]}
    assert by[MA]["attested"] is False
    assert by[MA]["superset"].startswith("LISTED_%s_NE_FOUND" % field.upper())
    assert by[MB]["attested"] is True                 # the other still goes
    assert D(store.state["retired_markets"][MA]["settlement_pnl"]) == D("-5")


def test_a_fill_added_since_the_list_was_written_refuses(store, monkeypatch):
    on(monkeypatch, "APPLY", "a" * 64)
    extra = fills() + [fill(MA, "UP", "1", "0.40", "TA9", "VA9",
                            t="2026-11-17T20:05:00Z")]
    out, = drive(store, Feed([extra]))
    by = {r["market"]: r for r in out["results"]}
    assert by[MA]["attested"] is False
    assert by[MA]["superset"].startswith("LISTED_")


def test_a_fee_on_any_leg_refuses(store, monkeypatch):
    on(monkeypatch, "APPLY", "a" * 64)
    f = fills()
    f[0] = fill(MA, "UP", "10", "0.46", "TA1", "VA1",
                t="2026-11-17T20:00:00Z", fee="0.02")
    out, = drive(store, Feed([f]))
    by = {r["market"]: r for r in out["results"]}
    assert by[MA]["attested"] is False
    assert "FEE" in (by[MA].get("superset") or by[MA].get("reason"))


def test_a_row_settled_before_the_rebase_is_refused(store, monkeypatch):
    """Its loss is no longer in the total; adding its improvement would be
    a credit against nothing."""
    store.state["retired_markets"][MB]["t"] = "2026-11-15T20:00:00+00:00"
    on(monkeypatch, "APPLY", "a" * 64)
    out, = drive(store, Feed([fills()]))
    by = {r["market"]: r for r in out["results"]}
    assert by[MB]["superset"] == "SETTLED_BEFORE_THE_LAST_REBASE"
    assert D(store.state["retired_markets"][MB]["settlement_pnl"]) == 0


def test_a_side_the_ledger_already_had_a_cost_for_is_refused(store,
                                                             monkeypatch):
    row = store.state["retired_markets"][MA]
    row["unverified_cost_basis_keys"] = [MA + "|DOWN"]
    on(monkeypatch, "APPLY", "a" * 64)
    out, = drive(store, Feed([fills()]))
    by = {r["market"]: r for r in out["results"]}
    assert by[MA]["attested"] is False
    assert by[MA]["superset"] == "KNOWN_SIDE:UP"


def test_venue_holding_less_than_the_ledger_is_refused(store, monkeypatch):
    f = [x for x in fills()
         if (x.get("trade") or {}).get("id") not in ("TB2", "TB3")]
    on(monkeypatch, "APPLY", "a" * 64)
    out, = drive(store, Feed([f]))
    by = {r["market"]: r for r in out["results"]}
    assert by[MB]["attested"] is False
    assert by[MB]["superset"] == "LEDGER_NOT_CONTAINED_IN_VENUE"


def test_a_feed_not_read_to_its_end_applies_nothing(store, tmp_path,
                                                    monkeypatch):
    on(monkeypatch, "APPLY", "a" * 64)
    monkeypatch.setattr(VCA, "MAX_WALK_PAGES", 1)
    before = snapshot(store, tmp_path)
    out, = drive(store, Feed([fills(), [deposit("2026-11-01T00:00:00Z")]],
                             eof=False))
    assert out.get("applied") == 0 and "walk_insufficient" in out
    assert snapshot(store, tmp_path) == before


def test_a_trade_already_used_by_another_correction_is_refused(
        store, monkeypatch):
    store.state.setdefault("cost_basis_corrections", []).append(
        {"id": "earlier", "trade_ids": ["TA2"]})
    on(monkeypatch, "APPLY", "a" * 64)
    out, = drive(store, Feed([fills()]))
    by = {r["market"]: r for r in out["results"]}
    assert by[MA]["reason"] == "TRADE_ID_ALREADY_CONSUMED"


# --- the store verb itself ---------------------------------------------------

def test_without_a_listing_the_store_refuses_exactly_as_before(store):
    w = {"sufficient": True, "by": "END_OF_FEED"}
    legs = VCA.legs_from_rows(fills(), MA)["legs"]
    before = copy.deepcopy(store.state)
    out = store.attest_retired_market_cost(MA, legs=legs, walk=w,
                                           correction_id="x")
    assert out["attested"] is False
    assert out["reason"] == "VENUE_QTY_NE_LEDGER_QTY"
    assert out["superset"] == "NOT_OWNER_LISTED"
    assert store.state == before


@pytest.mark.parametrize("listing, why", [
    ({"venue_up": "10"}, "LISTING_FIELDS"),
    (dict(LISTED[MA], extra="1"), "LISTING_FIELDS"),
    (dict(LISTED[MA], venue_up="ten"), "LISTING_UNREADABLE"),
    ("yes", "LISTING_FIELDS"),
])
def test_a_malformed_listing_refuses(store, listing, why):
    w = {"sufficient": True, "by": "END_OF_FEED"}
    legs = VCA.legs_from_rows(fills(), MA)["legs"]
    out = store.attest_retired_market_cost(
        MA, legs=legs, walk=w, correction_id="x", expect_superset=listing)
    assert out["attested"] is False and out["superset"] == why


def test_a_time_floor_walk_is_not_enough_for_this_form(store):
    w = {"sufficient": True, "by": "TIME_FLOOR"}
    legs = VCA.legs_from_rows(fills(), MA)["legs"]
    out = store.attest_retired_market_cost(
        MA, legs=legs, walk=w, correction_id="x", expect_superset=LISTED[MA])
    assert out["superset"] == "FEED_NOT_READ_TO_ITS_END"


def test_a_listing_cannot_force_the_exact_forms_or_widen_them(tmp_path):
    """A row whose quantities MATCH is the existing GROSS form: the listing
    is not consulted, so it cannot be used to change that path."""
    s = LX.InventoryStore(str(tmp_path / "g.json"))
    for k in ("realized_attributed", "realized_unattributed",
              "realized_today"):
        s.state[k] = "0"
    retire(s, MC, up="5", dn="5", yes="0", t="2026-11-16T15:00:00+00:00")
    legs = VCA.legs_from_rows(fills(), MC)["legs"]
    out = s.attest_retired_market_cost(
        MC, legs=legs, walk={"sufficient": True, "by": "END_OF_FEED"},
        correction_id="x", expect_superset={"nonsense": True})
    assert out["attested"] is True
    assert s.state["retired_markets"][MC]["venue_qty_form"] == "GROSS"


# --- the shipped manifest and the wiring -------------------------------------

def test_the_shipped_manifest_is_the_forty_reviewed_markets():
    m = VSC.load_manifest()
    assert m["ok"] and len(m["markets"]) == 40
    raw = Path(VSC.MANIFEST_PATH).read_bytes()
    assert m["sha256"] == hashlib.sha256(raw).hexdigest()
    doc = json.loads(raw)
    booked = sum(D(e["booked_pnl"]) for e in doc["markets"].values())
    fixed = sum(D(e["corrected_pnl"]) for e in doc["markets"].values())
    assert booked == D("-107.20") and fixed == D("-9.8835")
    assert D(doc["delta_total"]) == fixed - booked == D("97.3165")
    for name in doc["excluded_unreadable"]:
        assert name not in doc["markets"]
    for e in doc["markets"].values():
        vu, vd = D(e["venue_up"]), D(e["venue_down"])
        assert D(e["ledger_up"]) <= vu and D(e["ledger_down"]) <= vd
        assert D(e["yes_value"]) in (D(0), D(1))
        redeem = vu * D(e["yes_value"]) + vd * (1 - D(e["yes_value"]))
        assert redeem - D(e["cost_up"]) - D(e["cost_down"]) \
            == D(e["corrected_pnl"])


def test_a_changed_manifest_has_a_different_hash(tmp_path):
    doc = json.loads(Path(VSC.MANIFEST_PATH).read_text())
    first = next(iter(doc["markets"]))
    doc["markets"][first]["corrected_pnl"] = "99"
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    assert VSC.load_manifest(str(p))["sha256"] \
        != VSC.load_manifest()["sha256"]


@pytest.mark.parametrize("mutate, why", [
    (lambda d: d.update(markets={}), "NO_MARKETS"),
    (lambda d: d.update(count=39), "COUNT_NE_MARKETS"),
    (lambda d: d["markets"].update(x={"venue_up": "1"}), "ENTRY_FIELDS"),
])
def test_a_malformed_manifest_is_not_loaded(tmp_path, mutate, why):
    doc = json.loads(Path(VSC.MANIFEST_PATH).read_text())
    mutate(doc)
    p = tmp_path / "m.json"
    p.write_text(json.dumps(doc))
    got = VSC.load_manifest(str(p))
    assert got["ok"] is False and got["why"] == why


def test_the_runner_hook_is_off_by_default_and_swallowed():
    src = (ROOT / "src" / "l5_pmus_live_runner.py").read_text()
    assert src.count("self.maybe_correct_venue_supersets(now)") == 1
    i = src.index("    def maybe_correct_venue_supersets")
    body = src[i:src.index("    def maybe_diagnose_venue_costs")]
    assert "if not (os.environ.get(VSC.MODE_ENV) or \"\").strip():\n" \
           "                return" in body
    assert "except BaseException" in body
    for word in ("self.limits", "validation_gates", "max_cumulative",
                 "self.halt"):
        assert word not in body, word


def test_nothing_else_calls_the_superset_form():
    hits = []
    for p in (ROOT / "src").glob("*.py"):
        if "expect_superset=" in p.read_text():
            hits.append(p.name)
    assert sorted(hits) == ["l5_venue_superset_correction.py"]


def test_the_live_image_ships_the_module_and_the_manifest():
    d = (ROOT / "deploy" / "l5_pmus_live" / "Dockerfile").read_text()
    for f in ("src/l5_venue_superset_correction.py",
              "config/venue_superset_manifest_20261120.json"):
        assert "COPY %s %s" % (f, f) in d
    assert Path(VSC.MANIFEST_PATH) \
        == ROOT / "config" / "venue_superset_manifest_20261120.json"
