# PMUS incentive break-even (empirical, credited rebates only)

Owner directive #167 5574408755 item 3. Read-only, offline. Labels: OBSERVED = a venue credit; RECOVERED = a print price from our settlement records; DERIVED = arithmetic on observed numbers; UNPROVEN = advertised, never credited, worth $0.00 here. Machine-readable: `pmus_incentive_breakeven.json`.

## Verdict

At our sizes (3-5.7 shares, $1.5-2.6 a leg) the venue has credited exactly $0.01 or $0.02 per print and never more, so a complete pair collects at most $0.04 of credited rebate. Above a pair cost of 1.008 a COMPLETE loses money at every size and price studied even with every observed credit counted; at 5 shares the ceiling is 1.004 (p1 0.38, one cent a leg) to 1.008 (p1 0.45-0.50, two cents a leg), and on the 0.005 price grid that is 1.000 or 1.005. A 1.01 pair at 5 shares loses $0.01-0.03; a 1.02 pair loses $0.06-0.08. The most any pair above par can net from credited rebates is $0.015 (5 shares at 1.005 with two-cent legs); with one-cent legs the same 1.005 pair already loses $0.005. Every advertised reward beyond the maker rebate (liquidity programs, taker rebates) has credited this account $0.00 and is worth $0.00 here. A rebate does not make a chase profitable: a COMPLETE above par is a loss before the residual is even considered.

## A. Empirical credit table (OBSERVED credits, RECOVERED prices)

| market | qty | venue px | src | qty x p(1-p) | notional $ | credit $ | model raw | model rounded | match |
|---|---|---|---|---|---|---|---|---|---|
| tsc-cfb-lou-miss | 3.1300 | 0.74 | exact | 0.6022 | 2.3162 | 0.0100 | 0.00753 | 0.01 | yes |
| tsc-cfb-lou-miss | 3.2800 | 0.77 | exact | 0.5809 | 2.5256 | 0.0100 | 0.00726 | 0.01 | yes |
| tsc-cfb-lou-miss | 3.2000 | 0.79 | exact | 0.5309 | 2.5280 | 0.0100 | 0.00664 | 0.01 | yes |
| asc-cfb-wisc-nd | 4.6400 | 0.3262 | derived | 1.0198 | 1.5136 | 0.0100 | 0.01275 | 0.01 | yes |
| asc-cfb-wisc-nd | 4.3300 | 0.3262 | derived | 0.9517 | 1.4124 | 0.0100 | 0.01190 | 0.01 | yes |
| asc-cfb-wisc-nd | 5.1500 | 0.3262 | derived | 1.1319 | 1.6799 | 0.0200 | 0.01415 | 0.01 | NO |
| asc-cfb-wisc-nd | 5.1500 | 0.3262 | derived | 1.1319 | 1.6799 | 0.0200 | 0.01415 | 0.01 | NO |
| asc-cfb-lou-miss | 3.9000 | 0.65 | exact | 0.8872 | 2.5350 | 0.0100 | 0.01109 | 0.01 | yes |
| tsc-cfb-lou-miss | 5.6800 | 0.48 | exact | 1.4177 | 2.7264 | 0.0200 | 0.01772 | 0.02 | yes |
| tsc-cfb-lou-miss | 5.4300 | 0.45 | elimination | 1.3439 | 2.4435 | 0.0200 | 0.01680 | 0.02 | yes |
| asc-cfb-lou-miss | 5.6800 | 0.44 | exact | 1.3996 | 2.4992 | 0.0200 | 0.01749 | 0.02 | yes |
| asc-cfb-lou-miss | 5.6800 | 0.48 | exact | 1.4177 | 2.7264 | 0.0200 | 0.01772 | 0.02 | yes |
| asc-cfb-lou-miss | 5.3100 | 0.47 | exact | 1.3227 | 2.4957 | 0.0200 | 0.01653 | 0.02 | yes |
| asc-cfb-lou-miss | 5.5500 | 0.45 | exact | 1.3736 | 2.4975 | 0.0200 | 0.01717 | 0.02 | yes |
| tsc-cfb-lou-miss | 5.4300 | 0.47 | elimination | 1.3526 | 2.5521 | 0.0200 | 0.01691 | 0.02 | yes |
| tsc-cfb-lou-miss | 5.4300 | 0.46 | exact | 1.3488 | 2.4978 | 0.0200 | 0.01686 | 0.02 | yes |
| asc-cfb-lou-miss | 5.5500 | 0.47 | exact | 1.3825 | 2.6085 | 0.0200 | 0.01728 | 0.02 | yes |
| aec-atpcq-aleher-tundur | 2.5200 | 0.78 | exact | 0.4324 | 0.5544 | 0.0100 | 0.00541 | 0.01 | yes |
| aec-atpcq-aleher-tundur | 3.7200 | 0.49 | exact | 0.9296 | 1.8228 | 0.0100 | 0.01162 | 0.01 | yes |
| aec-atpcq-aleher-tundur | 5.2000 | 0.49 | exact | 1.2995 | 2.6520 | 0.0200 | 0.01624 | 0.02 | yes |
| aec-atpcq-aleher-tundur | 5.3100 | 0.47 | exact | 1.3227 | 2.4957 | 0.0200 | 0.01653 | 0.02 | yes |
| aec-atpcq-norgom-ollwal | 5.0100 | 0.49 | exact | 1.2520 | 2.5551 | 0.0200 | 0.01565 | 0.02 | yes |
| aec-atpcq-norgom-ollwal | 4.9000 | 0.51 | exact | 1.2245 | 2.4990 | 0.0200 | 0.01531 | 0.02 | yes |
| aec-atpcq-norgom-ollwal | 5.0000 | 0.49 | exact | 1.2495 | 2.5500 | 0.0200 | 0.01562 | 0.02 | yes |
| aec-atpcq-norgom-ollwal | 5.3100 | 0.47 | exact | 1.3227 | 2.4957 | 0.0200 | 0.01653 | 0.02 | yes |

