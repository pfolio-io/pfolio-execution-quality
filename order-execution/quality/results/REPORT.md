# Execution Quality Report—`paper`

Slice: **all rows**
Trials: **1670**  · runs: **31**  · instruments: **11**

## Coverage (trials per instrument × strategy)

| instrument       | LMT_MID | MIDPRICE_NATIVE | MKT_ADAPTIVE | MKT_RAW |
|:-----------------|--------:|----------------:|-------------:|--------:|
| AAPL/STK         |      33 |              32 |           32 |     101 |
| DX/FUT/20260615  |      24 |              24 |           24 |      59 |
| EFA/STK          |      28 |              28 |           28 |      92 |
| ES/FUT/20260618  |      33 |              32 |           32 |      88 |
| EUR/CASH         |      29 |              29 |           29 |      63 |
| LQD/STK          |      28 |              28 |           28 |      82 |
| PRIM/STK         |      24 |              24 |           24 |      59 |
| SPY/STK          |      29 |              29 |           29 |      98 |
| USD/CFD          |      24 |              24 |           24 |      25 |
| VIX/FUT/20260513 |      29 |              29 |           29 |      33 |
| VIX/FUT/20260519 |      24 |              24 |           24 |      59 |

## Fill-quality (status distribution per strategy)

| strategy_label  | FILLED | TIMEOUT | CANCELLED | SKIPPED |
|:----------------|-------:|--------:|----------:|--------:|
| LMT_MID         |    180 |      52 |         1 |      72 |
| MIDPRICE_NATIVE |     90 |      49 |         0 |     164 |
| MKT_ADAPTIVE    |    104 |     144 |         0 |      55 |
| MKT_RAW         |    691 |      66 |         0 |       2 |

## Slippage distribution—`slip_vs_mid_t0_bps` (FILLED only)

| strategy_label  | count |  median |     p90 |
|:----------------|------:|--------:|--------:|
| LMT_MID         |   180 | -0.1548 |  3.3101 |
| MIDPRICE_NATIVE |    90 | -0.4762 |  5.9339 |
| MKT_ADAPTIVE    |    88 |       0 |   1.407 |
| MKT_RAW         |   632 |  0.4902 | 13.0137 |

## Slippage distribution—`slip_vs_vwap_bps` (FILLED only)

_Reported only—primary ranking still uses `slip_vs_mid_t0_bps`. Null when VWAP unavailable._

| strategy_label  | count |  median |     p90 |
|:----------------|------:|--------:|--------:|
| LMT_MID         |    10 |      -0 |  3.0769 |
| MIDPRICE_NATIVE |     4 | -0.0454 |      -0 |
| MKT_ADAPTIVE    |     3 |  0.0055 |  0.0303 |
| MKT_RAW         |    12 |  0.2781 | 22.4383 |

## Time-to-fill distribution—`time_to_fill_s` (FILLED only)

| strategy_label  | count | median |     p90 |
|:----------------|------:|-------:|--------:|
| LMT_MID         |   180 | 1.2606 | 14.1134 |
| MIDPRICE_NATIVE |    90 | 7.9767 | 28.1521 |
| MKT_ADAPTIVE    |   104 | 2.1334 | 14.4424 |
| MKT_RAW         |   691 | 1.0058 |  1.2598 |

## T0 spread per instrument (realistic-cost lower bound)

| instrument       | median_spread_bps | p90_spread_bps |   n |
|:-----------------|------------------:|---------------:|----:|
| AAPL/STK         |            1.7866 |         4.2776 | 182 |
| EFA/STK          |            0.9846 |         1.9703 | 176 |
| ES/FUT/20260618  |            0.3436 |         0.3469 | 153 |
| EUR/CASH         |            0.1708 |         0.2563 |  92 |
| LQD/STK          |            0.9207 |         1.8459 | 166 |
| PRIM/STK         |           35.9657 |        60.9348 | 131 |
| SPY/STK          |            0.2769 |         0.5532 | 185 |
| VIX/FUT/20260513 |           3021.58 |        3021.58 |  31 |
| VIX/FUT/20260519 |           25.6082 |        25.9403 | 107 |

