# One live window: cpc-btc-updown-15m-2026-11-11-1900z

Owner-approved 2026-11-11. One BTC 15m window, 19:00:00-19:15:00Z, on
`ff4b5291afc8a272cd5e8ae5e873120a1d4b67d6`. Maker only. Inventory recovery at
the 0.98 target, requote-keeps-round, reducing-replace budget fallback. Cash
recycling off, taker off. Two live orders, one round at a time, OPEN 4 /
COMPLETE 12 / REPLACE 16 / total 32, additional loss $1.00. Every other limit
unchanged. No other window.

Run from this directory, in order:

    python3 rec_apply.py armed.json --dry   # every check, no write
    python3 rec_apply.py armed.json         # arms; only 7-25 min before 19:00Z
    python3 bal_verify.py armed.json        # read-only: what is actually running
    python3 bal_mon.py 19:15:30             # read-only: state changes
    python3 bal_restore.py                  # GO / HOLD, writes nothing
    python3 bal_restore.py --write          # both locks back, test switches emptied

`rec_apply.py` refuses to write unless all of its preconditions hold.
`bal_restore.py --write` refuses while any order is still at the venue.
