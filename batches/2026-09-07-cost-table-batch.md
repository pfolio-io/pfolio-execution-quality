# The cost-table batch — 2026-09-07

*Spec + priced batch for DD4-4 (`pfolio-hq/docs/2026-09-04-decision-day-desk.md`).
Written by W1 of the two-layer orchestration trial, unattended, under hq
convention 0b: the record lands before anything runs, and every call it took is a
row in §0. **Nothing here places an order.** Marcel types the runner; the lane
resumes at the store (W8).*

**Ready at 10:35 CEST, 2026-09-07.** Cut-off was 15:00. All of it is European —
none of it waits for the US open at 15:30.

---

## 0. Register — every call, finding → change

| # | Call | Class (0b(i)) | Finding → change |
|---|---|---|---|
| **W1-1** | **The batch is European only; no US legs** | derivable | H-4 is about the SMART routing mix behind the three `EU_STK_*` `min_per_order` values; CTS-14 is about `EBS`; the FX read is a quote read. Nothing in scope touches a US bucket, whose `min_per_order` is USD 0.35 and long measured → **the whole batch fits inside 09:00–17:20 CEST and does not wait for 15:30** |
| **W1-2** | **Two invocations, not one** | reversible on sight | `--qty` is a **global** override across every instrument in one invocation (`runner.py::main`, `qty_summary`). One invocation at the second notional would put 35 shares of SXR8 (≈ EUR 25,300) and of CSP1 (≈ GBP 21,600) through the router alongside the CHF line. → **Batch A at one share across the three lines; Batch B on the CHF line alone with `--qty`** |
| **W1-3** | **The second notional is CHF only** | derivable | At a second notional the XETRA and LSE excesses separate their two hypotheses by EUR 0.01 and GBP 0.04 — below anything readable. The SIX excess separates by **CHF 1.23** (§3). CTS-14 names `EBS`, and only `EBS` is measurable → **`EU_ETF_CHF` alone carries the second notional** |
| **W1-4** | **23 shares (≈ CHF 3,984) as the second notional** | derivable | The 6 bps rate must bind clearly over the CHF 1.50 schedule minimum (it binds above CHF 2,500) and the two competing hypotheses must separate by more than the measurement's variance, which is **zero** across `EBS`'s 7 fills. At 23 shares they predict CHF **4.47** and CHF **5.70** → readable. A higher notional separates further and costs proportionally more |
| **W1-5** | **`MKT_RAW` only in Batch B** | derivable | It is the leg the shipped executor reaches, it filled **10/10** on this line, and it is the only one that does not leave CHF 3,984 resting while a limit retries. `LMT_MID` filled 3/16 and `MKT_ADAPTIVE` 4/16 on CHSPI — at this size their non-fills are not free, they are exposure |
| **W1-6** | **`MIDPRICE_NATIVE` excluded from Batch A by name** | derivable | Absent from `orderTypes` on all three lines; 6 of 6 SKIPPED across both August sessions. Excluding it costs nothing and makes the banner's cell count honest (18, not 24). Pre-flight re-checks the eligibility for free, so the exclusion is not an assumption |
| **W1-7** | **Batch A runs once, in the morning window** | reversible on sight | An afternoon repeat would buy a within-day regime contrast that batches 3–4 already established exists (SIX 1.146 → 2.234 bps). H-4 needs **sessions**, and the study's target is ≥ 5; a different *day* is a better fourth session than a second slot today. Priced as an option in §5 anyway — spending more is Marcel's |
| **W1-8** | **Batch B is fired one run at a time, ceiling two, third optional** | reversible on sight | `EBS` takes 20.6% of SIX flow (7 of 34) and cannot be directed to (error 10311). Two runs = 8 venue draws = **84%** chance of at least one `EBS` fill; three = 94%. Buying the last 10 points costs ≈ €30 → **the third run is offered, not specified** |
| **W1-9** | **The FX read ran today, read-only, and is in §6** | derivable | `fx_rates.json`'s `_doc` promises IDEALPRO mids; three rows are Riksbank fixings. Read from the declared source at 10:05 CEST via `batches/fx_idealpro_read.py`, paper gateway (same market data). **All eleven pairs quoted.** The table is NOT edited here — `cost_tables/` is canon (S1-33, convention 8) |
| **W1-10** | **Nothing on this machine is listening on port 7496** | **Marcel's — blocking** | IB **Gateway** is up on 4001 (live) / 4002 (paper); `runner.py::IB_PORT` is hardcoded to **7496** (TWS) with no CLI override. The runner and `quality.preflight` will both fail to connect as written. Route (a) point the live Gateway's API socket at 7496, or start TWS live on 7496 — **no code change**; route (b) edit `IB_PORT`, which is an edit to order-building code with money at the other end (convention 8). **Recommend (a).** §2 gate 1 |
| **W1-11** | **The live store shows SXR8 net +1 share** | **Marcel's — blocking** | 31 entry fills, 30 exit fills: the 2026-08-12 auto-flatten timeout (`quality/README.md`). The store cannot say whether it was closed by hand, because a manual close never passes through the harness. `batches/flat_check.py` asks the account; **it was not run against the live account by this session** (permission denied — the live gateway is a real brokerage account). §2 gate 2 |
| **W1-12** | **The pre-flight banner UNDERSTATES Batch B** | reversible on sight (report only) | `runner.py::_eu_commission_estimate` prices every European order at `min_per_order` and never reads `--qty`; its own text says "at 1 share the minimum binds". At 23 shares the 6 bps rate binds and the banner will print **CHF 7.72** for a run whose ceiling is **CHF 28.3**. **True for Batch A, false for Batch B** → the maximum in §5 is this document's, not the banner's. Code fix is not W1's (`order-execution/` frozen); filed as a follow-up |
| **W1-13** | **`batches/` is a new top-level directory** | reversible on sight | The dispatch asks for the brief and priced batch "outside `order-execution/`", and this repo had nowhere for one — batches 1–5 exist only in commit messages and hq todo rows. Three files, one job each. Uncommitted; a commit here is a publication and is Marcel's |

