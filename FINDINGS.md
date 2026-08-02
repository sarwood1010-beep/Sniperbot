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

## TENNIS: RULED OUT — final verdict (2026-07-18)
Directly analyzed the downloaded log (8139 recs, 65-71 matches). Decisive:
- **No latency edge.** 2832 divergence events (|model-market|>=8pts, avg 17.6pts):
  over 1-10 min the market closes ~0% of the gap and converges <45% of the time.
  Even FRESH divergences right after a score change (model jumps) -> market moves
  AWAY (gap_closed -0.22, 35% converge). The market does NOT lag our model.
- **No accuracy edge.** Our model OVERSHOOTS on breaks (up-a-break -> 83%); the
  market correctly discounts (~55%) and is more right. The Polymarket in-play
  tennis market is EFFICIENT (tracks score +0.64, beats our model, doesn't lag).
- **Pinnacle NOT worth buying:** a market this efficient is unlikely to lag
  Pinnacle enough to beat $99/mo + thin liquidity. Prior dropped.
=> CONCLUSION: efficient + thin market = no retail edge for our tools. STOP the
spend (cancel RapidAPI $29, do NOT buy Pinnacle). Reached for ~$0 trading loss.
The hardened bot + analysis toolkit are reusable for another sport/venue.

## MLB — SCREEN 1 (LIQUIDITY): PASSED (2026-07-19, live games)
Probed the in-play book on Polymarket US during two live games (SF@SEA, PIT@CLE,
07-18) via the REST `pm.markets.book`/`bbo` endpoints. Decisive contrast with
tennis — the books are DEEP and TIGHT:
- **PIT@CLE:** spread 0.5c; ~$169k (bid) / >$181k (ask) resting within 2c of touch.
- **SF@SEA:** spread 0.5c; ~$14.8k (bid) / ~$1.55k (ask) within 2c; a $50 buy
  fills at avg 0.373 vs 0.365 ask (~1.1c slip vs mid). At-the-touch size varies
  (SF ask was only 4 shares) but within-2c depth is orders of magnitude over the
  $100 bar. OI/volume real (SF: 331k shares traded, 273k OI).
=> Both crush the Screen-1 bar (spread <=3c AND >=~$100/side within 2c). This is
NOT tennis-thin (tennis was ~10-share books, >5c spreads). Real depth to trade
size. Caveat: this is ONE snapshot of TWO games — the collector will confirm
breadth across many innings/games, but the liquidity gate is clearly cleared.

### What Screen 1 also SOLVED (the tennis data-quality holes)
- **Discovery:** the `search.query({"query":"mlb"})` feed EXCLUDES same-day
  markets (only lists games ~2-3 days out). But the slug is buildable directly:
  StatsAPI `abbreviation`.lower() == the Polymarket slug token for all 30 teams,
  so `aec-mlb-{away}-{home}-{ET-date}` + `pm.markets.retrieve_by_slug` reaches the
  live market. (Team-abbr search also surfaces it as a fallback.)
- **Clean price (fixes corrupted marketSides):** `pm.markets.bbo(slug)` returns
  real bestBid/bestAsk/currentPx/openInterest/sharesTraded in one cheap call.
- **Clean outcomes:** StatsAPI final score + status=Final (Polymarket's own
  `settlement` endpoint returned "not found" mid/soon-after game — StatsAPI is the
  outcome source, as planned).
=> NEXT = Screen 2 (efficiency/lag): `mlb_collect.py` (BUILT) polls bbo mid +
StatsAPI live WP + final outcome, matched by slug/date; then `analyze_mlb_lag.py`
(TODO) vs the bar. Only if lag exists AND clears cost is there an edge.

### Screen 2 — the TIMING subtlety (critical, measured 2026-07-19)
The reference (StatsAPI live WP) is ~**20-45s behind wall-clock** (latest WP play's
about.endTime vs now, measured across 4 live games). PM traders watch live TV and
reprice within seconds of a run. So:
- A naive "does PM converge to WP?" test would show PM LEADING WP by ~30s -- but
  that is a REFERENCE-LATENCY ARTIFACT, not true efficiency (the tennis-style
  false negative). Do NOT read "PM leads WP" as "no edge" without accounting for it.
- CORRECT test: measure PM's move AFTER StatsAPI's WP has already jumped. Since the
  reference is ~30s stale, ANY residual PM lag is a lag BEYOND 30s -- large, robust,
  tradeable. If PM has already moved by the time StatsAPI catches up -> efficient ->
  clean NO-GO. So the delay makes a DETECTED lag more credible while hiding small
  ones: Screen 2 surfaces only edges worth trading.
- The prior is still "probably efficient" -- deep/liquid markets (Screen 1 showed
  $180k books) are usually well-arbitraged. Screen 1 passing RAISES the bar for
  Screen 2. Collector records 3 clocks (poll_ts, wp_endTime, score/inning) so the
  analysis can separate efficiency from reference lag.

### Screen 2 — FIRST READ (07-19, leans EFFICIENT; NOT a verdict, sample too thin)
`analyze_mlb_lag.py` on the first overnight file (744 ticks). Coverage was thin
because collection STARTED 10:36pm ET -- most of the slate was already Final, so
only 4 games had live tick series (1 doubleheader dropped, 2 blowouts pinned near
0/100, leaving SF@SEA as the one game with real two-sided movement, an extra-
innings tie).
- **0 fresh divergences (|gap|>=6pts) across 11 WP-jump events.** When a run/big-
  out repriced WP, PM was ALREADY within 6pts -- no tradeable lag appeared.
- **SF@SEA: PM shadows WP ~1:1.** As wpAway swung 39->50->44->36->50->32->20, PM
  tracked it with |gap| median 2.8pts (p90 9.8), and no fresh gap CLOSED (converge
  ~9%). A small STABLE bias PM(away) ~2pts BELOW WP -- but a stable offset is not a
  lag (doesn't converge) and 2pts < the ~1.5-3c round-trip cost. Not tradeable.
- Looks like the tennis efficiency result (market tracks the reference, doesn't lag
  it), consistent with the deep-book prior. BUT this is ~1 moving game; the bar
  needs >=15 distinct games with full arcs. **Data-quality all validated**
  (price_side=away confirmed by 12 clean settlements; bbo mid tracks the game).
- DECISIVE NEXT: collect a FULL evening slate FROM FIRST PITCH (start ~6-7pm ET,
  run 5-6h) to get 15+ games incl. many close ones with real scoring-play
  divergences. Only then read the Screen-2 bar. WATCH the small PM<WP bias across
  more data (if it's persistent + calibrated + > cost it'd be a static-mispricing
  edge, distinct from a lag -- but 2pts is below cost, so: watch, don't chase).

## CROSS-VENUE: **RULED OUT — the "arb" was a POLLING TIME-SKEW ARTIFACT** (2026-08-02)
**This CORRECTS the 07-21 entry below, which called it a real edge. It is not.**
12 days of the paper model (3,170 detects / 2,029 fills / 1,134 misses, 64% capture,
+6.47c mean, $131 "profit" per contract, ZERO losing trades) looked spectacular —
which was itself the tell: a repeatable 6.5c arb across two liquid regulated
exchanges, hundreds of times a day for 12 days, would be industrial-scale free money.
Diagnosis (all checks in `analyze_mlb_xvenue.py`, velocity control now built in):
- Kalshi data is NOT stale/wrong: bulk `/markets` quotes MATCH the authoritative
  `/orderbook`; Kalshi reprices in-play as much as PM (0.665c vs 0.655c per tick,
  same ranges, millions of contracts of in-play volume). Both venues are live+active.
- Ticker mapping is correct (no date/matchup/side collisions; 99.1% of rows are
  closer as same-side than complement).
- **THE CAUSE — the two venues are sampled at slightly DIFFERENT INSTANTS.** The
  Kalshi index is fetched once per loop and reused while each game's PM quote is
  fetched fresh. When the price is repricing fast (right after a run), that time
  skew alone manufactures a phantom gap. Proof — NET>0 "arb" rate by how fast the
  price was moving:
      quiet (<0.5c/tick): **0.13%**   |  0.5-2c: 1.42%
      2-5c:               14.57%      |  >5c:   **41.61%**
  A **320x** difference driven purely by market velocity. 79% of all detected arbs
  occurred during fast repricing. At QUIET moments — the only condition where a gap
  would be stable enough to actually trade — the venues agree to ~0.4c and a
  net-positive arb essentially never appears (0.13%, indistinguishable from noise).
- The paper model inherited the same skew in its fill re-check, so phantom gaps
  "survived" the latency window -> the fake 64% capture and zero losses.
=> **NO cross-venue edge.** PM and Kalshi are efficient with respect to each other.
Any future two-venue test MUST quote both venues within the same instant (parallel
fetch, timestamp each leg, and DISCARD samples taken during fast repricing) and be
judged ONLY on quiet-moment gaps. LESSON (same family as the tennis
corrupted-price and the WP-latency traps): when two data sources are compared, an
apparent edge is a measurement-timing bug until proven otherwise; the giveaway is
an implausibly good, loss-free result. ACTION: stop `mlb-arb-paper` (it emits false
signals).

## [SUPERSEDED — see the correction above] CROSS-VENUE: a REAL arb EXISTS but is SPEED-GATED (2026-07-21)
First genuine edge signal in the project. `mlb_xvenue.py` collected PM+Kalshi
quotes on the SAME MLB game-winner binary (25k rows, 6.3k in-play). `analyze_mlb_
xvenue.py`:
- **Pre-game: efficient.** Venues agree to ~0.25c; NET arb after fees = 0.00%.
- **In-play: real dislocations.** |gap| p99=8.5c, max=31.5c. A NET-positive
  EXECUTABLE arb (after Kalshi fee) occurs ~**4% of live moments**, and SURVIVES a
  strict liquidity filter: with BOTH venues at <=1c spread, 32 arbs of 4.5-8.5c net
  (e.g. Miami PM 12% vs Kalshi 23.5% -> +8.5c). NOT stale-quote artifacts.
- **It's a TRUE arb, not a directional bet** (so the ~50/50 lead direction is
  irrelevant): buy the cheap venue + sell the dear venue on the same outcome ->
  both settle to the same $0/$1 -> locked gap regardless of winner. Verified
  (long MIA@PM 0.125 + long NO-MIA@Kalshi 0.77 = pay 0.895, always get $1 = +10.5c).
- **THE BINDING CONSTRAINT = execution speed.** Gaps persist median 1 poll (~20s),
  p90 2, MAX 3 (~60s). At 20s polling + manual fills we can't reliably fill BOTH
  legs before the gap closes. This is a latency/execution problem, not an
  "edge exists?" problem -- the edge is real and repeatable.
- OPEN before it's tradeable: (1) TRUE gap lifetime (need faster polling, ~2-3s);
  (2) resting SIZE at the arb price (only have top-of-book); (3) leg risk (one fills,
  gap closes -> naked); (4) capital efficiency (lock ~$0.90/contract for hours per
  ~5-10c); (5) PM fee (assumed 0 = best case) + Kalshi order API + PM order API.
=> STATUS: NOT ruled out -- the ONLY live edge found. Next (still PAPER/read-only):
build a FAST cross-venue monitor (poll both every ~2-3s) to measure true gap
lifetime + simulate latency-realistic paper fills -> is enough capturable after leg
risk to justify an execution build? Guardrail: real 2-venue execution touches the
"never enable live trading" rule -> paper-prove first, stage funding later.

## MLB: RULED OUT — final verdict (2026-07-19)
Full-slate data (15 distinct games, full pre-game->in-play arcs; 15,839 ticks; 27
clean outcomes). BOTH edge hypotheses fail decisively. Screen 1 (liquidity) PASSED
— deep, tight books — but the market is EFFICIENT both in-play and pre-game.
- **(A) IN-PLAY WP-lag = NO edge.** 298 WP-jump events, 46 FRESH divergences
  (|PM-WP|>=6pts after a run/big-out). PM does NOT close them: median gap_closed
  = -0.04, converge_rate 46% (bar: +0.40 and 60%). PM tracks the win-prob and does
  not lag it. Same signature as tennis. `analyze_mlb_lag.py`.
- **(B) PRE-GAME order-flow / "smart money" = NO edge.** Big money DOES flow in
  pre-game (OI grows 40-450% per game) but the price does NOT move: of 24 OI-jump
  events (>=10% or >=5k sh), only 3 (12%) moved price >=0.5c, and continuation was
  1 vs 21 flat. Pre-game drift predicted the winner 4/7 (57%, coin flip). The deep
  two-sided book absorbs size at fair value -> no line movement to follow.
  `analyze_mlb_orderflow.py`.
=> CONCLUSION: Polymarket US MLB is EFFICIENT (pre-game AND in-play) on DEEP books
that absorb size without moving. No retail directional edge for our tools, from a
free model reference OR order flow. Reached for ~$0 (StatsAPI free + read-only PM).
Same lesson as tennis, now on a major/liquid market: **liquid PM US sports
moneylines are well-arbitraged.** Pattern across both candidates (niche+thin AND
major+deep) => LOW prior for any free-data directional edge in PM US sports
moneylines. The clean pipeline (discovery/bbo price/StatsAPI WP+outcomes/analyzers)
is reusable. Data-quality all validated (price_side=away confirmed by 27 outcomes).

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
