"""Asset-class bucketing — the one place that reads `asset_class_buckets.json`.

`analyze.py` (which builds the matrix CSV the public tool reads) and
`calculator/harness_data.py` (which the cost model reads) both have to answer the
same question: *which calculator asset class does this trial row belong to?* They
each carried their own copy of `_instrument_key` and their own matcher, and the
two copies agreeing was a coincidence maintained by hand. This module is that
answer, once.

**Why the selector grew a second form.** The original map was
`asset_class -> [instrument-key patterns]`, and an instrument key is
`'<symbol>/<secType>'` (plus `/<expiry>` for futures). That is enough while every
class is distinguishable by symbol. It stops being enough at `EU_STK_XETRA` /
`EU_STK_LSE` / `EU_STK_SIX`, which are **the same secType on three venues**: the
key carries no venue, so a symbol pattern written for one of them matches a
listing on either of the others, and there is no ticker to write anyway — the
universe screen's `venues` column is null on all 1,851 rows.

So a selector may also constrain `exchange` and `currency`, which the trial store
records per row from the contract IBKR actually resolved. Every constraint
present must hold.

**The ambiguity check is not decoration.** `_bucket_for` returned the first
matching bucket in file order, which is unambiguous only while no two selectors
can match the same row. The venue-partitioned classes are the first ones where
that could quietly stop being true, and a row landing in the wrong venue's bucket
would move a published median rather than raise anything. `check_ambiguity`
reports every collision it can see in the data it is given.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

BUCKETS_PATH = Path(__file__).resolve().parent / "cost_tables" / "asset_class_buckets.json"


@dataclass(frozen=True)
class Selector:
    """One asset class's membership rule. Empty constraints are unconstrained."""

    patterns: tuple[str, ...] = ()
    exchange_any: tuple[str, ...] = ()
    currency_any: tuple[str, ...] = ()

    #: True when this selector needs columns the instrument key does not carry.
    #: Callers that only have a key (and not a whole row) must refuse rather than
    #: guess — see `matches_key`.
    @property
    def is_venue_partitioned(self) -> bool:
        return bool(self.exchange_any or self.currency_any)

    def matches_key(self, inst_key: str) -> bool:
        """Pattern leg only. **Not sufficient for a venue-partitioned selector**,
        and it is the caller's job to know that; `bucket_series` does."""
        return any(
            fnmatch.fnmatchcase(inst_key, p) if "*" in p else inst_key == p
            for p in self.patterns
        ) if self.patterns else True

    def matches_row(self, inst_key: str, exchange: str, currency: str) -> bool:
        if not self.matches_key(inst_key):
            return False
        if self.exchange_any and (exchange or "") not in self.exchange_any:
            return False
        if self.currency_any and (currency or "") not in self.currency_any:
            return False
        return True


def _to_selector(value) -> Optional[Selector]:
    if isinstance(value, list):
        return Selector(patterns=tuple(value))
    if isinstance(value, dict):
        return Selector(
            patterns=tuple(value.get("patterns", ())),
            exchange_any=tuple(value.get("exchange_any", ())),
            currency_any=tuple(value.get("currency_any", ())),
        )
    return None


def load_bucket_map(path: Path = BUCKETS_PATH) -> dict[str, Selector]:
    """asset_class -> Selector. Meta keys (leading `_`) are dropped.

    Returns `{}` on a missing or malformed file, which is what both callers did
    before and is the only behaviour that keeps the matrix build from dying on a
    bad edit. A bucket present but *empty* is a different thing and is kept, so
    `declared_classes()` can still report it as declared-and-unmeasured.
    """
    try:
        raw = json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        sel = _to_selector(value)
        if sel is not None:
            out[key] = sel
    return out