## Commission per instrument (FILLED only)

Raw commission in `commission_currency`. `median_commission_bps` = commission / notional × 1e4. When
`commission_currency` differs from `contract.currency`, both are converted to USD using `cost_tables/fx_rates.json` (
`fx_converted=True` flags those rows). Edit the JSON when rates drift.

| instrument       | median_commission | currency | median_commission_bps | fx_converted |   n |
|:-----------------|------------------:|:---------|----------------------:|:-------------|----:|
| AAPL/STK         |             1.003 | USD      |               35.6794 | False        | 138 |
| DX/FUT/20260615  |              2.22 | USD      |                0.2259 | False        |  68 |
| EFA/STK          |            1.0011 | USD      |                98.311 | False        | 128 |
| ES/FUT/20260618  |              2.25 | USD      |                0.0618 | False        | 108 |
| EUR/CASH         |            1.5678 | CHF      |                0.8039 | True         |  68 |
| LQD/STK          |            1.0012 | USD      |               92.1782 | False        | 108 |
| PRIM/STK         |             1.002 | USD      |               49.5935 | False        |  68 |
| SPY/STK          |            1.0075 | USD      |               13.9769 | False        | 138 |
| VIX/FUT/20260513 |              2.38 | USD      |                  1.19 | False        |   4 |
| VIX/FUT/20260519 |              2.38 | USD      |                1.2205 | False        |  68 |

## Per-instrument recommendation (primary)

Ranking: lowest median `slip_vs_mid_t0_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`.

|   | instrument       | best_strategy   | median_slip_bps | median_ttf_s | n_fills |
|--:|:-----------------|:----------------|----------------:|-------------:|--------:|
| 0 | AAPL/STK         | MIDPRICE_NATIVE |         -0.5312 |       7.6672 |      26 |
| 1 | EFA/STK          | MIDPRICE_NATIVE |         -0.9882 |       6.9035 |      24 |
| 2 | ES/FUT/20260618  | LMT_MID         |         -0.1721 |       1.2587 |      29 |
| 3 | EUR/CASH         | LMT_MID         |         -0.2136 |       1.2587 |      27 |
| 4 | LQD/STK          | MIDPRICE_NATIVE |         -0.4614 |      13.5536 |      11 |
| 5 | PRIM/STK         | MIDPRICE_NATIVE |         -2.4459 |      28.3799 |       3 |
| 6 | SPY/STK          | MIDPRICE_NATIVE |         -0.1387 |       9.4376 |      26 |
| 7 | VIX/FUT/20260513 | MKT_RAW         |         1510.79 |       1.0081 |       4 |
| 8 | VIX/FUT/20260519 | MKT_ADAPTIVE    |         -0.0328 |      10.4321 |       4 |

## Per-instrument recommendation (VWAP secondary view)

Ranking: lowest median `slip_vs_vwap_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`. Excludes cells
without VWAP. _Reported, not used for primary ranking._

|   | instrument       | best_strategy   | median_slip_bps | median_ttf_s | n_fills |
|--:|:-----------------|:----------------|----------------:|-------------:|--------:|
| 0 | AAPL/STK         | LMT_MID         |         -0.1828 |       1.2577 |       1 |
| 1 | EFA/STK          | MIDPRICE_NATIVE |              -0 |      13.9449 |       2 |
| 2 | ES/FUT/20260618  | LMT_MID         |         -0.0054 |      20.1375 |       4 |
| 3 | LQD/STK          | MKT_RAW         |               0 |       1.0037 |       1 |
| 4 | SPY/STK          | LMT_MID         |         -0.1392 |      14.0407 |       1 |
| 5 | VIX/FUT/20260519 | LMT_MID         |              -0 |       1.0061 |       4 |

---
_Source: `trials_paper.parquet` · primary metric: `slip_vs_mid_t0_bps` · tiebreak: `time_to_fill_s`_
