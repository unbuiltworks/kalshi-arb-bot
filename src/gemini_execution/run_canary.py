"""Run the ONE authorized Gemini canary on an ephemeral CI host.

THE PROBLEM THIS SOLVES. `latch.py` guarantees one-shot against a crash on a
host whose disk persists. A GitHub Actions runner has no such disk: a retried
workflow starts clean, the local latch is gone, and the guarantee is worth
nothing precisely when it is needed. So the latch is bound to GIT, which
outlives the runner:

  1 look for a committed latch for this authorization; present -> refuse
  2 consume locally
  3 COMMIT AND PUSH the latch -- durable, before the venue sees anything
  4 only then send

Step 3 before step 4 is the whole design. A failed push means nothing was
sent, so a later attempt places exactly one order. The unsafe ordering is the
other one: send, fail to record, and let a retry send a second.

Nothing here decides the envelope. `canary.submit_once` does, from a fresh
preflight, and refuses itself if any rule fails.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import time
from typing import Any, Dict, Mapping, Optional, Sequence

from src.gemini_execution import arming, canary, latch

#: committed alongside the evidence artifacts, on the working branch
DURABLE_DIR = "analysis/gemini/canary_latch"
#: THE LATCH MUST BE PUSHABLE, OR NOTHING TRADES. make_durable() writes the
#: latch here and refuses the mutation if it cannot reach the remote -- which
#: is correct, and which makes this constant a live safety dependency rather
#: than a cosmetic one.
#:
#: MOVED 2026-09-21. It still named the instrumentation branch after the
#: workflow checkout moved to the q=1 branch, so run 35570948794 checked out
#: one tree and tried to push its latch onto another. The push was rejected,
#: the rebase onto that unrelated history failed, and the driver refused with
#: ZERO ORDERS -- correctly, but for an infrastructure reason. Had the rebase
#: SUCCEEDED it would have been worse: it would have pushed the q=1 tree onto
#: the instrumentation branch as a side effect of latching, which is the merge
#: that is explicitly forbidden.
BRANCH = "claude/gemini-btc-economic-viability-h3u7zl"


def _durable_path(auth_id: str) -> pathlib.Path:
    safe = "".join(c for c in auth_id if c.isalnum() or c in "-_")
    return pathlib.Path(DURABLE_DIR) / ("latch_%s.json" % safe)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def durable_check(auth_id: str) -> bool:
    """Is this authorization already recorded as consumed, in git?

    Checks the remote as well as the working tree: another runner may have
    consumed it since this job checked out.
    """
    path = _durable_path(auth_id)
    if path.exists():
        return True
    _git("fetch", "origin", BRANCH)
    r = _git("cat-file", "-e", "origin/%s:%s" % (BRANCH, path))
    return r.returncode == 0


def can_make_durable() -> Dict[str, Any]:
    """Could make_durable() push, if it were called right now? Pushes NOTHING.

    WHY THIS EXISTS, AND WHY IT IS NOT MERELY A NICETY. latch.consume() marks
    the authorization spent BEFORE make_durable() pushes it -- deliberately,
    because a lost order is preferred to a double order. The consequence
    nobody priced: when the PUSH fails, the grant is already spent on disk and
    is swept into the evidence commit, so an INFRASTRUCTURE fault costs an
    owner authorization even though nothing traded. That is exactly how
    OWNER-2026-09-21-GEMINI-Q1-LIVE-1 died: run 35570948794 could not push the
    latch onto a branch it shared no history with, and the grant went with it.

    Asking FIRST costs nothing and changes no guarantee: the latch is still
    written, committed and pushed before any mutation. This only moves the
    infrastructure failure to a point where it is free.

    The condition checked is the one a fast-forward push needs: the remote
    branch exists and is an ancestor of HEAD. An unrelated branch -- the
    run-68/69 failure -- fails this precisely.
    """
    fetch = _git("fetch", "origin", BRANCH)
    if fetch.returncode != 0:
        return {"ok": False,
                "why": "cannot fetch origin/%s: %s"
                       % (BRANCH, fetch.stderr.strip()[:200])}
    anc = _git("merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD")
    if anc.returncode != 0:
        return {"ok": False,
                "why": ("origin/%s is not an ancestor of HEAD, so the latch "
                        "push would be rejected and the rebase would run on "
                        "unrelated history; refusing BEFORE the grant is "
                        "spent rather than after" % BRANCH)}
    return {"ok": True, "why": None, "branch": BRANCH}


def make_durable(auth_id: str, record: Mapping[str, Any]) -> None:
    """Commit and push the latch. Raises on failure, which aborts the send."""
    path = _durable_path(auth_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(record), indent=2, default=str) + "\n",
                    encoding="utf-8")
    _git("config", "user.name", "gemini-canary[bot]")
    _git("config", "user.email", "gemini-canary@users.noreply.github.com")
    add = _git("add", "--", str(path))
    if add.returncode != 0:
        raise RuntimeError("git add failed: %s" % add.stderr[:200])
    cm = _git("commit", "-m",
              "latch(gemini): authorization %s consumed [skip ci]" % auth_id)
    if cm.returncode != 0:
        raise RuntimeError("git commit failed: %s" % cm.stderr[:200])
    for attempt in range(3):
        push = _git("push", "origin", "HEAD:%s" % BRANCH)
        if push.returncode == 0:
            return
        _git("fetch", "origin", BRANCH)
        rb = _git("rebase", "FETCH_HEAD")
        if rb.returncode != 0:
            _git("rebase", "--abort")
            raise RuntimeError("could not rebase the latch onto the remote")
        time.sleep(1 + attempt)
    raise RuntimeError("could not push the durable latch after 3 attempts")


async def no_existing_canary_exposure(symbol: str) -> Dict[str, Any]:
    """Owner gate: no existing canary order or residual before submitting."""
    from src.gemini_execution import transport as T
    active = await T.active_orders(symbol)
    if not active.get("ok"):
        # UNREADABLE IS NOT EMPTY. _orders_of returns [] for a failed read as
        # much as for an empty account, so without this the gate reports
        # "clear" on a read that never happened -- and "0 active orders" in
        # the run-6 record is, for that reason, not evidence the read worked.
        return {"clear": False,
                "why": "active orders unreadable (%s %s); an unread order "
                       "book is not an empty one"
                       % (active.get("status"), active.get("classification")),
                "read": {k: active.get(k) for k in
                         ("status", "classification", "error",
                          "request_shape", "json_body_attempt")}}
    orders = T._orders_of(active)
    from src.gemini_prediction_auth import private_rest_request
    try:
        r = await private_rest_request(
            "POST", "/v1/prediction-markets/positions", timeout=15.0)
        body = r.json()
    except Exception as exc:                                   # noqa: BLE001
        return {"clear": False, "why": "positions unreadable: %s"
                % type(exc).__name__}
    pos = body.get("positions") if isinstance(body, dict) else body
    n_pos = len(pos) if isinstance(pos, list) else None
    if n_pos is None:
        return {"clear": False, "why": "positions payload not a list"}
    return {"clear": (not orders) and n_pos == 0,
            "active_orders": len(orders), "open_positions": n_pos,
            "why": (None if (not orders and n_pos == 0)
                    else "an existing order or residual is present")}


async def main_async(auth_id: str, state_dir: str) -> Dict[str, Any]:
    from src.gemini_execution import readiness as R

    rep = await R.report(auth_id, state_dir=state_dir)
    pf = rep.get("preflight") or {}
    out: Dict[str, Any] = {"readiness": {k: rep[k] for k in
                                         ("gates", "blocking", "verdict")}}

    if rep["verdict"] != "GEMINI_EXECUTION_READY":
        out["state"] = "PREFLIGHT_REFUSED"
        out["why"] = "blocking gates: %s" % rep["blocking"]
        return out

    sym = pf.get("instrument")
    clear = await no_existing_canary_exposure(sym)
    out["existing_exposure"] = clear
    if not clear.get("clear"):
        out["state"] = "PREFLIGHT_REFUSED"
        out["why"] = "existing canary order or residual: %s" % clear.get("why")
        return out

    # the BBO and queue-ahead AS AT submission, captured before the mutation
    out["bbo_before_submission"] = pf.get("current_bbo")
    out["queue_ahead_at_submission"] = (pf.get("fill_prospect") or {}).get(
        "volume_ahead_at_our_price")
    out["fee_bps_conservative"] = 60

    res = await canary.submit_once(auth_id, state_dir=state_dir,
                                   durable_check=durable_check,
                                   make_durable=make_durable)
    out.update(res)
    out["submitted_at"] = time.time()
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--auth-id", default=arming.EXPECTED_AUTH_ID)
    ap.add_argument("--state-dir", default=latch.DEFAULT_DIR)
    a = ap.parse_args(argv)
    out = asyncio.run(main_async(a.auth_id, a.state_dir))
    print("GEMINI_CANARY " + json.dumps(out, indent=2, default=str))
    # a refusal is a RESULT, not a job failure
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
