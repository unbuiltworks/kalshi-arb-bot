"""Production combo supervisor v20: time-guarded MLB game prepricing.

v19's zero-refetch MLB game prepricer accidentally bound the base Kalshi MLB
normalizer instead of the already-proven America/New_York scheduled-start guard.
Swap only that normalization function.  Manufacturing ranking changes; execution,
rule certification, q=1 gates, capital controls and settlement accounting do not.
"""
from src import sports_kp_live_combo_supervisor_v19 as v19
from src import sports_kp_mve_mlb_preprice as mlb_preprice
from src.kalshi_mlb_time_guard import normalize_kalshi_mlb_eastern

mlb_preprice.normalize_kalshi_mlb = normalize_kalshi_mlb_eastern

run = v19.run
main = v19.main

if __name__ == "__main__":
    main()
