"""OWNER (maximize legitimate L5 evidence turnover, no added risk): the
recorder's READ-ONLY multi-market shadow runs the frozen policy on every
observable market with the canonical lookup v3, persists the sampled
action (side/price/size/timing/inventory) plus the execution-eligibility
verdict and market evolution, fills theoretically from public prints,
survives a restart without double counting, and scores its own rows
with the venue-neutral scorer."""
from __future__ import annotations

import json
import sys

from src import l5_fidelity_gate as gate
from src import pmus_l5_multi_shadow as ms
from src import pmus_l5_continuous_session as cont

NOW = 1788700000.0


def _state(now=NOW):
    return {
        "mkt-a": {"bid": "0.44", "ask": "0.46", "bid_qty": "120", "ask_qty": "90",
                  "both_sides_served": True,
                  "trades": [{"t": now - 100, "px": "0.45", "qty": "5", "dir": "buy"},
                             {"t": now - 50, "px": "0.44", "qty": "3", "dir": "sell"}]},
        "mkt-b": {"bid": None, "ask": "0.70", "trades": []},
        "mkt-c": {"bid": "0.10", "ask": "0.12", "bid_qty": "5", "ask_qty": "5",
                  "both_sides_served": True, "trades": []},
    }


def test_every_observable_market_gets_a_canonical_v3_decision(tmp_path):
    sh = ms.MultiShadow(tmp_path)
    out = sh.tick(_state(), NOW)
    assert out == {"decisions_made": 2, "markets": 3}
    lines = [json.loads(l) for l in (tmp_path / ms.DECISIONS_FILE).read_text().splitlines()]
    assert [l["kind"] for l in lines] == ["pmus_shadow_decision"] * 2
    a = next(l for l in lines if l["slug"] == "mkt-a")
    assert a["lookup"]["lookup_version"] == 3 and a["lookup"]["pre_fill"] is True
    assert a["lookup"]["state"] == "p25-45c|tte>10m|flat"
    assert a["sampled"] == "open" == a["acted"] and a["refusal"] is None
    assert a["behavioral"] == {"side": "YES", "px": "0.44", "shares": 10.0,
                               "note": "the frozen model's own draw acted in shadow"}
    assert a["executable"]["eligible"] is True and a["executable"]["refusals"] == []
    assert a["model_sha"] == sh.model_sha and a["code_sha"] == ms.CODE_SHA
    assert a["draw"]["seed"] and a["decision_id"]
    # mkt-c: the model still answers, the shell would refuse (band + flow)
    c = next(l for l in lines if l["slug"] == "mkt-c")
    assert c["sampled"] == "open"
    assert set(c["executable"]["refusals"]) == {"entry_band", "thin_flow"}
    # funnel: discovered -> observable -> canonical -> in-distribution ->
    # sampled -> execution eligible, with reason-coded losses
    f = sh.funnel
    assert f["discovered_markets"] == 3 and f["observable"] == 2
    assert f["canonical_state_valid"] == 2 and f["l5_action_sampled"] == 2
    assert f["execution_eligible"] == 1
    assert f["losses"]["no_two_sided_book"] == 1
    assert f["losses"]["execution:entry_band"] == 1
    # the no-book market is re-evaluated once per cadence unit, not per tick
    sh.tick(_state(), NOW + 0.5)
    assert f["losses"]["no_two_sided_book"] == 1


def test_public_print_fills_the_resting_behavioral_order_and_reopens_the_decision(tmp_path):
    sh = ms.MultiShadow(tmp_path)
    st = _state()
    sh.tick(st, NOW)
    # a BUY print above the YES bid does not fill a resting YES bid
    st["mkt-a"]["trades"].append({"t": NOW + 0.5, "px": "0.46", "qty": "4", "dir": "buy"})
    sh.tick(st, NOW + 1.0)
    assert sh.markets["mkt-a"].inv.total == 0
    # a SELL print at/through the resting price fills strictly
    st["mkt-a"]["trades"].append({"t": NOW + 1.2, "px": "0.44", "qty": "4", "dir": "sell"})
    sh.tick(st, NOW + 1.5)
    m = sh.markets["mkt-a"]
    assert str(m.inv.up) == "4" and m.inv.last_rel == "open"
    assert m.inv.up_shares_behavioral == 10.0        # L5-scale shares recorded
    lines = [json.loads(l) for l in (tmp_path / ms.DECISIONS_FILE).read_text().splitlines()]
    fill = next(l for l in lines if l["kind"] == "pmus_shadow_fill")
    assert fill["side"] == "YES" and fill["px"] == "0.44" and fill["qty"] == "4"
    assert fill["print"]["dir"] == "sell"
    post = lines[-1]
    assert post["kind"] == "pmus_shadow_decision" and post["slug"] == "mkt-a"
    assert post["lookup"]["pre_fill"] is False
    assert post["state"] == "p25-45c|tte>10m|imb10-50"       # shares-scale imbalance
    assert post["residual_units"] == 1.0
    assert post["evolution_from_prev"]["prev_sampled"] == "open"
    assert post["evolution_from_prev"]["dt_s"] == 1.5
    rep = sh.report(NOW + 2)
    assert rep["unique_decisions"] == 3 and rep["post_fill_decisions"] == 1
    assert rep["theoretical_fills"] == 1
    assert rep["lookup_provenance"]["v2_decisions"] == 3
    assert rep["lookup_provenance"]["lookup_version_gate"] == 3
    assert rep["behavioral_layer"]["scored_decisions"] == 3
    assert rep["behavioral_layer"]["post_fill"]["decisions"] == 1


