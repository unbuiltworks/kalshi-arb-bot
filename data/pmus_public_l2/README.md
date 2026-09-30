# Live full-L2 capture from the PUBLIC PMUS gateway

Recorded by `scripts/l5_public_l2_recorder.py` directly from this session — no
credentials, no Railway service, no deployment, nothing touched.

    GET /v1/markets/{slug}/book   bids[] + offers[], 30-44 levels a side,
                                  with the venue's own transactTime
    GET /v1/markets/{slug}/bbo    lastTradePx + CUMULATIVE sharesTraded

~2 Hz, i.e. roughly 1,800 full-depth snapshots per 15-minute window, against the
50 top-of-book states per window that earlier analysis used.

## What this data CANNOT support

**There are no individual trade prints here.** The public feed exposes a
cumulative `sharesTraded` counter and a last price. Between two polls we know how
much traded and the last price it traded at — not how that volume split across
prints or price levels, and there is no trade id and no taker direction.

So volume attribution at a non-touch level is an **inference**, not an
observation. Any fill derived from it is an inferred fill and must be labelled
as such. It must never be reported beside a print-derived fill as though the two
were the same class of evidence. Print-accurate work uses the authenticated WS
slice in `data/pmus_ws_stream/raw_l2/` instead.

Files are append-only and a capture in progress may be mid-window.
