# Shared inventory allocator for crypto: prepared, not wired, not deployed

27 Nov 2026. Branch `dev/l5-crypto-shared-allocator`, built from `19ca55ad2`
(the latest crypto line). **The crypto controller, its parameters, its
service and its limits are unchanged.** Crypto stays disarmed.

## What is here

- **`src/pmus_directional_alloc.py`:** the allocator shared with the sports
  lane. It is an identical file (sha256 `5b8b7e0b…`, checked by a test) to
  the one on `dev/pmus-directional-allocation`.
  - Marginal EV per contract, after fill loss, fees, an optional calibrated
    reward and an inventory-concentration charge.
  - The favoured side has hysteresis. The lighter side is bought when its
    edge plus the risk reduction pays.
  - Reference freshness: a stale reference is refused, and an ageing one
    widens the band.
- **`src/l5_crypto_alloc_adapter.py`:**
  - `probability_band(reference, strike, tau_s, vol_per_sqrt_s, symbol,
    ref_age_s)`: the controller's own digital model
    Φ((ref − strike)/(σ√τ)), with a band from volatility uncertainty
    (±25%) and how far the reference can have moved since it was read. It
    is refused past `max_ref_age_s` (3 s, the reference model's own
    `MAX_AGE_S`). Symbols are BTC, ETH, SOL and XRP; ETH, SOL and XRP have
    floors but no recorded data here.
  - `probability_from_model(p_up, index_age_s)`: the same band from
    `l5_btc_reference` output (distance_to_strike, sd, sigma_per_second).
  - `filter_decision(decision, inventory, prob, …)`: takes
    `l5_btc5m_controller.decide()` output and judges its exposure-adding
    PLACE orders. Completions on the lighter side and cancels pass
    unchanged. It never adds an order or raises a price or size.
- **`analysis/l5/btc5m/allocator_replay.py` and
  `ALLOCATOR_REPLAY_RESULT.txt`:** the existing controller against the same
  controller plus the adapter, on the 187 recorded BTC5m windows with the
  same fill rule as `entry_filter_study`.

## Result (THEORETICAL replay on public quotes)

| | Existing | + allocator (fill loss 0.01) | + allocator (0.02) | additions only |
|---|---|---|---|---|
| Net, 187 windows | −$4.59 | −$4.28 | −$4.18 | −$4.33 |
| Paired per window | — | +0.17c [−0.7, +1.0] | +0.22c [−0.7, +1.1] | +0.14c [−0.7, +1.0] |

The allocator reduces residual losses (−1.61c to about −1.06c per window)
and loses some matched-pair income. The difference is not distinguishable
from zero. Neither policy is profitable before incentives.

## To wire it (for the crypto development session; not done here)

In the runner, after `C.decide(...)`:

```python
prob = AD.probability_from_model(p_up, index_age_s=age)
d = AD.filter_decision(d, inventory=inv, prob=prob, fill_loss=...)
```

Put it behind a default-off flag, then add the image COPY lines for both
modules and verify the image as in the sports RELEASE_CHECKLIST.