def instrument_key(df: pd.DataFrame) -> pd.Series:
    """`<symbol>/<secType>` plus `/<expiry>` for futures. The canonical form.

    Both consumers used to define this themselves. It lives here now so the two
    cannot drift — a drift would not raise, it would silently empty a bucket.
    """
    expiry = df["expiry"].fillna("").astype(str) if "expiry" in df else pd.Series("", index=df.index)
    sec = df["secType"].fillna("").astype(str) if "secType" in df else pd.Series("", index=df.index)
    return (
        df["symbol"].astype(str)
        + sec.apply(lambda s: f"/{s}" if s else "")
        + expiry.apply(lambda e: f"/{e}" if e else "")
    )


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    if name in df:
        return df[name].fillna("").astype(str)
    return pd.Series("", index=df.index)


def bucket_series(df: pd.DataFrame, bucket_map: Optional[dict[str, Selector]] = None) -> pd.Series:
    """One bucket name (or None) per row, using exchange and currency when the
    selector asks for them."""
    bucket_map = load_bucket_map() if bucket_map is None else bucket_map
    if df.empty or not bucket_map:
        return pd.Series([None] * len(df), index=df.index, dtype=object)

    keys = instrument_key(df)
    exchanges, currencies = _col(df, "exchange"), _col(df, "currency")

    def assign(i) -> Optional[str]:
        k, x, c = keys[i], exchanges[i], currencies[i]
        for name, sel in bucket_map.items():
            if sel.matches_row(k, x, c):
                return name
        return None

    return pd.Series([assign(i) for i in df.index], index=df.index, dtype=object)


def rows_for(df: pd.DataFrame, asset_class: str,
             bucket_map: Optional[dict[str, Selector]] = None) -> pd.DataFrame:
    """Every trial row belonging to `asset_class`. Empty frame when none do."""
    bucket_map = load_bucket_map() if bucket_map is None else bucket_map
    sel = bucket_map.get(asset_class)
    if sel is None or df.empty:
        return df.iloc[0:0]
    keys = instrument_key(df)
    exchanges, currencies = _col(df, "exchange"), _col(df, "currency")
    mask = pd.Series(
        [sel.matches_row(keys[i], exchanges[i], currencies[i]) for i in df.index],
        index=df.index,
    )
    return df[mask]


def check_ambiguity(df: pd.DataFrame,
                    bucket_map: Optional[dict[str, Selector]] = None) -> dict[str, list[str]]:
    """Instrument keys that satisfy more than one selector, key -> buckets.

    `bucket_series` resolves a collision by file order and says nothing. That is
    fine while no collision exists and invisible the moment one does, so it is
    checked rather than assumed — the venue-partitioned classes are the first
    place in this file where two selectors could overlap.
    """
    bucket_map = load_bucket_map() if bucket_map is None else bucket_map
    if df.empty or not bucket_map:
        return {}
    keys = instrument_key(df)
    exchanges, currencies = _col(df, "exchange"), _col(df, "currency")
    out: dict[str, list[str]] = {}
    for i in df.index:
        hits = [n for n, s in bucket_map.items()
                if s.matches_row(keys[i], exchanges[i], currencies[i])]
        if len(hits) > 1:
            out[f"{keys[i]}@{exchanges[i]}/{currencies[i]}"] = hits
    return out


def declared_classes(bucket_map: Optional[dict[str, Selector]] = None) -> list[str]:
    return list((load_bucket_map() if bucket_map is None else bucket_map).keys())


def unmeasured_classes(df: pd.DataFrame,
                       bucket_map: Optional[dict[str, Selector]] = None) -> list[str]:
    """Classes the map declares and the store has no rows for.

    This is the state `EU_STK_XETRA` / `EU_STK_LSE` / `EU_STK_SIX` are in on
    2026-08-04, and the reason the list is computed rather than commented: a class
    with no rows is one the cost model has to refuse to price, and the refusal
    has to know which classes those are without anyone maintaining a second list.
    """
    bucket_map = load_bucket_map() if bucket_map is None else bucket_map
    return [c for c in bucket_map if rows_for(df, c, bucket_map).empty]
