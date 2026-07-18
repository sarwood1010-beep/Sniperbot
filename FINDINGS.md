# FINDINGS — running log of the edge hunt

Purpose: track what we've tested, concluded, at what confidence, and what's still
open — so we don't re-derive, and don't over-conclude. Update as we go.

## Goal
Find a real, tradeable edge and slowly grow principal. Paper-only until proven.

## DONE — high confidence
- **Hardening of the bot** (safety fixes, tested `core.py`, structured logging,
  budget-safe measurement loop, deploy discipline, rollback). Solid and reusable
  regardless of which edge we pursue. This is what lets us test cheaply/safely.
- **Original mean-reversion strategy has NO edge.** Measured: symmetric TP/SL on
  ~50% hit rate = coin flip, PF ~1.0, expectancy ~0. The trail-stop was dead
  code. Confidence: HIGH.

## CORRECTION (important) — the venue is NOT dead
An earlier read ("Polymarket in-play tennis is a dead ~50% market") was WRONG. It
was based on 3 cherry-picked snapshots from the tiny has_book subset + a buggy
calibration. `diag_bookmovement.py` on the FULL data: 59/70 markets tracked the
score, avg corr(market, model) = +0.64, market prices ranged 0.5-0.9 during play.
The markets are ALIVE and track the match — but appear to LAG the model (e.g. up
a set+break: model 83%, market still 51%), which is exactly the original edge
thesis. Lesson: never conclude from a cherry-picked subset; use all the data +
a robust metric.

## ROOT CAUSE FOUND — our market price + outcome data is corrupted
Investigated the downloaded log (8139 recs, 71 matches) directly. Findings:
- **market_p2 ~= market_p1 (mean |diff| 0.07), NOT complementary.** The two
  marketSides "price" fields from extract_aec_markets both track ~participant1 /
  the favorite. So `market_p2` and `market_sum` are GARBAGE.
- **The `healthy()` sum-in-[0.9,1.1] filter therefore selected a non-
  representative subset** -> every calibration/Brier/edge conclusion that used it
  is INVALID (that's why market always looked ~50% / Brier ~0.25 / actual=0%).
- **At settlement the price flips/collapses** (mkt_p1 -> 0, mkt_p2 -> 0.99, sum
  -> ~1) even for a player who was winning (e.g. Korneeva up 6-2,5-2 shows
  mkt_p1=0). Last-live mkt_p1 -> all p1; settlement -> all p2. So we CANNOT
  extract true winners from the price. And completed scores are almost never
  captured (1/71). => **NO reliable OUTCOMES. Calibration is impossible on this
  data.**
- **What survives (trustworthy):** market_p1 DURING PLAY tracks the score (corr
  +0.64, hits 0.98 for dominant players, 0.16 for losing ones). Venue is ALIVE.

### Implication
The 8k existing records can show the venue is alive, but CANNOT answer "does the
model beat the market" (corrupted prices, no outcomes). We must FIX DATA
COLLECTION and re-gather:
1. Log the CLEAN market price = the WS order book (bid/ask / longQuote/shortQuote
   were complementary in /raw), NOT the corrupted REST marketSides "price".
2. Capture the REAL match outcome (feed result endpoint, or a completed score,
   or Polymarket resolution) — a dedicated settlement record, not a price proxy.
Then collect fresh clean data and re-run calibration.

## OPEN — the tennis model-vs-Polymarket edge (NOT ruled out; blocked on clean data)
Thesis: a live tennis win-prob model (anchored to the opening price, updated by
the score) diverges from a slow Polymarket price -> trade the gap.

What we've learned so far:
- The model is a WEAK edge candidate on its own: it knows only the score; the
  market knows more. Divergence alone != edge. (Strategic caution, not disproof.)
- Live main-tour markets CAN be liquid (saw a 1c spread, 18.7k OI on one).
- Pre-match tennis markets are thin/unreliable (sides summed 61%-116%).
- **Measurement is UNRELIABLE — do not trust edge conclusions yet:**
  - `market_p1` logged the stale REST price; live price is the WS book.
  - In the resolved-match diagnostic, books sat ~50% and didn't track the score
    — BUT this is CONFOUNDED (see open question below).
  - Calibration showed model & market both at Brier ~0.25 (coin-flip) and
    `actual`=0% for P1 — signature of broken/ mislabeled price data, NOT a real
    "no edge" result.

### THE open question (must answer before any conclusion)
Were live matches paired to their CORRECT, currently-active Polymarket market, or
to a FUTURE-dated / pre-match market that's parked at ~50%? A live-match-paired-
to-a-dead-pre-match-market produces an IDENTICAL "dead book" signature to a truly
dead venue. **Not yet excluded.** `diag_bookmovement.py` tests this: if ANY match
shows the market price tracking the score (corr>0.4 & moved), the venue is alive
and our pairing/coverage is the bug (fixable) — tennis is NOT ruled out.

### Known/suspected bugs in the measurement (fix before trusting data)
- market_p1 = stale REST price, not live WS book mid.
- Possible player-ordering inconsistency (score vs price vs outcome; `actual`=0%).
- Match->market pairing may hit wrong-date/pre-match markets.
- Outcome capture is a proxy (last snapshot); needs real settlement.
- "sane sum" (0.9-1.1) does NOT guarantee a live/traded market (a dead 50/50
  book passes).

## More robust edge (researched, parked): sharper reference
`The Odds API` Business tier ($99/mo, 200k req) includes **Pinnacle** in-play
tennis odds (legal to consume as data). Classic robust edge: thin market lags the
sharp book. BUT it still needs a correctly-read, LIQUID live Polymarket price to
trade against — so it depends on resolving the open question above.
See `RESEARCH_sharp_reference.md`.

## Tools built (all read-only analysis; run on the droplet)
- `analyze_edge.py` — volume + data-quality summary.
- `analyze_convergence.py` — signal backtest (outcome proxy was buggy; superseded).
- `analyze_calibration.py` — model-vs-market Brier + calibration (needs clean data).
- `diag_resolved.py` — dump raw resolved matches.
- `diag_bookmovement.py` — DECISIVE: did the market ever track the score?

## Current decision point
Run `diag_bookmovement.py`. Then:
- If some markets tracked the score -> fix pairing/coverage, re-measure. Tennis
  alive.
- If nothing tracked AND slugs were future-dated -> pairing bug, still fixable.
- If nothing tracked AND slugs were same-day/live -> strong evidence the venue's
  in-play tennis is untradeable; consider pivot (other Polymarket markets w/ real
  in-play liquidity, e.g. NBA/MLB) or wind down.