Uncredited prints (OBSERVED $0.00): 0.11 sh @0.50 (raw 0.00034); 1.38 sh @0.49 (raw 0.00431); 0.72 sh @0.78 (raw 0.00154); 0.20 sh @0.49 (raw 0.00062); 1.41 sh @0.50 (raw 0.00441); 1.00 sh @0.79 (raw 0.00207).

Fits (DERIVED, pooled over 25 credits, $0.4200): credit per notional dollar 0.00739 (notional $56.8624); per share 0.00351; per unit of qty x p(1-p) 0.01488 vs published 0.0125. The rounded formula reproduces 23/25 credits (19/19 of the exactly-priced ones); the mismatches are the wisc-nd prints priced at the market average (per-print prices not recoverable), where a $0.02 credit on 5.15 sh implies p(1-p) >= 0.233, i.e. a print at or above 0.37.

Credit size vs print size (OBSERVED): $0.0100: 8 prints, 2.5200-4.6400 sh, $0.5544-2.5350 notional; $0.0200: 17 prints, 4.9000-5.6800 sh, $2.4435-2.7264 notional.

### Rounding / minimum-credit behaviour (DERIVED, confirmed by the credits)

credit = round_half_even(0.0125 x qty x p x (1-p), cent); a print pays $0.00 below $0.005 raw, $0.01 from $0.005, $0.02 from $0.015, $0.03 from $0.025.

| px | raw $/share | sh for 1c | sh for 2c | sh for 3c | notional for 1c | notional for 2c | notional for 3c |
|---|---|---|---|---|---|---|---|
| 0.20 | 0.002000 | 2.50 | 7.50 | 12.50 | 0.500 | 1.500 | 2.500 |
| 0.22 | 0.002145 | 2.33 | 6.99 | 11.66 | 0.513 | 1.538 | 2.564 |
| 0.30 | 0.002625 | 1.90 | 5.71 | 9.52 | 0.571 | 1.714 | 2.857 |
| 0.38 | 0.002945 | 1.70 | 5.09 | 8.49 | 0.645 | 1.935 | 3.226 |
| 0.45 | 0.003094 | 1.62 | 4.85 | 8.08 | 0.727 | 2.182 | 3.636 |
| 0.47 | 0.003114 | 1.61 | 4.82 | 8.03 | 0.755 | 2.264 | 3.774 |
| 0.50 | 0.003125 | 1.60 | 4.80 | 8.00 | 0.800 | 2.400 | 4.000 |