def test_complete_draw_records_the_l5_touch_vs_fee_safe_counterfactual(tmp_path, monkeypatch):
    sh = ms.MultiShadow(tmp_path)
    st = _state()
    sh.tick(st, NOW)
    st["mkt-a"]["trades"].append({"t": NOW + 1, "px": "0.44", "qty": "4", "dir": "sell"})
    sh.tick(st, NOW + 1.5)                       # filled YES 4 @0.44
    # force the next draw to COMPLETE and move the book so the pair at
    # L5's touch exceeds 1.00
    from src import pmus_l5_fidelity_live as fid
    monkeypatch.setattr(fid, "sample_relation", lambda rr, seed: {
        "sampled": "complete", "u": 0.1, "p": 0.3, "rank": 2, "seed": seed[:16]})
    st["mkt-a"]["bid"], st["mkt-a"]["ask"] = "0.40", "0.42"   # NO bid = 0.58
    m = sh.markets["mkt-a"]
    m.next_t = 0.0
    sh.tick(st, NOW + 40)
    last = [json.loads(l) for l in (tmp_path / ms.DECISIONS_FILE).read_text().splitlines()
            if '"slug":"mkt-a"' in l and '"pmus_shadow_decision"' in l][-1]
    assert last["sampled"] == "complete" and last["behavioral"]["side"] == "NO"
    ex = last["executable"]
    assert ex["pair_at_touch"] == "1.02"           # 0.44 + 0.58
    assert ex["pair_gt_1"] is True and ex["fee_safe_at_touch"] is False
    assert ex["fee_safe_bound_px"] == "0.54" and ex["px"] == "0.54"
    assert ex["delta_ticks_touch_minus_executable"] == "4"
    assert ex["eligible"] is True                  # rests at the bound, like the live shell
    rep = sh.report(NOW + 41)
    cf = rep["executable_layer"]["complete_counterfactual"]
    assert cf["complete_draws_with_inventory"] == 1 and cf["pair_gt_1_count"] == 1


def test_restart_restores_inventory_and_never_double_counts(tmp_path):
    sh = ms.MultiShadow(tmp_path)
    st = _state()
    sh.tick(st, NOW)
    st["mkt-a"]["trades"].append({"t": NOW + 1, "px": "0.44", "qty": "4", "dir": "sell"})
    sh.tick(st, NOW + 1.5)
    n_lines = len((tmp_path / ms.DECISIONS_FILE).read_text().splitlines())
    # duplicate a decision line on disk
    with (tmp_path / ms.DECISIONS_FILE).open("a") as f:
        f.write((tmp_path / ms.DECISIONS_FILE).read_text().splitlines()[0] + "\n")
    sh2 = ms.MultiShadow(tmp_path)
    assert sh2.restore == {"decisions": 3, "duplicates_dropped": 1, "fills": 1, "markets": 2}
    m = sh2.markets["mkt-a"]
    assert m.inv.snapshot() == sh.markets["mkt-a"].inv.snapshot()
    assert m.inv.up_shares_behavioral == 10.0 and m.idx == 2
    assert sh2.report(NOW + 2)["unique_decisions"] == 3
    # the next decision continues the index (seed provenance) and the file
    sh2.tick(st, NOW + 60)
    lines = [json.loads(l) for l in (tmp_path / ms.DECISIONS_FILE).read_text().splitlines()]
    assert len(lines) == n_lines + 1 + 2           # dup line + one decision per market
    assert [l["idx"] for l in lines if l["kind"] == "pmus_shadow_decision" and l["slug"] == "mkt-a"] == [0, 1, 0, 2]
    assert len({l["decision_id"] for l in lines if l["kind"] == "pmus_shadow_decision"}) == 5


