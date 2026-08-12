# Execution Quality Report—`live`
Slice: **all rows**
Trials: **389**  ·  runs: **33**  ·  instruments: **12**

## Coverage (trials per instrument × strategy)
| instrument       |   LMT_MID |   MIDPRICE_NATIVE |   MKT_ADAPTIVE |   MKT_RAW |
|:-----------------|----------:|------------------:|---------------:|----------:|
| AAPL/STK         |         4 |                 4 |              4 |        20 |
| BBSI/STK         |         2 |                 2 |              2 |         4 |
| CHSPI/STK        |        16 |                 4 |             16 |        27 |
| CSP1/STK         |        10 |                 4 |             16 |        31 |
| DX/FUT/20260615  |         2 |                 2 |              2 |         6 |
| ES/FUT/20260618  |         4 |                 4 |              4 |        16 |
| EUR/CASH         |         4 |                 4 |              4 |        12 |
| PRIM/STK         |         4 |                 4 |              4 |        14 |
| SPY/STK          |         4 |                 4 |              4 |        20 |
| SXR8/STK         |        10 |                 4 |             16 |        41 |
| USD/CFD          |         2 |                 2 |              2 |         4 |
| VIX/FUT/20260519 |         4 |                 4 |              4 |         8 |

## Fill-quality (status distribution per strategy)
| strategy_label   |   FILLED |   TIMEOUT |   SKIPPED |
|:-----------------|---------:|----------:|----------:|
| LMT_MID          |       41 |        21 |         4 |
| MIDPRICE_NATIVE  |       13 |         1 |        28 |
| MKT_ADAPTIVE     |       41 |        31 |         6 |
| MKT_RAW          |      202 |         1 |         0 |

## Slippage distribution—`slip_vs_mid_t0_bps` (FILLED only)
| strategy_label   |   count |   median |    p90 |
|:-----------------|--------:|---------:|-------:|
| LMT_MID          |      41 |  -0.0851 | 0.2564 |
| MIDPRICE_NATIVE  |      13 |  -0.1762 | 1.0463 |
| MKT_ADAPTIVE     |      39 |  -0      | 2.5369 |
| MKT_RAW          |     192 |   0.5332 | 6.7756 |

## Slippage distribution—`slip_vs_vwap_bps` (FILLED only)
_Reported only—primary ranking still uses `slip_vs_mid_t0_bps`. Null when VWAP unavailable._

| strategy_label   |   count |   median |    p90 |
|:-----------------|--------:|---------:|-------:|
| LMT_MID          |       3 |        0 | 0.0552 |

## Time-to-fill distribution—`time_to_fill_s` (FILLED only)
| strategy_label   |   count |   median |     p90 |
|:-----------------|--------:|---------:|--------:|
| LMT_MID          |      41 |   1.5063 | 10.7494 |
| MIDPRICE_NATIVE  |      13 |   1.0054 |  2.7119 |
| MKT_ADAPTIVE     |      41 |   3.2645 | 18.3327 |
| MKT_RAW          |     202 |   1.0045 |  1.2558 |

## T0 spread per instrument (realistic-cost lower bound)
| instrument       |   median_spread_bps |   p90_spread_bps |   n |
|:-----------------|--------------------:|-----------------:|----:|
| AAPL/STK         |              1.4055 |           2.8188 |  32 |
| BBSI/STK         |            117.885  |         134.816  |  10 |
| CHSPI/STK        |              3.4636 |           4.6243 |  59 |
| CSP1/STK         |              2.0995 |           2.4261 |  57 |
| ES/FUT/20260618  |              0.3417 |           0.3427 |  24 |
| EUR/CASH         |              0.0855 |           0.171  |  16 |
| PRIM/STK         |             42.3376 |          64.5639 |  26 |
| SPY/STK          |              0.2761 |           0.4141 |  32 |
| SXR8/STK         |              0.553  |           1.3785 |  67 |
| VIX/FUT/20260519 |             25.8732 |          25.9403 |  16 |

## Commission per instrument (FILLED only)
Raw commission in `commission_currency`. `median_commission_bps` = commission / notional × 1e4. When `commission_currency` differs from `contract.currency`, both are converted to USD using `cost_tables/fx_rates.json` (`fx_converted=True` flags those rows). Edit the JSON when rates drift.

| instrument       |   median_commission | currency   |   median_commission_bps | fx_converted   |   n |
|:-----------------|--------------------:|:-----------|------------------------:|:---------------|----:|
| AAPL/STK         |              0.3543 | USD        |                 12.4909 | False          |  32 |
| BBSI/STK         |              0.2977 | USD        |                100.278  | False          |   8 |
| CHSPI/STK        |              1.5    | CHF        |                 86.5701 | False          |  34 |
| CSP1/STK         |              1      | GBP        |                 16.1755 | False          |  42 |
| DX/FUT/20260615  |              2.22   | USD        |                  0.227  | False          |   8 |
| ES/FUT/20260618  |              2.25   | USD        |                  0.0615 | False          |  24 |
| EUR/CASH         |              1.567  | CHF        |                  0.6415 | True           |  16 |
| PRIM/STK         |              0.3517 | USD        |                 31.6634 | False          |  24 |
| SPY/STK          |              0.3587 | USD        |                  4.9402 | False          |  32 |
| SXR8/STK         |              1.25   | EUR        |                 17.2819 | False          |  61 |
| USD/CFD          |              1.5662 | CHF        |                  0.8033 | False          |   4 |
| VIX/FUT/20260519 |              2.38   | USD        |                  1.2284 | False          |  12 |

## Per-instrument recommendation (primary)
Ranking: lowest median `slip_vs_mid_t0_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`.

|    | instrument       | best_strategy   |   median_slip_bps |   median_ttf_s |   n_fills |
|---:|:-----------------|:----------------|------------------:|---------------:|----------:|
|  0 | AAPL/STK         | MIDPRICE_NATIVE |           -2.0278 |         0.8795 |         4 |
|  1 | BBSI/STK         | MIDPRICE_NATIVE |           -0      |         1.0036 |         1 |
|  2 | CHSPI/STK        | LMT_MID         |           -0      |         8.3932 |         3 |
|  3 | CSP1/STK         | LMT_MID         |            0      |        10.7462 |         5 |
|  4 | ES/FUT/20260618  | LMT_MID         |           -0.1708 |         1.3801 |         4 |
|  5 | EUR/CASH         | LMT_MID         |            0.0216 |         1.3809 |         4 |
|  6 | PRIM/STK         | MIDPRICE_NATIVE |           -2.8019 |         2.6358 |         4 |
|  7 | SPY/STK          | MIDPRICE_NATIVE |           -0.1376 |         1.055  |         4 |
|  8 | SXR8/STK         | LMT_MID         |           -0.138  |         1.8844 |        10 |
|  9 | VIX/FUT/20260519 | MKT_ADAPTIVE    |           12.87   |         1.256  |         1 |

## Per-instrument recommendation (VWAP secondary view)
Ranking: lowest median `slip_vs_vwap_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`. Excludes cells without VWAP. _Reported, not used for primary ranking._

|    | instrument   | best_strategy   |   median_slip_bps |   median_ttf_s |   n_fills |
|---:|:-------------|:----------------|------------------:|---------------:|----------:|
|  0 | SPY/STK      | LMT_MID         |             0.069 |         1.2541 |         1 |
|  1 | SXR8/STK     | LMT_MID         |             0     |         1.1298 |         2 |

---
_Source: `trials_live.parquet` · primary metric: `slip_vs_mid_t0_bps` · tiebreak: `time_to_fill_s`_