Effective rate at our sizes: 3-4.6 sh: 8 prints, $0.0800 on $15.21 = 0.00526/$ ($0.0100 a print); 4.9-5.7 sh: 17 prints, $0.3400 on $41.65 = 0.00816/$ ($0.0200 a print).

## B. Break-even completion ceiling c* = p1 + p2 (DERIVED, empirical credits)

c* = max pair cost with net >= 0 (and >= $0.01) on a 0.001 scan; _grid = largest executable price on the 0.005 grid at or below c*; smooth = the unrounded formula for comparison; rows with within_observed_sizes false (6.5 sh) extrapolate beyond any print the venue has credited us

| qty | p1 | leg credit | c* net>=0 | grid | c* net>=1c | grid | smooth net>=0 | smooth net>=1c | in observed sizes |
|---|---|---|---|---|---|---|---|---|---|
| 3 | 0.20 | 0.01 | 1.006 | 1.005 | 1.003 | 1.000 | 1.003 | 1.000 | yes |
| 3 | 0.30 | 0.01 | 1.006 | 1.005 | 1.003 | 1.000 | 1.005 | 1.001 | yes |
| 3 | 0.38 | 0.01 | 1.006 | 1.005 | 1.003 | 1.000 | 1.005 | 1.002 | yes |
| 3 | 0.45 | 0.01 | 1.006 | 1.005 | 1.003 | 1.000 | 1.006 | 1.002 | yes |
| 3 | 0.50 | 0.01 | 1.006 | 1.005 | 1.003 | 1.000 | 1.006 | 1.002 | yes |
| 4 | 0.20 | 0.01 | 1.005 | 1.005 | 1.002 | 1.000 | 1.003 | 1.001 | yes |
| 4 | 0.30 | 0.01 | 1.005 | 1.005 | 1.002 | 1.000 | 1.005 | 1.002 | yes |
| 4 | 0.38 | 0.01 | 1.005 | 1.005 | 1.002 | 1.000 | 1.005 | 1.003 | yes |
| 4 | 0.45 | 0.01 | 1.005 | 1.005 | 1.002 | 1.000 | 1.006 | 1.003 | yes |
| 4 | 0.50 | 0.01 | 1.005 | 1.005 | 1.002 | 1.000 | 1.006 | 1.003 | yes |
| 5 | 0.20 | 0.01 | 1.004 | 1.000 | 1.002 | 1.000 | 1.003 | 1.001 | yes |
| 5 | 0.30 | 0.01 | 1.004 | 1.000 | 1.002 | 1.000 | 1.005 | 1.003 | yes |
| 5 | 0.38 | 0.01 | 1.004 | 1.000 | 1.002 | 1.000 | 1.005 | 1.003 | yes |
| 5 | 0.45 | 0.02 | 1.008 | 1.005 | 1.006 | 1.005 | 1.006 | 1.004 | yes |
| 5 | 0.50 | 0.02 | 1.008 | 1.005 | 1.006 | 1.005 | 1.006 | 1.004 | yes |
| 6.5 | 0.20 | 0.01 | 1.003 | 1.000 | 1.001 | 1.000 | 1.003 | 1.002 | NO (extrapolated) |
| 6.5 | 0.30 | 0.02 | 1.006 | 1.005 | 1.004 | 1.000 | 1.005 | 1.003 | NO (extrapolated) |
| 6.5 | 0.38 | 0.02 | 1.006 | 1.005 | 1.004 | 1.000 | 1.005 | 1.004 | NO (extrapolated) |
| 6.5 | 0.45 | 0.02 | 1.006 | 1.005 | 1.004 | 1.000 | 1.006 | 1.004 | NO (extrapolated) |
| 6.5 | 0.50 | 0.02 | 1.006 | 1.005 | 1.004 | 1.000 | 1.006 | 1.004 | NO (extrapolated) |

## C. Net dollars per pair, q = 5 at the R4 first-leg prices (DERIVED)