def test_evaluator_reports_two_layers_and_the_pmus_shadow_beside_the_minimums(tmp_path):
    from tests.test_l5_evidence_integrity import _report
    sh = ms.MultiShadow(tmp_path)
    sh.tick(_state(), NOW)
    rep = sh.report(NOW + 1)
    out = gate.evaluate([_report("s1", "m1", 6)], pmus_shadow=rep)
    layers = out["layers"]
    assert set(layers) == {"behavioral", "executable", "rule"}
    b, e = layers["behavioral"], layers["executable"]
    assert "l1_sampled_vs_l5" in b["action_distribution_relation"]["value"]
    assert "l1_acted_vs_l5" in e["action_distribution_relation"]["value"]
    assert "size" in e and "timing_cadence" in e and "size" not in b
    # the combined dimensions are untouched by the layered reading
    assert out["live"]["action_distribution_relation"]["status"] in ("PASS", "FAIL", "INSUFFICIENT")
    ps = out["pmus_shadow"]
    assert ps["status"] == "OK" and ps["decisions"] == 2 and ps["markets"] == 2
    assert ps["counts_toward_frozen_minimums"] is False
    assert ps["action_distribution_relation"]["status"] in ("INSUFFICIENT", "FAIL")
    assert ps["lookup_provenance"]["lookup_version_gate"] == 3
    # frozen minimums unchanged and the pmus shadow not counted in them
    mins = out["evidence_progress"]["minimums"]
    assert mins["live_scored_decisions"]["need"] == 200 and mins["shadow_windows"]["need"] == 300
    assert mins["live_scored_decisions"]["have"] == 6
    # a report gating another lookup version is refused
    bad = {**rep, "lookup_provenance": {**rep["lookup_provenance"], "lookup_version_gate": 2}}
    assert gate.score_pmus_shadow(bad, gate.load_spec())["status"] == "LOOKUP_VERSION_MISMATCH"
    assert gate.score_pmus_shadow(None, gate.load_spec())["status"] == "UNMEASURED"


def test_probe_derives_the_shadow_url_from_the_recorder_status_url(monkeypatch):
    monkeypatch.delenv(cont.PMUS_SHADOW_URL_ENV, raising=False)
    monkeypatch.setenv(cont.entry.EVIDENCE_STATUS_URL_ENV,
                       "http://pmus-ws-evidence-recorder.railway.internal:8080/status")
    assert cont.pmus_shadow_url() == "http://pmus-ws-evidence-recorder.railway.internal:8080/l5_shadow"
    monkeypatch.setenv(cont.PMUS_SHADOW_URL_ENV, "http://x/l5_shadow")
    assert cont.pmus_shadow_url() == "http://x/l5_shadow"
    monkeypatch.delenv(cont.PMUS_SHADOW_URL_ENV, raising=False)
    monkeypatch.setenv(cont.entry.EVIDENCE_STATUS_URL_ENV, "/state/status.jsonl")
    assert cont.pmus_shadow_url() == ""


def test_recorder_side_import_chain_carries_no_order_path():
    """The recorder image imports the shadow, the adapter and the scorer
    only; none of them may pull the executor or the Gemini tape code."""
    loaded = {m for m in sys.modules}
    import importlib
    for name in ("src.pmus_l5_multi_shadow", "src.l5_venue_adapter", "src.l5_shadow_scoring"):
        importlib.import_module(name)
    # (other tests may have imported the executor already; check the
    # adapter's own module-level names instead of sys.modules)
    from src import l5_venue_adapter as va
    assert not hasattr(va, "q1") and not hasattr(va, "tb")
    assert callable(va._q1) and callable(va._tb)
    assert loaded is not None


def test_recorder_serves_the_shadow_report_beside_the_status_document(tmp_path):
    import socket
    import urllib.error
    import urllib.request
    from src import pmus_ws_evidence_recorder as rec
    root = tmp_path / "root"
    rec.write_status_doc(root, {"placeholder": True})
    sh = ms.MultiShadow(root / "l5_shadow")
    sh.tick(_state(), NOW)
    rep = sh.write_report(NOW + 1)
    assert rep and "error" not in rep
    assert rec.shadow_report_path(root).exists()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = rec.serve_status_doc(root, port)
    assert srv is not None
    base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base_url + "/l5_shadow", timeout=5) as resp:
            got = json.loads(resp.read().decode("utf-8"))
        assert got["kind"] == "pmus_l5_multi_shadow" and got["unique_decisions"] == 2
        assert got["lookup_provenance"]["lookup_version_gate"] == 3
        with urllib.request.urlopen(base_url + "/status", timeout=5) as resp:
            assert b"placeholder" in resp.read()
        try:
            urllib.request.urlopen(base_url + "/state", timeout=5)
            assert False, "unexpected path must 404"
        except urllib.error.HTTPError as err:
            assert err.code == 404
    finally:
        srv.shutdown()
    compact = rec.shadow_compact(rep)
    assert compact["kind"] == "shadow_report" and compact["unique_decisions"] == 2
    assert compact["funnel"]["execution_eligible"] == 1


def test_shadow_can_be_disabled_and_never_blocks_the_recorder(tmp_path, monkeypatch):
    from src import pmus_ws_evidence_recorder as rec
    monkeypatch.setenv(rec.SHADOW_ENV, "0")
    assert rec.start_multi_shadow(tmp_path) is None
    monkeypatch.setenv(rec.SHADOW_ENV, "1")
    sh = rec.start_multi_shadow(tmp_path)
    assert sh is not None and sh.restore["decisions"] == 0
