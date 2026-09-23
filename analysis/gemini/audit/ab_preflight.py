"""A+B preflight, verified FROM THE ACTUAL DISPATCH PATH.

    python3 analysis/gemini/audit/ab_preflight.py       # exit 0 = all pass

It parses the workflow YAML and reads the values the runner will actually use
-- spec id, spec sha, --max-windows, --max-loss-usd, --max-drawdown-usd, the
authorization id and the expiry -- rather than trusting a manifest. The last
attempt to arm failed because a manifest and a workflow disagreed about all of
those, and a document cannot catch that. A parser can.

Every check FAILS CLOSED: any mismatch exits non-zero and the probe does not
proceed.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shlex
import sys
from decimal import Decimal as D

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

import yaml                                                    # noqa: E402

from src.gemini_execution import bundle                        # noqa: E402
from src.gemini_execution import cohort as CO                  # noqa: E402
from src.gemini_execution import live_probe_spec as S          # noqa: E402
from src.gemini_execution.cohort_runner import discover_chain  # noqa: E402

WF = os.path.join(ROOT, ".github", "workflows", "gemini-settlement-gate.yml")
AUTH_ID = "OWNER-2026-09-23-GEMINI-Q1-AB-1"
ARMS = {"A": "PROBE A - BTC15m", "B": "PROBE B - BTC5m"}


def _steps():
    doc = yaml.safe_load(open(WF))
    out = []
    for job in (doc.get("jobs") or {}).values():
        out.extend(job.get("steps") or [])
    return out


def dispatch_facts():
    """What the runner will actually execute, per arm."""
    facts = {}
    for arm, prefix in ARMS.items():
        step = next((s for s in _steps()
                     if str(s.get("name", "")).startswith(prefix)), None)
        if step is None:
            facts[arm] = {"found": False}
            continue
        env = step.get("env") or {}
        run = str(step.get("run") or "")
        #: the CLI is the authority, so it is PARSED, not assumed
        flat = run.replace("\\\n", " ")
        toks = shlex.split(flat, comments=True)
        def flag(name):
            return toks[toks.index(name) + 1] if name in toks else None
        spec = env.get("COHORT_SPEC")
        facts[arm] = {
            "found": True, "step_name": step.get("name"),
            "spec_id": spec, "spec_sha": env.get("COHORT_SPEC_SHA"),
            "auth_id": env.get("GEMINI_CANARY_AUTH"),
            "expires": env.get("GEMINI_CANARY_AUTH_EXPIRES"),
            "live_flag": env.get("GEMINI_CANARY_LIVE"),
            "max_windows": flag("--max-windows"),
            "max_loss": flag("--max-loss-usd"),
            "max_drawdown": flag("--max-drawdown-usd"),
            "uses_spec_flag": "--spec" in toks,
            "if": step.get("if"),
        }
    return facts


def checks():
    f = dispatch_facts()
    out = []

    def ck(name, ok, detail):
        out.append((name, bool(ok), detail))

    for arm, want_windows, want_family, want_spec in (
            ("A", "2", "btc15m", "btc15m_q1_cap4_w60_880_c2"),
            ("B", "4", "btc5m", "btc5m_q1_improve")):
        d = f[arm]
        ck("%s armed step exists" % arm, d.get("found"),
           d.get("step_name", "MISSING"))
        if not d.get("found"):
            continue
        sp = S.get(d["spec_id"]) if d["spec_id"] in S.SPECS else None
        ck("%s dispatches the right spec" % arm, d["spec_id"] == want_spec,
           "%s (want %s)" % (d["spec_id"], want_spec))
        ck("%s spec family" % arm, sp and sp.family == want_family,
           sp.family if sp else "?")
        ck("%s spec_sha in workflow == resolved" % arm,
           sp and d["spec_sha"] == sp.spec_sha(),
           "%s" % (d["spec_sha"] or "")[:16])
        ck("%s --max-windows == %s" % (arm, want_windows),
           d["max_windows"] == want_windows, str(d["max_windows"]))
        ck("%s --max-loss-usd == 10.00" % arm, d["max_loss"] == "10.00",
           str(d["max_loss"]))
        ck("%s --max-drawdown-usd == 5.00" % arm,
           d["max_drawdown"] == "5.00", str(d["max_drawdown"]))
        ck("%s auth id" % arm, d["auth_id"] == AUTH_ID, str(d["auth_id"]))
        #: a future, bounded expiry -- absent is refused by armed() and an
        #: open-ended one is not a one-probe authorization
        try:
            exp = dt.datetime.fromisoformat(
                str(d["expires"]).replace("Z", "+00:00"))
            now = dt.datetime.now(dt.timezone.utc)
            hrs = (exp - now).total_seconds() / 3600.0
            ck("%s expiry is future and bounded (<24h)" % arm,
               0 < hrs < 24, "%s (%.1fh away)" % (d["expires"], hrs))
        except Exception as exc:                               # noqa: BLE001
            ck("%s expiry parses" % arm, False, "%s: %s" % (d["expires"], exc))
        ck("%s spec drives the invocation" % arm, d["uses_spec_flag"],
           "--spec present")
        #: chains, read from disk
        chain = discover_chain(sp) if sp else ()
        unused = [c for c in chain if CO.state(c)["consumed"] == 0]
        ck("%s chain length == %s" % (arm, want_windows),
           len(chain) == int(want_windows), "%d ledgers" % len(chain))
        ck("%s chain fully UNUSED" % arm, len(unused) == len(chain),
           "%d of %d unused" % (len(unused), len(chain)))
        e = sp.envelope_obj if sp else None
        ck("%s max_concurrent_markets == 1" % arm,
           e and e.max_concurrent_markets == 1,
           str(e.max_concurrent_markets if e else "?"))

    # structural ceiling, computed from the chain lengths and the envelopes
    a, b = S.get("btc15m_q1_cap4_w60_880_c2"), S.get("btc5m_q1_improve")
    na, nb = len(discover_chain(a)), len(discover_chain(b))
    ceil = (na * a.envelope_obj.max_committed_usd
            + nb * b.envelope_obj.max_committed_usd)
    ck("structural ceiling == $18.00", ceil == D("18.00"),
       "%d x $%s + %d x $%s = $%s"
       % (na, a.envelope_obj.max_committed_usd, nb,
          b.envelope_obj.max_committed_usd, ceil))

    # maker/post-only protections
    from src.gemini_execution import ab_arms as AB
    bk = {"yes": {"ask": "0.46"}, "no": {"ask": "0.60"}}
    ck("maker_improve refuses to reach the ask",
       AB.maker_only_improve(bk, "YES", "0.45") is None
       and AB.maker_only_improve(bk, "YES", "0.44") == D("0.45"),
       "0.45 -> None (would be 0.46 = ask); 0.44 -> 0.45")
    ck("B placement is maker_improve", b.placement == "maker_improve",
       str(b.placement))
    ck("A placement is the frozen one", a.placement is None, str(a.placement))

    # arming state. Before the owner's GO this asserted AUTHORIZED_BUNDLE was
    # None; after it, the meaningful check is that the installed value is the
    # EXACT hash approved and that it still equals the bundle actually running
    # -- which is what `authorized()` compares. A file edited after review
    # breaks the equality and the run refuses at preflight.
    APPROVED = ("48ec77ac64fc412a18a311f3c18bb4aeeb464452ec8573d70b"
                "4e4bdd8f3cb279")
    ck("AUTHORIZED_BUNDLE == the approved hash",
       bundle.AUTHORIZED_BUNDLE == APPROVED, str(bundle.AUTHORIZED_BUNDLE))
    ck("running bundle == authorized bundle", bundle.authorized(),
       "; ".join(bundle.state()["why_not"]) or "match")
    from src.gemini_execution import arming as _AR
    ck("EXPECTED_AUTH_ID == the approved identifier",
       _AR.EXPECTED_AUTH_ID == AUTH_ID, _AR.EXPECTED_AUTH_ID)
    ck("bundle normalizes, 15 members", len(bundle.MEMBERS) == 15,
       bundle.bundle_sha())
    return out


def main():
    rows = checks()
    bad = [r for r in rows if not r[1]]
    print("A+B PREFLIGHT -- read from %s" % os.path.relpath(WF, ROOT))
    for name, ok, detail in rows:
        print("  %-6s %-42s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("\nBUNDLE_SHA %s" % bundle.bundle_sha())
    print("VERDICT: %s" % ("ALL PASS" if not bad
                           else "FAILED CLOSED on %s" % [r[0] for r in bad]))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