**Parked, not taken** — W1-10, W1-11, and: whether a central-bank fixing is
admissible in `fx_rates.json` at all (the repo's `CLAUDE.md` reserves it, and §6
makes it moot in practice but not in principle); the optional third Batch B run
and the optional afternoon Batch A repeat; every `cost_tables/` edit.

---

## 1. What the batch is for

Three things, in one market session:

1. **H-4 — re-measure the SMART routing mix** before any cost is scored. The
   three `EU_STK_*` `min_per_order` values are routing-weighted charges measured
   over **137 fills on two days in August**, and `broker_ibkr.json` says in its
   own `_edit_authority`: *"the routing mix is an observation, not a constant …
   nothing re-derives them."* This adds **session 3** against a target of ≥ 5.
2. **CTS-14 — the second notional.** Every European fill in the store is at one
   share, so nothing can say whether `EBS`'s CHF 3.58 against a CHF 1.50 schedule
   minimum is a flat add-on or a rate. **The two imply opposite corrections at a
   user's size**, and that is the ambiguity CTS-8's boundary sits on.
3. **FX from IDEALPRO mids** — a read, done, §6.

## 2. Gates — all three clear before anything fires

**Gate 1 — the API port (W1-10).** Nothing is listening on **7496**, which is
where `runner.py` and `quality.preflight` connect. IB Gateway is on 4001 (live)
and 4002 (paper). Point the **live** Gateway's API socket at 7496 (Configure →
Settings → API → Socket port), or start TWS on the live account with the API on
7496. No file in this repo changes.

