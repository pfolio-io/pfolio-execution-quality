# Execution Quality Report — `paper`

Slice: **last-run=20260504T171243Z-b1a954d1**
Trials: **128**  · runs: **1**  · instruments: **11**

## Coverage (trials per instrument × strategy)

| instrument       | LMT_MID | MIDPRICE_NATIVE | MKT_ADAPTIVE | MKT_RAW |
|:-----------------|--------:|----------------:|-------------:|--------:|
| AAPL/STK         |       2 |               2 |            2 |       8 |
| DX/FUT/20260615  |       2 |               2 |            2 |       4 |
| EFA/STK          |       2 |               2 |            2 |       8 |
| ES/FUT/20260618  |       2 |               2 |            2 |       6 |
| EUR/CASH         |       2 |               2 |            2 |       6 |
| LQD/STK          |       2 |               2 |            2 |       8 |
| PRIM/STK         |       2 |               2 |            2 |       5 |
| SPY/STK          |       2 |               2 |            2 |       8 |
| USD/CFD          |       2 |               2 |            2 |       2 |
| VIX/FUT/20260513 |       2 |               2 |            2 |       2 |
| VIX/FUT/20260519 |       2 |               2 |            2 |       5 |

## Fill-quality (status distribution per strategy)

| strategy_label  | FILLED | TIMEOUT | SKIPPED |
|:----------------|-------:|--------:|--------:|
| LMT_MID         |     14 |       2 |       6 |
| MIDPRICE_NATIVE |      8 |       2 |      12 |
| MKT_ADAPTIVE    |      0 |      18 |       4 |
| MKT_RAW         |     58 |       4 |       0 |

## Slippage distribution — `slip_vs_mid_t0_bps` (FILLED only)

| strategy_label  | count | median |     p90 |
|:----------------|------:|-------:|--------:|
| LMT_MID         |    14 | -0.125 |  0.4859 |
| MIDPRICE_NATIVE |     8 | -3.176 | -0.3029 |
| MKT_RAW         |    54 | 0.5379 | 15.1702 |

## Slippage distribution — `slip_vs_vwap_bps` (FILLED only)

_Reported only — primary ranking still uses `slip_vs_mid_t0_bps`. Null when VWAP unavailable._

| strategy_label | count |  median |     p90 |
|:---------------|------:|--------:|--------:|
| LMT_MID        |     1 | -0.1392 | -0.1392 |
| MKT_RAW        |     1 |  0.4008 |  0.4008 |

## Time-to-fill distribution — `time_to_fill_s` (FILLED only)

| strategy_label  | count | median |     p90 |
|:----------------|------:|-------:|--------:|
| LMT_MID         |    14 | 2.1355 |  9.1828 |
| MIDPRICE_NATIVE |     8 | 9.2534 | 24.6345 |
| MKT_RAW         |    58 | 1.0062 |   1.585 |

## T0 spread per instrument (realistic-cost lower bound)

| instrument       | median_spread_bps | p90_spread_bps |  n |
|:-----------------|------------------:|---------------:|---:|
| AAPL/STK         |            0.9023 |         1.4442 | 14 |
| EFA/STK          |            0.9941 |         0.9943 | 14 |
| ES/FUT/20260618  |            0.3461 |         0.6921 | 10 |
| EUR/CASH         |            0.1711 |         0.2566 |  8 |
| LQD/STK          |            0.9251 |         0.9251 | 14 |
| PRIM/STK         |           29.2619 |        33.4755 | 11 |
| SPY/STK          |            0.2783 |         0.3759 | 14 |
| VIX/FUT/20260519 |           25.2207 |        25.2207 |  9 |

## Commission per instrument (FILLED only)

Raw commission in `commission_currency`. `median_commission_bps` = commission / (qty × price × multiplier) × 1e4,
computed only when `commission_currency == contract.currency` (no FX conversion yet).

| instrument       | median_commission | currency | median_commission_bps |  n |
|:-----------------|------------------:|:---------|----------------------:|---:|
| AAPL/STK         |             1.003 | USD      |               36.2012 | 12 |
| DX/FUT/20260615  |              2.22 | USD      |                0.2258 |  4 |
| EFA/STK          |            1.0011 | USD      |               99.4187 | 12 |
| ES/FUT/20260618  |              2.25 | USD      |                0.0622 |  8 |
| EUR/CASH         |            1.5638 | CHF      |                   nan |  8 |
| LQD/STK          |            1.0012 | USD      |               92.5936 | 12 |
| PRIM/STK         |             1.002 | USD      |               54.2049 |  6 |
| SPY/STK          |            1.0075 | USD      |               14.0318 | 12 |
| VIX/FUT/20260519 |              2.38 | USD      |                1.2005 |  6 |

## Per-instrument recommendation (primary)

Ranking: lowest median `slip_vs_mid_t0_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`.

|   | instrument       | best_strategy   | median_slip_bps | median_ttf_s | n_fills |
|--:|:-----------------|:----------------|----------------:|-------------:|--------:|
| 0 | AAPL/STK         | MIDPRICE_NATIVE |         -6.6818 |      11.7422 |       2 |
| 1 | EFA/STK          | MIDPRICE_NATIVE |         -11.929 |       5.1579 |       2 |
| 2 | ES/FUT/20260618  | LMT_MID         |         -7.6995 |       1.0066 |       2 |
| 3 | EUR/CASH         | LMT_MID         |         -2.4382 |        3.391 |       2 |
| 4 | LQD/STK          | MIDPRICE_NATIVE |         -2.7753 |      27.1631 |       2 |
| 5 | PRIM/STK         | LMT_MID         |         -19.508 |        1.259 |       1 |
| 6 | SPY/STK          | MIDPRICE_NATIVE |         -7.5264 |       5.5465 |       2 |
| 7 | VIX/FUT/20260519 | LMT_MID         |        -62.5782 |       0.7555 |       1 |

## Per-instrument recommendation (VWAP secondary view)

Ranking: lowest median `slip_vs_vwap_bps` among FILLED cells; tiebreak on lowest median `time_to_fill_s`. Excludes cells
without VWAP. _Reported, not used for primary ranking._

|   | instrument | best_strategy | median_slip_bps | median_ttf_s | n_fills |
|--:|:-----------|:--------------|----------------:|-------------:|--------:|
| 0 | AAPL/STK   | MKT_RAW       |          0.4008 |        1.005 |       1 |
| 1 | SPY/STK    | LMT_MID       |         -0.1392 |      14.0407 |       1 |

---
_Source: `trials_paper.parquet` · primary metric: `slip_vs_mid_t0_bps` · tiebreak: `time_to_fill_s`_
