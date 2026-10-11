"""READ-ONLY Kalshi account preflight. No order path exists in this module.

Owner comment 5594247824 confirms $3,419 cash on the Kalshi account and asks
for read-only verification of account identity, balance, and -- most
valuable -- whether historical credited liquidity rewards already exist,
because that would settle share of score WITHOUT a new trade.

--- FINDING THAT CHANGES THE PROBE'S READOUT: THERE IS NO REWARD LEDGER ---
Endpoint existence was mapped from THIS session with no credentials at all,
by distinguishing 401 (exists, needs auth) from 404 (does not exist). That
is a free, safe probe and it settles the question:

    EXISTS (401):  /portfolio/balance
                   /portfolio/settlements
                   /portfolio/fills
                   /portfolio/orders
                   /portfolio/positions
                   /portfolio/orders/queue_positions
    404:           /portfolio/rewards, /liquidity_rewards, /incentives,
                   /incentive_payouts, /reward_payouts, /payouts, /credits,
                   /earnings, /incentive_earnings, /market_maker_rewards,
                   /transactions, /ledger, /account_history, /statements,
                   /activity, /history, /summary, /account  (31 names tried)

So Kalshi exposes NO dedicated reward-ledger endpoint to us. A credited
liquidity reward cannot be read directly; it can only be INFERRED from a
balance movement that fills and settlements do not explain:

    credited_reward = balance_delta - realised_fill_cash - settlement_cash

That is a real, auditable observation, and it is why the probe's design of
ONE market for ONE period matters more than I first thought: the fewer
concurrent cashflows, the cleaner the residue. It also means the probe
REQUIRES a quiet account -- any other activity in the window contaminates
the only instrument we have.

--- WHAT THIS MODULE REPORTS, AND WHAT IT REFUSES TO ---
It emits account identity in NON-SECRET form only (a suffix of the key id,
never the key, never the PEM), the cash balance, and a scan of historical
fills/settlements against the incentivised series for evidence of prior
resting activity.

If no prior credited reward can be demonstrated, it reports NONE. It never
infers a share from displayed depth -- that is exactly the fabrication the
owner prohibited, and the leg ranking already shows why: the same book
supports a 78% share or a 0.7% share depending on a denominator we do not
have.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal as D, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence

ORDER_ENTRY = False
READ_ONLY = True

INCENTIVISED_SERIES = ("KXNATGAS15M", "KXCOPPER15M", "KXSILVER15M",
                       "KXWTI15M", "KXGOLD15M", "KXCRYPTOLEAD15M")

# Mapped from this session with NO credentials, by 401-vs-404.
ENDPOINTS_THAT_EXIST = ("/portfolio/balance", "/portfolio/settlements",
                        "/portfolio/fills", "/portfolio/orders",
                        "/portfolio/positions",
                        "/portfolio/orders/queue_positions")

NO_REWARD_LEDGER = (
    "Kalshi exposes no dedicated reward-ledger endpoint. 31 plausible names "
    "return 404. A credited liquidity reward is therefore OBSERVABLE ONLY "
    "as a balance movement unexplained by fills and settlements, which "
    "requires a quiet account across the measurement window.")

REWARD_OBSERVATION_PATH = (
    "credited_reward = balance_delta - realised_fill_cash - settlement_cash, "
    "taken across one reward period on an otherwise idle account. Snapshot "
    "balance BEFORE the quote goes up and AFTER the period settles; any "
    "other activity in that window destroys the only instrument we have.")


def key_id_fingerprint(key_id: Optional[str]) -> Optional[str]:
    """A non-secret handle for the credential in use. Last four characters
    only -- enough for the owner to confirm WHICH key is configured, never
    enough to reconstruct it. Returns None when unset, which is a fact
    worth reporting rather than an error to swallow."""
    if not key_id:
        return None
    k = str(key_id).strip()
    return f"...{k[-4:]}" if len(k) >= 4 else "(too short to fingerprint)"


def credential_presence() -> Dict[str, Any]:
    """Presence and shape ONLY. This function must never return, log or
    embed a key, a PEM, or any substring of either beyond the four-character
    fingerprint above."""
    kid = os.getenv("KALSHI_API_KEY_ID", "")
    pem = os.getenv("KALSHI_PRIVATE_KEY_PEM", "")
    path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
    return {
        "api_key_id_present": bool(kid),
        "api_key_id_fingerprint": key_id_fingerprint(kid),
        "private_key_pem_present": bool(pem.strip()),
        "private_key_path_set": bool(path),
        "env": os.getenv("KALSHI_ENV", "demo"),
        "note": ("presence and a 4-char fingerprint only; no key material "
                 "is returned by this function under any code path"),
    }


def balance_usd(payload: Mapping[str, Any]) -> Optional[D]:
    """Kalshi returns the balance in CENTS. Returns None rather than 0 when
    the field is absent, because an unreadable balance and a zero balance
    are different facts and only one of them is a blocker."""
    if payload is None or "balance" not in payload:
        return None
    try:
        return D(str(payload["balance"])) / D(100)
    except (TypeError, ValueError, InvalidOperation):
        # Decimal raises InvalidOperation, which is an ArithmeticError and
        # NOT a ValueError -- catching only the latter let a junk balance
        # crash the preflight instead of reporting it as unreadable.
        return None


def scan_for_prior_incentivised_activity(
        fills: Sequence[Mapping[str, Any]],
        settlements: Sequence[Mapping[str, Any]],
        series: Sequence[str] = INCENTIVISED_SERIES) -> Dict[str, Any]:
    """Has this account ever traded an incentivised series? Resting activity
    is what earns liquidity rewards, and a fill proves an order was resting
    when it was hit. No fills on these series means no reward can ever have
    been credited, which is a clean NONE rather than an unknown."""
    want = {s.upper() for s in series}

    def ser(row: Mapping[str, Any]) -> str:
        return str(row.get("ticker") or row.get("market_ticker")
                   or "").split("-", 1)[0].upper()

    hit_fills = [f for f in fills if ser(f) in want]
    hit_setts = [s for s in settlements if ser(s) in want]
    by_series: Dict[str, int] = {}
    for f in hit_fills:
        by_series[ser(f)] = by_series.get(ser(f), 0) + 1
    return {
        "fills_on_incentivised_series": len(hit_fills),
        "settlements_on_incentivised_series": len(hit_setts),
        "by_series": by_series,
        "prior_activity": bool(hit_fills or hit_setts),
        "verdict": ("NONE - no fill or settlement on any incentivised "
                    "series, so no liquidity reward can ever have been "
                    "credited and share of score remains unmeasured"
                    if not (hit_fills or hit_setts) else
                    "PRIOR ACTIVITY FOUND - reconcile the balance across "
                    "those dates before assuming a reward was credited"),
    }


def preflight(balance_payload: Optional[Mapping[str, Any]] = None,
              fills: Sequence[Mapping[str, Any]] = (),
              settlements: Sequence[Mapping[str, Any]] = (),
              expected_cash_usd: Optional[D] = None) -> Dict[str, Any]:
    """The whole read-only check, assembled from data the caller fetched.
    Pure: it performs no I/O, so it is fully testable and cannot leak."""
    cash = balance_usd(balance_payload) if balance_payload is not None else None
    matches = None
    if cash is not None and expected_cash_usd is not None:
        # within a dollar -- the owner quoted a rounded figure
        matches = abs(cash - D(expected_cash_usd)) <= D("1.00")
    return {
        "kind": "KALSHI_ACCOUNT_PREFLIGHT",
        "read_only": READ_ONLY,
        "order_entry": ORDER_ENTRY,
        "credentials": credential_presence(),
        "cash_usd": (str(cash) if cash is not None else None),
        "expected_cash_usd": (str(expected_cash_usd)
                              if expected_cash_usd is not None else None),
        "balance_matches_owner_figure": matches,
        "endpoints_that_exist": list(ENDPOINTS_THAT_EXIST),
        "no_reward_ledger": NO_REWARD_LEDGER,
        "reward_observation_path": REWARD_OBSERVATION_PATH,
        "prior_incentivised_activity":
            scan_for_prior_incentivised_activity(fills, settlements),
        "realized_pnl": None,
    }


BLOCKED_HERE = (
    "This session holds NO Kalshi credentials: no KALSHI_API_KEY_ID, no "
    "KALSHI_PRIVATE_KEY_PEM, no key file. The two ways to obtain them are "
    "both closed by standing rules -- reading Railway service variables "
    "would pull secret values into the transcript, and the credentials that "
    "do exist are reached through the Gemini/Kalshi transport on AWS, where "
    "no work is authorised. So the preflight is WRITTEN AND TESTED but has "
    "NOT been executed against the live account, and no balance, identity "
    "or reward figure in this repo is asserted from it.")