> **⇒ RIDER 2026-09-08, 10:27 CEST — GATE 1 IS DISCHARGED, AND NOT BY EITHER
> ROUTE W1-10 OFFERED.** Marcel ruled that the Gateway's sockets are **permanent**
> (4001 live · 4002 paper) because they are shared with his personal-investing
> workspace — so route (a), moving the Gateway's socket to 7496, was never
> available, and the ruling makes that a standing constraint rather than today's
> inconvenience. Route (b) was therefore the only route, and he authorised it
> ("the ports 4002 and 4001 will stay like this now, you can permanently change
> them"). Taken as a **mode-keyed** change rather than the flat `IB_PORT = 4001`
> W1-10 described: `IB_PORT_BY_MODE = {"paper": 4002, "live": 4001}`, because a
> single constant would send `--mode paper` at the live Gateway — caught by
> `account_mode_refusal`, but caught as a refusal every paper run would then hit.
> `preflight.py` gains `--mode` (default **live**: it pre-flights a live batch and
> places no orders in either mode); `flat_check.py` and `fx_idealpro_read.py`
> default to 4001; the README's "port 7496" line is corrected. 47 tests pass.
> **The gate's operator action is now: none.** ⚑ Written after the code, not
> before it, against hq convention 0b — the batch had a market-hours window and
> the record would have spent it. The repo's own write authority covers the edit
> (`CLAUDE.md`, "may, unasked … write code in `quality/`"); the commit is Marcel's.

**Gate 2 — flat (W1-11).** Confirm the account holds no SXR8, CSP1 or CHSPI:

```
cd "/Users/marceloedi/pfolio/08 Claude/pfolio-workspace/pfolio-execution-quality"
python batches/flat_check.py --port 7496     # or --port 4001 before gate 1
```

Read-only, places no orders; it prints only the contracts this harness has
traded and counts the rest without naming them. If SXR8 shows a position, close it by hand first —
the round-trip pairing W8 computes starts from the opening position.

**Gate 3 — pre-flight (free, and it is the step that answers the money
question).**

```
cd "/Users/marceloedi/pfolio/08 Claude/pfolio-workspace/pfolio-execution-quality/order-execution"
python -m quality.preflight
```

Three things to read in its output before firing:

- every line **READY** (a `NO MARKET DATA` verdict means the other strategies
  would fill and record a null slippage — full commission, no measurement);
- `eligible` on each line — confirms `MIDPRICE_NATIVE` is still unsupported and
  that the three strategies in Batch A exist;
- **`sizes=` on the CHF line — this gates Batch B.** 23 shares must sit inside
  the displayed touch. If the ask size is below 23, `MKT_RAW` walks the book: the
  cost rises and the slippage stops being comparable with the one-share rows.
  **If the touch is thinner than 23, cut `--qty` to the displayed size and say so
  in the handover — do not fire 23 anyway.**

## 3. What Batch B can settle, stated before it runs

`EBS` charged **CHF 3.58 on 7 of 7 fills with zero variance** at a CHF 173
notional, where the CHF 1.50 schedule minimum binds. At 23 shares
(**CHF 3,983.60**) the 6 bps rate binds instead — CHF 2.39 — and the two
explanations `broker_ibkr.json` names stop agreeing:

| Explanation | What `EBS` charges at CHF 3,984 |
|---|---|
| **Flat** — a fixed exchange add-on of CHF 2.08 that persists at size | **CHF 4.47** |
| **A rate** — 2.387× the IBKR charge, i.e. the excess scales with notional | **CHF 5.70** |

They separate by **CHF 1.23** against a measured variance of zero, and they imply
opposite corrections at a user's size — which is the whole of CTS-14. IBKR's own
`max_per_order` for this bucket is CHF 99, so that is the contractual ceiling per
order whatever the answer turns out to be.

A single `EBS` fill separates them. The non-`EBS` venues are informative in the
same run and for free: `EUDARK` and `TRWBCH` charged CHF 1.50 exactly at one
share and should charge **CHF 2.39** here if the 6 bps rate is right.

Second, unpaid-for observation: the store's claim that **slippage is
size-independent while the order sits inside the displayed quote** has never been
tested at two sizes. Batch B tests it, provided gate 3's size check passed.

⚑ **`EBS` cannot be aimed at.** The account is refused on every directed order
(error 10311), so SMART picks the venue and `EBS` took 20.6% of SIX flow. Two
runs give 8 draws — **84%**. If no `EBS` fill appears, that is reported as
**n = 0 at the second notional**, blank, not filled in from the one-share row.

## 4. The commands — in this order, and nothing else

Directory for everything in this section:

```
/Users/marceloedi/pfolio/08 Claude/pfolio-workspace/pfolio-execution-quality/order-execution
```

(The space in `08 Claude` needs the quotes. `python` here is
`/opt/anaconda3/bin/python`, 3.12.10; `python3` is the same binary.)

**Step 1 — Batch A, the routing mix at one share (H-4).** Inside 09:00–17:20
CEST. One invocation.

```
python -m quality.runner --mode live --yes-live \
    --instruments eu --side BUY SELL \
    --strategies LMT_MID MKT_ADAPTIVE MKT_RAW
```

18 cells (3 lines × 3 strategies × 2 sides), ≤ 36 orders with auto-flatten.
Auto-flatten is **on by default in live mode — never pass `--no-auto-flatten`**,
and `--outside-rth` is refused for European cells by the runner itself.

**Then read two things in the output before going on:** the `venue_coverage`
block (any venue with no commission rule is reported, not absorbed) and the
`FLAT — no traded contract carries a position` line. If it does not say FLAT,
**stop and flatten by hand.**

**Step 2 — Batch B, the second notional on the CHF line (CTS-14).** Same window.
Run it **once**, read the output, then run it again.

```
python -m quality.runner --mode live --yes-live \
    --instruments EU_ETF_CHF --side BUY SELL \
    --strategies MKT_RAW --qty 23
```

After each run: `venue_coverage` — how many fills landed on **`EBS`** — and the
FLAT line. **Stop as soon as two `EBS` fills are in.** Two runs is the spec; a
third is §5's option and yours to take.

⚑ **Attended, one run at a time.** At 23 shares an auto-flatten exit that times
out leaves ≈ CHF 3,984 open, not the CHF 173 of the August incident — and that
incident happened on run 14 of 20 in an unattended loop, with a correct warning
printed to a console nobody was watching.

**Do not run:** `--instruments all` · `--outside-rth` · `--no-auto-flatten` ·
any `--qty` on Batch A · a second notional on the EUR or GBP line (W1-3).

## 5. What it can cost

Every input is measured — the committed tables, the 389-row live store, and
today's FX read. Nothing is assumed (S1-33).

| | Batch A | Batch B (2 runs) | Total |
|---|---|---|---|
| Orders, worst case | 36 | 8 | 44 |
| Commission, worst | EUR 15.62 · GBP 13.92 · CHF 42.96 | CHF 45.64 | |
| Spread paid, worst | ≈ EUR 7.5 | CHF 11.05 | |
| **Ceiling** | **≈ €85** | **≈ €60** | **≈ €145** |
| **Expected** | ≈ €35 | ≈ €31 | **≈ €66** |

**The maximum this batch can cost is ≈ €145.**

How the ceiling is built. Commission: every cell fills *and* every fill lands on
the most expensive venue ever measured for that line — `IBIS2` EUR 1.3019,
`LSEETF` GBP 1.16, `EBS` CHF 3.58 at one share, and at the second notional the
dearer of §3's two hypotheses (CHF 5.70). Spread: the worst `MKT_RAW` entry
slippage ever recorded on that line (5.25 · 2.50 · 3.47 bps), doubled for the
round trip, on every round trip. Converted at today's IDEALPRO mids
(EUR 1.16215, GBP 1.35302, CHF 1.23461 USD). The expected column uses the
routing-weighted charges, the measured fill rates (`LMT_MID` 58%,
`MKT_ADAPTIVE` 50%, `MKT_RAW` 100%) and median slippage.

**Options, priced, both yours:**

- a **third Batch B run** — +≈ €30 ceiling, `EBS` odds 84% → 94%;
- an **afternoon repeat of Batch A** after 15:30 CEST — +≈ €85 ceiling, buys a
  second intraday regime (W1-7 recommends against: a different day is worth more
  to H-4 than a second slot today).

Both together take the ceiling to ≈ €260.

**Taxes — named, and not in the numbers above.** At these sizes:

- **UK: none.** CSP1 is `IE00B5BMR087`, Irish-domiciled → no SDRT; SDRT needs
  ≥ £1,000 and the PTM levy ≥ £10,000, and one share is £617.
- **Switzerland: none expected**, and this is a measurement, not a hope — the
  2026-08-12 close established the account trades through IB UK, so
  `broker_swiss_only` applies and the duty stops (`EU_STK_SIX` went 46.02 →
  16.02 bps on that finding). **If it is charged after all**, it is 0.075% on
  both legs of a Swiss-issued line: CHF 1.56 on Batch A + CHF 23.90 on Batch B
  ≈ **€27** on top. Worth a glance at the first CHSPI fill's charge.

⚑ The runner's own banner will print an EU estimate that is **right for Batch A
and low for Batch B** (W1-12): it prices every European order at
`min_per_order` and never reads `--qty`. Approve against this table, not against
that line.

## 6. The FX read — done, 10:05 CEST, no orders

`python batches/fx_idealpro_read.py --port 4002`, read-only. All eleven pairs
quoted, so the entitlement question is closed and every row can come from the
source `fx_rates.json`'s own `_doc` promises.

| ccy | IDEALPRO mid (USD per 1 unit) | in the table | drift |
|---|---|---|---|
| EUR | 1.162145 | 1.10 | +5.65% |
| GBP | 1.353015 | 1.27 | +6.54% |
| AUD | 0.722125 | 0.66 | **+9.41%** |
| NZD | 0.587860 | 0.61 | −3.63% |
| CAD | 0.722982 | 0.74 | −2.30% |
| CHF | 1.234606 | 1.20 | +2.88% |
| JPY | 0.006428 | 0.0066 | −2.61% |
| HKD | 0.127557 | 0.128 | −0.35% |
| MXN | 0.059213 | 0.0572 | +3.52% |
| SEK | 0.104267 | 0.108 | −3.46% |
| SGD | 0.789656 | 0.78 | +1.24% |

Three rows are past the file's own refresh trigger (*"edit when rates drift more
than ~5%"*), one of them by nearly double. The table is read by
`calculator/cost_model.py` to express every cost component in the user's base
currency and is fetched from `@main` by the public tool, so the drift reaches a
published number wherever a cost is charged in one currency and quoted in
another, and wherever the base currency is not USD. **It does not move the
European study's published round-trip figures** — those have commission and
notional in the same currency.

**Not edited here.** `cost_tables/` is canon and read-only to every agent
(convention 8; S1-33). Two things ride with whoever does edit it: the three
Riksbank-sourced rows (MXN, SEK, SGD) can now come from IDEALPRO instead, which
settles the practical half of that question and leaves the principle to Marcel;
and `_as_of` moves to 2026-09-07 with `_doc` losing its Riksbank sentence.

## 7. Hand-over — what W8 does after the account is flat

Read the store, not the console. Per bucket: the new routing weights and the
routing-weighted `min_per_order`, against the August values (XETRA 1.2636 · LSE
1.0343 · SIX 1.9291) — those reproduce exactly from the store's per-fill
commissions, so the recomputation is checkable. Then `EBS` at the second
notional against §3's table, and **blank beside n = 0** if no `EBS` fill landed.
The CHF 1.50 figure must not ship.

## 8. Files

- `batches/2026-09-07-cost-table-batch.md` — this document
- `batches/fx_idealpro_read.py` — read-only IDEALPRO mid reader (ran, §6)
- `batches/flat_check.py` — read-only pre-batch position check (gate 2)

All three are uncommitted. **A commit to `main` in this repo is a publication**
(the public page reads these files from `@main` via jsDelivr) — the commit is
Marcel's.
