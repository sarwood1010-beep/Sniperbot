# Research: a sharper reference price (the more robust edge)

## The strategic problem this addresses
Our model-vs-market approach rests on a weak premise: a generic tennis-math model,
anchored to the opening price, knows only the SCORE — while the market knows the
score *plus* everything a watching trader knows (form, momentum, fatigue, who's
actually better today). So our model has strictly LESS information than the
market, and our divergences are as likely to be our overconfidence as the
market's slowness. (Measured: the model jumps to ~89% when a player is up a set
and a break — reasonable math, but it can't know if THESE players make that lead
fragile. The market can.)

The classic, robust prediction-market edge is different and doesn't require
beating the market with our own model:

> **A thin, slow market lags a sharp, fast one.** Use the sharp market's price as
> "truth" and trade the thin market's lag toward it.

Polymarket US tennis is thin and new. **Pinnacle** is one of the sharpest tennis
books in the world and reprices within seconds. If Polymarket lags Pinnacle, that
gap is a real, model-free edge.

## The key finding: a sharp reference IS accessible to a US trader, legally
- **The Odds API** (the-odds-api.com) **Business tier — $99/mo, 200,000 req/mo**
  — includes **Pinnacle** (the sharp anchor) plus international books, covers
  **live/in-play tennis** (match-winner odds, ATP & WTA), and provides de-vigged
  **"fair odds"** and Pinnacle-anchored edge detection out of the box.
  (Professional tier, $29/mo, has US books DK/FD/etc. but NOT Pinnacle — US recs
  are slow/soft, not a useful sharp reference.)
- **Latency:** Pinnacle updates lines within seconds; recreational books lag
  30-60s. The Odds API relays via polling (limited by our interval; sub-minute is
  fine for a first pass). 200k req/mo ≈ 6,600/day — ample.
- **Legality:** *consuming odds DATA* via a commercial API is standard and
  low-risk — the US legal restrictions bite on *betting* at offshore/illegal
  books, not on reading their prices. We'd trade on Polymarket (a regulated US
  venue), using Pinnacle's price only as a reference. (Not legal advice; check
  the API's ToS, but there's no gambling-law barrier to consuming odds.)

Sources: the-odds-api.com/sports/tennis-odds.html ; oddspapi.io pricing 2026 ;
The Odds API v4 docs.

## Why this is a stronger bet than the model
- **Prior confidence:** "Pinnacle >> a thin new prediction market in sharpness" is
  a *strong* prior. "Our generic model >> the market" is a *weak* one.
- **Model-free:** Pinnacle's de-vigged win prob IS the fair value — no need for
  our score model at all (and possibly no need for the RapidAPI tennis feed).
- **Our infrastructure transfers:** the pipeline we built — match feed<->Polymarket
  by player name (`match_market_to_feed`), edge decision after the spread
  (`edge_signal`), budget-guarded measurement loop, the analysis tools — all
  reuse directly. We just swap the fair-value SOURCE from "our model" to
  "Pinnacle (de-vigged)". `core.decimal_to_prob` / `american_to_prob` /
  `devig_two_way` are already added and tested for exactly this.

## Caveats / open questions (to validate before committing)
1. **Overlap:** how many matches are live on BOTH Pinnacle and Polymarket at once?
   Pinnacle covers main-tour + bigger challengers; Polymarket lists mainly
   main-tour. The tradeable set is that intersection — needs measuring.
2. **Does Polymarket actually lag Pinnacle, and by enough to clear the spread?**
   Strong prior, but must be measured (same convergence/Brier tooling, with
   Pinnacle as truth instead of the model).
3. **Cost:** +$99/mo. Only rational once the account is funded enough that the
   edge covers it (Stage 2/3), OR for a one-month validation.

## Recommendation
Treat the **sharp-reference approach as the primary edge hypothesis** and demote
the model to a secondary sanity-check. Validate cheaply first: one month of The
Odds API Business ($99), run Pinnacle-vs-Polymarket in parallel using the SAME
pipeline, and measure the lag with the calibration tools. If Polymarket lags
Pinnacle after costs (very likely), that's the edge — model-free and robust. If
not, we've spent $99 to rule out the whole venue, which is cheap certainty.
