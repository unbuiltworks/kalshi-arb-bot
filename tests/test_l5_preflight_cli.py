"""The preflight runner, exercised as a program.

A pure module with no entry point is only half an executable preflight:
nobody but a test could run it. These tests drive the CLI the way the
owner or the deployment lane would, including the bug it shipped with.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "analysis", "l5", "issue208",
                   "run_probe_preflight.py")
MARKET = "cpc-btc-updown-15m-2026-11-08-1200z"

HB = {
    "submission_gate": {"blocked": True, "reason": "SHADOW_ONLY_LOCK",
                        "armed": False, "halt_latch": "CLEAR",
                        "halt_latch_telemetry": {"kind": "PRESENT"}},
    "effective_cash_gate": {"max_cash_deployed_usd": "12",
                            "cash_policy": "NO_RECYCLING",
                            "max_unmatched_contracts": "5",
                            "max_worst_case_deficit_usd": "1.00",
                            "worst_case_deficit_check": "ACTIVE"},
    "day_realized": {"room_usd": "5", "halt_usd": "5", "would_halt": False},
    "market": MARKET,
    "authorization_preflight": {"ok": True, "evaluated_market": MARKET},
    "campaign": {"permits_this_window": True,
                 "completion_reserve": {"may_open": True,
                                        "submissions_per_obligation": 2}},
    "auth_book": {"connected": True, "stream_age_s": 0.0,
                  "gap_suspected": False, "stall_reconnects": 0},
    "realized": {"unknown_basis_keys": []},
    "accounting": {"unknown_detail": {
        "post_baseline_settlements_not_attested": []}},
    # the recorder report a LIVE gate may pass. The `readback` block is
    # part of that: `events_appended` is a per-process counter zeroed by
    # every restart and so cannot establish that order events survive one.
    # `events_replayed_from_disk` can only be non-zero if a previous
    # process's write came back, which is the fact the gate needs.
    "fill_calibration": {"orders": 3, "events_appended": 11, "errors": {},
                         "orders_straddling_a_restart": 0,
                         "events_replayed_from_disk": 11,
                         "readback": {"readable": True, "error": None,
                                      "events_readable": 11,
                                      "orders_readable": 3,
                                      "orders_with_full_capture": 3,
                                      "unreadable_lines": 0,
                                      "events_appended_this_process": 0,
                                      "events_replayed_from_disk": 11}},
}


def write_hb(tmp_path, labelled=True, **over):
    hb = dict(HB)
    hb.update(over)
    p = tmp_path / "hb.json"
    body = json.dumps(hb)
    p.write_text(("L5_LIVE_HB " + body) if labelled else body)
    return str(p)


def run(*args, env=None):
    e = dict(os.environ)
    e.setdefault("L5_TS_EXPERIMENT_ID", "cli-test")
    e.setdefault("L5_TS_MARKET", MARKET)
    e.update(env or {})
    return subprocess.run([sys.executable, CLI, *args], capture_output=True,
                          text=True, env=e, timeout=180, cwd=ROOT)


def test_a_clean_preflight_passes_and_exits_zero(tmp_path):
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63",
            "--depth", "2")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS  ok=True" in r.stdout
    assert "FAIL" not in r.stdout


def test_the_market_is_passed_as_a_STRING_not_a_list(tmp_path):
    """THE BUG THIS SHIPPED WITH. `--market` is action="append", so
    `a.market` is a list, and passing it to `build_orders` refused every
    plan with NOT_THE_AUTHORIZED_MARKET -- a failure that looks exactly
    like a misconfigured environment, which is the worst disguise for a
    bug in the tool you reach for when unsure of your configuration."""
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63")
    assert "NOT_THE_AUTHORIZED_MARKET" not in r.stdout, r.stdout
    assert "plan_market_is_pre_registered" in r.stdout


def test_it_reads_a_labelled_log_line_as_well_as_a_bare_object(tmp_path):
    for labelled in (True, False):
        r = run("preflight", "--heartbeat",
                write_hb(tmp_path, labelled=labelled), "--market", MARKET,
                "--bid", "0.62", "--ask", "0.63")
        assert r.returncode == 0, (labelled, r.stdout + r.stderr)


def test_no_market_is_a_refusal_with_its_own_exit_code(tmp_path):
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--bid", "0.62", "--ask", "0.63")
    assert r.returncode == 2
    assert "pre-registered" in r.stdout


def test_several_markets_are_refused_for_a_PLAN_which_prices_one(tmp_path):
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--market", MARKET, "--market", "cpc-btc-updown-15m-x-0000z",
            "--bid", "0.62", "--ask", "0.63")
    assert r.returncode == 2
    assert "ONE market" in r.stdout


def test_a_depth_0_plan_fails_and_exits_one(tmp_path):
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63",
            "--depth", "0")
    assert r.returncode == 1
    assert "PAIR_COST_ABOVE_CAP" in r.stdout


def test_an_armed_gate_fails_the_run(tmp_path):
    hb = dict(HB["submission_gate"])
    hb.update({"blocked": False, "armed": True})
    r = run("preflight", "--heartbeat",
            write_hb(tmp_path, submission_gate=hb), "--market", MARKET,
            "--bid", "0.62", "--ask", "0.63")
    assert r.returncode == 1
    assert "submission_gate_blocked_until_owner_clears" in r.stdout


def test_a_stale_book_fails_the_run(tmp_path):
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63",
            "--book-age", "9.9")
    assert r.returncode == 1


def test_json_mode_emits_a_parseable_verdict(tmp_path):
    r = run("preflight", "--heartbeat", write_hb(tmp_path),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63", "--json")
    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert isinstance(out["checks"], list) and out["checks"]


def test_the_audit_mode_runs_on_a_store_state_file(tmp_path):
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({
        "positions": {"%s|UP" % MARKET: "1", "%s|DOWN" % MARKET: "1"},
        "leg_cost": {"%s|UP" % MARKET: "0.60", "%s|DOWN" % MARKET: "0.35"},
        "submitted": {"l5f-a": {"market": MARKET, "venue_order_id": "V-1"},
                      "l5f-b": {"market": MARKET, "venue_order_id": "V-2"}},
        # the POSITIVE evidence the audit now requires, read from the
        # store's own state rather than from the heartbeat
        "fill_count": {"%s|UP" % MARKET: 1, "%s|DOWN" % MARKET: 1},
        "gross_quarantine": {},
        "live_orders": {}}))
    r = run("audit", "--heartbeat", write_hb(tmp_path), "--inventory",
            str(inv), "--market", MARKET)
    # MEASURED is the verdict on the BASIS. The exit code is 1 because a
    # store state file carries no fee record, so the fee check is UNKNOWN
    # -- which is the right answer: fees are confirmed from the venue,
    # not from our own ledger.
    # ASSERT ON THE VERDICT LINE, not on the whole output: the module's
    # own `labels` text explains what UNINTERPRETABLE means, so a naive
    # substring search over stdout matches the explanation rather than the
    # verdict. That tripped the first version of this test.
    verdict = r.stdout.splitlines()[0]
    assert verdict.startswith("MEASURED"), r.stdout
    assert "UNINTERPRETABLE" not in verdict, r.stdout
    assert "actual_fees_and_rebates_present" in r.stdout


def test_the_audit_mode_classifies_an_unknown_basis_as_UNINTERPRETABLE(
        tmp_path):
    inv = tmp_path / "inv.json"
    inv.write_text(json.dumps({
        "positions": {"%s|UP" % MARKET: "1"},
        "leg_cost": {"%s|UP" % MARKET: "0.60"},
        "submitted": {"l5f-a": {"market": MARKET, "venue_order_id": "V-1"}},
        "live_orders": {}}))
    r = run("audit", "--heartbeat",
            write_hb(tmp_path,
                     realized={"unknown_basis_keys": ["%s|UP" % MARKET]}),
            "--inventory", str(inv), "--market", MARKET)
    assert r.returncode == 1
    assert r.stdout.splitlines()[0].startswith("UNINTERPRETABLE"), r.stdout


def test_the_cli_changes_nothing(tmp_path):
    """It is a reader. No transport, no submission, no env mutation of
    the things that gate money."""
    src = open(CLI, encoding="utf-8").read().lower()
    for banned in ("record_submit", "apply_order_progress", "arm(",
                   ".post(", "requests.", "os.environ["):
        assert banned not in src, banned


def test_cli_rejects_stale_executor_market(tmp_path):
    r = run("preflight", "--heartbeat",
            write_hb(tmp_path, market="cpc-btc-updown-15m-2026-11-08-1145z"),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63")
    assert r.returncode == 1
    assert "executor_active_market_matches" in r.stdout


def test_cli_rejects_document_only_authorization(tmp_path):
    r = run("preflight", "--heartbeat",
            write_hb(tmp_path, authorization_preflight={
                "ok": True, "evaluated_market": None}),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63")
    assert r.returncode == 1
    assert "authorization_evaluated_same_market" in r.stdout


def test_cli_rejects_wrong_campaign_window(tmp_path):
    r = run("preflight", "--heartbeat",
            write_hb(tmp_path, campaign={
                "permits_this_window": False,
                "completion_reserve": {"may_open": True}}),
            "--market", MARKET, "--bid", "0.62", "--ask", "0.63")
    assert r.returncode == 1
    assert "campaign_permits_exact_window" in r.stdout