| qty | p1 | p2 | pair cost | pair cash $ | credited rebate $ | unproven $ | net $ | smooth rebate $ | smooth net $ | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 5 | 0.38 | 0.60 | 0.98 | 0.1000 | 0.03 | 0.00 | 0.1300 | 0.0297 | 0.1297 | profit |
| 5 | 0.38 | 0.61 | 0.99 | 0.0500 | 0.02 | 0.00 | 0.0700 | 0.0296 | 0.0796 | profit |
| 5 | 0.38 | 0.62 | 1.00 | 0.0000 | 0.02 | 0.00 | 0.0200 | 0.0294 | 0.0294 | profit |
| 5 | 0.38 | 0.63 | 1.01 | -0.0500 | 0.02 | 0.00 | -0.0300 | 0.0293 | -0.0207 | LOSS |
| 5 | 0.38 | 0.64 | 1.02 | -0.1000 | 0.02 | 0.00 | -0.0800 | 0.0291 | -0.0709 | LOSS |
| 5 | 0.38 | 0.65 | 1.03 | -0.1500 | 0.02 | 0.00 | -0.1300 | 0.0289 | -0.1211 | LOSS |
| 5 | 0.38 | 0.67 | 1.05 | -0.2500 | 0.02 | 0.00 | -0.2300 | 0.0285 | -0.2215 | LOSS |
| 5 | 0.47 | 0.51 | 0.98 | 0.1000 | 0.04 | 0.00 | 0.1400 | 0.0312 | 0.1312 | profit |
| 5 | 0.47 | 0.52 | 0.99 | 0.0500 | 0.04 | 0.00 | 0.0900 | 0.0312 | 0.0812 | profit |
| 5 | 0.47 | 0.53 | 1.00 | 0.0000 | 0.04 | 0.00 | 0.0400 | 0.0311 | 0.0311 | profit |
| 5 | 0.47 | 0.54 | 1.01 | -0.0500 | 0.04 | 0.00 | -0.0100 | 0.0311 | -0.0189 | LOSS |
| 5 | 0.47 | 0.55 | 1.02 | -0.1000 | 0.04 | 0.00 | -0.0600 | 0.0310 | -0.0690 | LOSS |
| 5 | 0.47 | 0.56 | 1.03 | -0.1500 | 0.04 | 0.00 | -0.1100 | 0.0310 | -0.1190 | LOSS |
| 5 | 0.47 | 0.58 | 1.05 | -0.2500 | 0.04 | 0.00 | -0.2100 | 0.0308 | -0.2192 | LOSS |

## D. Unproven rewards (value $0.00 in every break-even)

| reward | advertised | observed on this account | credited $ | in break-even $ | label |
|---|---|---|---|---|---|
| liquidityProgram rewards (/v1/incentives/earnings) | resting-order rewards from per-market reward pools (e.g. us_open_props 400/600, atp_wta_futures 500, atp_player_props 100 reward_pool, target_size 10000) | 4 earnings rows, $0.22 total, dated 2026-08-23/24, every row status SKIPPED; 0 credit rows in the full activity walk (the only non-trade row is a $10 deposit TRANSFER); our_markets_in_programs = 0 in every audit | 0.00 | 0.00 | UNPROVEN |
| taker rebates | L5's history shows +$7,323 of taker rebates; no PMUS taker-rebate program is published for our markets | none: we are maker-only (19/19 R3L, all R4 fills passiveExecution); venue_charged_us_on_our_fills_usd 0.0000 | 0.00 | 0.00 | UNPROVEN |
| published liquidity programs covering our filled markets | 20,000-row (truncated) active program list: atp/wta props and futures; MLB 0 of 3,101 markets with any period | published_programs_covering_our_markets = 0 in all five audits | 0.00 | 0.00 | UNPROVEN |
| maker rebate below the cent floor | 0.0125 x C x p x (1-p) on every maker fill | 6 of our 31 at-size prints (0.11-1.41 shares) and every earlier sub-floor print were credited $0.00 (166 venue trades in the final audit walk, 25 with a credit): the venue rounds the per-print rebate to the cent and pays nothing below $0.005 | 0.00 | 0.00 | OBSERVED ZERO |

## E. Previous estimate

l5_profitability F_break_even: 0.789% of filled dollars (0.808% at R3L) applied smoothly per pair: 1.008. DERIVED smooth-rate estimate; consistent with the two-cents-a-leg case only (q 4.9-5.7, p 0.44-0.56). At 5 sh / p 0.38 the empirical ceiling is 1.004 because the leg credit rounds DOWN to one cent; the rate is a step, not a slope.
