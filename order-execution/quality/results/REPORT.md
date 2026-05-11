# Execution Quality Report—`live`
Slice: **all rows**
Trials: **184**  ·  runs: **3**  ·  instruments: **8**

## Coverage (trials per instrument × strategy)
| instrument       |   LMT_MID |   MIDPRICE_NATIVE |   MKT_ADAPTIVE |   MKT_RAW |
|:-----------------|----------:|------------------:|---------------:|----------:|
| AAPL/STK         |         4 |                 4 |              4 |        20 |
| DX/FUT/20260615  |         2 |                 2 |              2 |         6 |
| ES/FUT/20260618  |         4 |                 4 |              4 |        16 |
| EUR/CASH         |         4 |                 4 |              4 |        12 |
| PRIM/STK         |         4 |                 4 |              4 |        14 |
| SPY/STK          |         4 |                 4 |              4 |        20 |
| USD/CFD          |         2 |                 2 |              2 |         4 |
| VIX/FUT/20260519 |         4 |                 4 |              4 |         8 |

## Fill-quality (status distribution per strategy)
| strategy_label   |   FILLED |   TIMEOUT |   SKIPPED |
|:-----------------|---------:|----------:|----------:|
| LMT_MID          |       22 |         2 |         4 |
| MIDPRICE_NATIVE  |       12 |         0 |        16 |
| MKT_ADAPTIVE     |       18 |         4 |         6 |
| MKT_RAW          |      100 |         0 |         0 |

## Slippage distribution—`slip_vs_mid_t0_bps` (FILLED only)
| strategy_label   |   count |   median |     p90 |
|:-----------------|--------:|---------:|--------:|
| LMT_MID          |      22 |  -0.0851 |  0.4383 |
| MIDPRICE_NATIVE  |      12 |  -0.5299 |  1.1771 |
| MKT_ADAPTIVE     |      16 |  -0.1547 |  2.8935 |
| MKT_RAW          |      90 |   0.1892 | 12.87   |

## Slippage distribution—`slip_vs_vwap_bps` (FILLED only)
_Reported only—primary ranking still uses `slip_vs_mid_t0_bps`. Null when VWAP unavailable._

| strategy_label   |   count |   median |   p90 |
|:-----------------|--------:|---------:|------:|
| LMT_MID          |       1 |    0.069 | 0.069 |

## Time-to-fill distribution—`time_to_fill_s` (FILLED only)
| strategy_label   |   count |   median |    p90 |
|:-----------------|--------:|---------:|-------:|
| LMT_MID          |      22 |   1.3814 | 2.8874 |
| MIDPRICE_NATIVE  |      12 |   1.055  | 2.7373 |
| MKT_ADAPTIVE     |      18 |   1.5057 | 3.817  |
| MKT_RAW          |     100 |   1.0042 | 1.0052 |

## T0 spread per instrument (realistic-cost lower bound)
| instrument       |   median_spread_bps |   p90_spread_bps |   n |
|:-----------------|--------------------:|-----------------:|----:|
| AAPL/STK         |              1.4055 |           2.8188 |  32 |
| ES/FUT/20260618  |              0.3417 |           0.3427 |  24 |
| EUR/CASH         |              0.0855 |           0.171  |  16 |
| PRIM/STK         |             42.3376 |          64.5639 |  26 |
| SPY/STK          |              0.2761 |           0.4141 |  32 |
| VIX/FUT/20260519 |             25.8732 |          25.9403 |  16 |

## Commission per instrument (FILLED only)
Raw commission in `commission_currency`. `median_commission_bps` = commission / notional × 1e4. When `commission_currency` differs from `contract.currency`, both are converted to USD using `cost_tables/fx_rates.json` (`fx_converted=True` flags those rows). Edit the JSON when rates drift.

| instrument       |   median_commission | currency   |   median_commission_bps | fx_converted   |   n |
|:-----------------|--------------------:|:-----------|------------------------:|:---------------|----:|
| AAPL/STK         |              0.3543 | USD        |                 12.4909 | False          |  32 |
| DX/FUT/20260615  |              2.22   | USD        |                  0.227  | False          |   8 |
| ES/FUT/20260618  |              2.25   | USD        |                  0.0615 | False          |  24 |
| EUR/CASH         |              1.567  | CHF        |                  0.6415 | True           |  16 |
| PRIM/STK         |              0.3517 | USD        |                 31.6634 | False          |  24 |
| SPY/STK          |              0.3587 | USD        |                  4.9402 | False          |  32 |
| USD/CFD          |              1.5662 | CHF        |                  0.8033 | False          |   4 |
| VIX/FUT/20260519 |              2.38   | USD        |                  1.2284 | False          |  12 |

## Per-instrument recommendation (primary)
Ranking: lowest median `slip_vs_mid_t0_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`.

|    | instrument       | best_strategy   |   median_slip_bps |   median_ttf_s |   n_fills |
|---:|:-----------------|:----------------|------------------:|---------------:|----------:|
|  0 | AAPL/STK         | MIDPRICE_NATIVE |           -2.0278 |         0.8795 |         4 |
|  1 | ES/FUT/20260618  | LMT_MID         |           -0.1708 |         1.3801 |         4 |
|  2 | EUR/CASH         | LMT_MID         |            0.0216 |         1.3809 |         4 |
|  3 | PRIM/STK         | MIDPRICE_NATIVE |           -2.8019 |         2.6358 |         4 |
|  4 | SPY/STK          | MIDPRICE_NATIVE |           -0.1376 |         1.055  |         4 |
|  5 | VIX/FUT/20260519 | MKT_ADAPTIVE    |           12.87   |         1.256  |         1 |

## Per-instrument recommendation (VWAP secondary view)
Ranking: lowest median `slip_vs_vwap_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`. Excludes cells without VWAP. _Reported, not used for primary ranking._

|    | instrument   | best_strategy   |   median_slip_bps |   median_ttf_s |   n_fills |
|---:|:-------------|:----------------|------------------:|---------------:|----------:|
|  0 | SPY/STK      | LMT_MID         |             0.069 |         1.2541 |         1 |

---
_Source: `trials_live.parquet` · primary metric: `slip_vs_mid_t0_bps` · tiebreak: `time_to_fill_s`_
