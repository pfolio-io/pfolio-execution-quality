# DS-1 — the calculator's `fx_conv_bps` term, 2026-09-14

*Claude Code, unattended under hq convention 0b: **the record lands before the code**,
and every call is a row in §1. Item: "Apply the calculator's `fx_conv_bps` term when
commission and contract currencies differ" (`pfolio-hq/todo/claude-code.md`, block
"Owed after the sitting's part one"; `pfolio-hq/docs/2026-09-11-decision-definitions-sitting.md`
row DS-1). Nothing is re-measured; nothing under `cost_tables/` is touched.*

**Publication boundary** (as W8-1): `main` is served via jsDelivr, so a branch commit
is not a publication and a merge is. This change set is a branch and a PR.
**Marcel merges.**

## 0. What the ruling says, and what was found

**`ruled:`** (DS-1, Marcel, 2026-09-11 ~09:50) *"yes, recommendation; and the order
execution study should have data on currency conversions"* · his framing: *"for a CHF
investor ACWI in USD (user converts chf to usd) directly competes with a ACWI
CHF-hedged and we have to determine which one is better for the user taking all
factors into account (order execution layer, tax layer)"*.

**`derived:`** (the sitting record) the conversion leg is measured at the reference
broker (`FX_IDEALPRO`); *"what is not applied is the calculator's `fx_conv_bps` term
when commission and contract currencies differ (`REPORT_RTH.md` 'no FX conversion
yet') — a Code chore, no number re-read"*.

**Four findings, read before any code:**

- **F1 — the report sentence is stale; the analyzer already converts.**
  `order-execution/quality/analyze.py::_commission_bps_row` pivots commission and
  notional through USD when the two currencies differ, and has since the initial public
  release (`b4c3aa7`). `results/REPORT.md` (regenerated 2026-08-12) shows it working:
  `EUR/CASH` commission 1.567 CHF → **0.6415 bps, `fx_converted` True**.
  `results/REPORT_RTH.md` is a paper slice of run `20260504T171243Z` whose text
  predates that; its `nan` for `EUR/CASH` is the old rendering, not the code's.
- **F2 — the calculator has no `fx_conv_bps` term at all.** `METHODOLOGY.md` §1 lists
  it; `calculator/cost_model.py` never computes it. `_to_base` converts each line into
  `base_currency` **for presentation** — no line charges the cost of converting money.
  This is the gap DS-1 names.
- **F3 — the literal trigger cannot carry the ruling.** In the calculator the
  commission currency is the broker rule's and `contract_currency` defaults to it, so
  "commission currency ≠ contract currency" fires only when a caller overrides the
  contract currency. And in Marcel's own case — a CHF client buying ACWI in USD — the
  commission is USD and the contract is USD: **the literal trigger charges nothing for
  exactly the conversion he named.**
- **F4 — `base_currency` is a pivot, not the client's currency, for the callers that
  exist.** `pfolio-app/research/allocation-study/costs.py` (read by
  `core-tier-race/race_costs.py`) passes `base_currency="USD"` for `EU_STK_SIX` and
  `EU_STK_XETRA`, the CHF and EUR scenarios. A trigger on "contract ≠ base" would charge
  the Swiss scenario a USD→CHF conversion its client never makes, and would move the
  next Core re-draw's costs with it.

## 1. Register — every call, finding → change

| # | Call | Class (0b(i)) | Finding → change |
|---|---|---|---|
| **FX-1** | **The term fires on an explicit funding currency** — `CostInput.funding_currency`, the currency the client pays in; the conversion is charged when it differs from the contract currency; `None` charges nothing | reversible on sight | F3 (the literal trigger misses the ruling's case) and F4 (inferring it from `base_currency` would charge a conversion nobody makes). The ruling's words are about the client's money moving from one currency to another; that is a fact only the caller knows → a field, default `None`, **every existing caller's output byte-identical** |
| **FX-2** | **The conversion is priced as an `FX_IDEALPRO` trade of the notional** — commission from `broker_ibkr.json` (`per_value_bps` 0.20, `min_per_order` 2.0, USD) on the notional's USD value, plus the harness's measured `slip_vs_mid_t0_bps` median for `FX_IDEALPRO` × the named strategy, capped at 0, flagged UNMEASURED where the store has none | derivable | the bucket DS-1 names; both paths are the calculator's own (`_commission`, `_slippage_cost`), reading `cost_tables/` and the committed trial store only — S1-33. The commission is computed on the USD value because the rule is denominated in USD (its `$2` minimum is dollars) |
| **FX-3** | **The conversion's strategy is required when the leg applies** — `fx_conv_strategy`, no default; missing → `ValueError` | derivable | the file's own ⚑ on `CostInput.strategy` (`6d3e9aa`): *"A default here is not a convenience; it is an unattributed assumption about execution, and S1-33 does not allow those."* Reusing the trade's strategy silently would be that default |
| **FX-4** | **A round trip converts twice** — in on entry, back on exit: commission ×2, one slippage line per leg | derivable | mirrors the trade's own `side="BOTH"` handling in `compute_cost` |
| **FX-5** | **Every conversion line says what was measured** — the `FX_IDEALPRO` bucket holds one instrument, `EUR/CASH` (EUR.USD); a CHF→USD leg carries a note that the median is that bucket's | reversible on sight | the calculator already applies a bucket's median to every instrument in its class; the note makes the pair visible rather than implying a CHF.USD measurement. No proxy table, no new number |
| **FX-6** | **The commission's own currency is not charged a separate conversion** | reversible on sight | the commission is already expressed in `base_currency` by `_to_base`; charging an IDEALPRO order with a $2 minimum to convert a $1–2 commission would cost more than the commission. What moves is the notional |
| **FX-7** | **`REPORT_RTH.md` is neither edited nor regenerated** | derivable | F1: the code it describes is already fixed. Regenerating re-reads every number in a published file — "no number re-read" (DS-1) → the stale sentence is reported here and in the PR |
| **FX-8** | **`tool/` (the public page's JS) is not touched** | derivable | lane scope; its header says *"USD-only so the FX layer is a no-op"*. Parity is owed, not taken (§3) |
| **FX-9** | **`METHODOLOGY.md` §1/§3, `calculator/README.md` and the CLI describe the term** | derivable | the repo's `CLAUDE.md` lets a session edit both docs and `calculator/`; a term the docs misdescribe is F2 again |

**Marcel's calls found: none. Calls that could not be classified: none.**
**Narrowing check (0b(ix)):** none — a caller who names no funding currency gets
exactly what it got before; nothing is removed from any result.

## 2. How it is checked

`tests/test_fx_conversion.py`, synthetic tables and a stubbed harness except where
the point is the committed store:

- no funding currency → no conversion line, and the lines equal the pre-change set;
- funding = contract → no line;
- CHF funding into a USD contract, one leg: commission = max(0.20 bps × USD value, $2)
  in base; slippage = the stubbed median × notional; bps add to the total;
- the $2 minimum binds on a small conversion;
- round trip: commission ×2, an entry and an exit slippage line, directions labelled;
- funding ≠ contract with no `fx_conv_strategy` → `ValueError`;
- an unmeasured conversion strategy → the line is flagged and the total reads incomplete;
- on the committed store: `FX_IDEALPRO` × `MKT_RAW` live is measured, so a CHF→USD
  leg is complete.

`python3 -m pytest tests -q` green before and after.

## 3. Owed, not taken

- **The public tool** (`tool/src/03-cost-model.js`) has no conversion leg; parity is
  its own item.
- **No caller names a funding currency yet.** The Core race and One price in USD
  as a pivot (F4); whether and where a funding currency enters them is the package's
  and the re-emission's, not this chore's.
- **`REPORT_RTH.md`**'s stale sentence (F1) stays until the report is next
  regenerated by a run that is allowed to re-read it.
