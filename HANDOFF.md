# HANDOFF — read this first (context-window bridge)

You are picking up a Polymarket US sports-trading research project. Goal: find a
REAL, defined edge and grow principal slowly. **Paper only — never enable live
trading. Never commit secrets.** Full history in `FINDINGS.md`.

## STATUS: Tennis RULED OUT. MLB RULED OUT (2026-07-19). Decision point: where next?
## MLB: Screen 1 (liquidity) PASSED (deep books) but BOTH edge hypotheses FAILED on a
## full slate — (A) in-play PM does NOT lag the win-prob (efficient, like tennis);
## (B) pre-game big money doesn't move the price (no order-flow edge). See FINDINGS
## "MLB: RULED OUT". Two candidates (niche+thin, major+deep) both efficient => LOW
## prior for a free-data directional edge in PM US sports moneylines. Open options in
## the "WHERE NEXT" section below; recommend NOT spending on the $99 sharp feed yet.
Tennis (in-play, Polymarket US) has no retail edge for our tools — the market is
efficient (it beats our win-prob model and does NOT lag it) and the books are
thin. Proven directly from data, ~$0 trading loss. See FINDINGS.md "TENNIS: RULED
OUT". The RapidAPI tennis sub should be cancelled; do NOT buy Pinnacle.

## THE LESSON (what killed tennis = what to screen a new sport for)
1. **Liquidity/depth** — tennis books were thin (token quotes ~10 shares). Need a
   market with REAL depth to trade size. Major US sports (MLB/NBA/NFL) likely
   deeper on Polymarket than niche tennis.
2. **Market inefficiency** — the tennis market was EFFICIENT (didn't lag a good
   estimate). An edge needs the Polymarket price to LAG a good win-prob or a
   sharp reference. This is the #1 thing to MEASURE, not assume.
3. **A genuinely accurate model** — our tennis model OVERSHOT (worse than market).
   Pick a sport with mature, calibrated public win-prob so our estimate is right.

## WHY MLB IS THE RECOMMENDED NEXT TEST
- **Free, excellent live data:** MLB StatsAPI (statsapi.mlb.com) is public and
  detailed — live inning/score/outs/baserunners/count, and MLB even publishes its
  own win probability. No paid feed needed to start.
- **Mature, accurate live win-prob models** (FanGraphs et al.) — so OUR estimate
  would be calibrated, unlike the tennis model.
- **Likely more liquid** (major US sport) and **in-season now** (daily games ->
  fast data collection). Discrete events (runs/innings) = repricing moments.
- Caveat: MLB was cut by the OLD bot (PF 0.68) — but under the edge-LESS
  mean-reversion strategy that lost everywhere; NOT informative about a real edge.
- Alternatives: NBA (liquid, mature model, but OFF-SEASON in July); NFL (most
  liquid, but weekly/seasonal, starts Sept); soccer (inefficiency potential but
  messy Polymarket market structure — ~328 prop markets per match).

## METHODOLOGY FOR NEXT WINDOW (reuse the toolkit; CHEAP checks FIRST)
Do NOT build heavy or spend money before the two cheap screens pass:
1. **Liquidity screen:** on a live MLB game, inspect the Polymarket order book
   depth (via the bot's WS `/raw` or a direct read). Is there real size, not a
   token quote? If books are as thin as tennis, stop.
2. **Efficiency screen:** compare a good MLB live win-prob (MLB StatsAPI's own, or
   a public model) to Polymarket's price over a few games. Does Polymarket LAG it
   (edge) or track it (efficient, like tennis)? Reuse the lag/convergence method
   from `analyze_edge_v2.py` / the tennis analysis.
3. Set a GO/NO-GO bar UP FRONT (e.g., a lag that plausibly nets >3-5x any data
   cost at realistic depth). Below bar -> stop, report, don't sink more time.
4. Only if both screens pass: adapt the measurement loop (new feed + a `core.py`
   MLB win-prob function + matching by team/date), gather clean data, paper-trade
   with the harsh fill sim, then the staged funding plan.

## REUSABLE ASSETS (all built + tested)
- `core.py` — pure, unit-tested decision logic (edge_signal, simulate_fill,
  round_trip_cost, match_market_to_feed, build_measurement_record pattern). Add an
  MLB win-prob function here (game state -> P(win)).
- `tests/test_core.py`, `.github/workflows/ci.yml` (py_compile + pytest gate).
- Analysis tools: `analyze_edge_v2.py`, `analyze_calibration.py`,
  `diag_bookmovement.py` (proves venue alive/efficient), `diag_resolved.py`.
- Bot: hardened, deployed in PAPER on `hardening` branch. Measurement loop is
  budget-safe + read-only; adaptable to a new feed. `DEPLOY_RUNBOOK.md`.

## KNOWN BUGS to fix if reusing the measurement loop for MLB
- The REST `market_p1`/`market_p2`/`market_sum` from `extract_aec_markets` are
  CORRUPTED (p1~=p2, not complementary). Use the WS order-book (`best_bid`/
  `best_ask`) as the market price, NOT the REST marketSides "price".
- No reliable OUTCOME capture — add a clean settlement record (real game result),
  not a last-snapshot price proxy.

## OPERATIONAL FACTS (critical for a fresh context)
- **No droplet access.** User runs commands (Termius) / downloads files. To
  analyze data locally: user downloads the log to their PC (last at
  `C:\Users\a00579503\Downloads\`), assistant reads it directly.
- **Repo LOCAL at `C:\Users\a00579503\Documents\Sniperbot`** (shell cwd defaults
  to the Git install dir — use absolute paths).
- **Only usable local Python is FreeCAD's:** `"C:\Program Files\FreeCAD 1.0\bin\
  python.exe"` (3.11, stdlib). Use for py_compile + running unittest/analysis.
- **`git push` is broken from the Bash tool — use the PowerShell tool for git.**
  (Its output wraps git stderr as a red "error"; exit 0 + a `->` ref line = ok.)
- Deploy = user pulls `hardening` on the droplet + restarts (DEPLOY_RUNBOOK.md).
  `.env` via systemd EnvironmentFile; `load_dotenv(override=True)`; feed HTTP
  needs a `User-Agent` header.
- Memory files persist: `sniperbot-env.md`, `sniperbot-hardening.md`.

## MLB PROGRESS (as of 2026-07-18)
- `MLB_SCREEN.md` = go/no-go plan (MLB StatsAPI is free + publishes per-play win
  probability -> free calibrated reference + clean outcomes). `mlb_discover.py` =
  Screen-1 liquidity probe (read-only WS MARKET_DATA_LITE + depth-ladder type
  probe). BUILT + committed.
- First run (afternoon 07-18) found the full MLB slate but only FUTURE-dated
  pre-game markets: `aec-mlb-{away}-{home}-{date}`, sides = FULL team names
  ("San Francisco Giants" ...) -> clean StatsAPI match, no tennis-style ordering
  pain. But no live game yet -> no real liquidity read.
- `mlb_discover.py` NOW flags each market TODAY/YEST/OTHER by the ET date in the
  slug (commit a17604f), sorts TODAY-first (so a live game is never truncated out
  of the WS sub or the type-probe), and prints a loud WARNING if 0 markets are
  dated today. So the afternoon "future-only" risk is instrumented, not silent.
- **SCREEN 1 (LIQUIDITY): PASSED (07-19, live games).** In-play books are DEEP +
  TIGHT — the opposite of tennis. SF@SEA & PIT@CLE (live 07-18): spreads 0.5c,
  $1.5k–$180k resting within 2c/side (bar = >=~$100). Full detail in FINDINGS.md
  "MLB — SCREEN 1: PASSED". Reached via `mlb_book_probe.py` (REST).
- HOW TO REACH A LIVE MARKET (discovery solved):
  - Search `query:"mlb"` EXCLUDES same-day markets (only lists games ~2-3 days
    out) — do NOT use it for live. Build the slug instead:
    StatsAPI `abbreviation`.lower() == Polymarket slug token for all 30 teams ->
    `aec-mlb-{away}-{home}-{ET-date}`, then `pm.markets.retrieve_by_slug(slug)`.
  - Clean PRICE = `pm.markets.bbo(slug)` (bestBid/bestAsk/currentPx/OI/shares) —
    replaces the corrupted tennis marketSides price. Depth = `pm.markets.book(slug)`
    ({"marketData":{"bids":[{"px":{"value":..},"qty":..}],"offers":[...]}}).
  - Clean OUTCOME = StatsAPI final + status=Final. (`pm.markets.settlement` was
    "not found" mid-game; don't rely on it.)
  - WS lite feed (subscription_type 2) sends 0 frames for same-day slugs — it's
    NOT a live in-play source for MLB; use the REST book. (`mlb_discover.py` /
    `mlb_live_probe.py` WS probes are superseded for the live read.)
- SCREEN 2 (efficiency/lag) — IN PROGRESS. `mlb_collect.py` BUILT + committed
  (StatsAPI half tested live). Run on the droplet DURING live games:
    cd /home/deploy/polymarket-discord-bot && git pull origin hardening
    venv/bin/python mlb_collect.py 120 20   # run 120 min, poll 20s; safe to Ctrl-C
    cp mlb_data.jsonl /root/                 # then user downloads it
  Records (JSONL): type=tick {poll_ts, slug, gamePk, best_bid/ask, currentPx,
  wp_home/away, wp_endTime, inning/half/score} and type=settlement {winner, final
  score, final book}. price_side="away" ASSUMED (currentPx~=P(away)); analysis
  self-calibrates via settlement (winner px -> ~1.0). Collect a few nights.
- CRITICAL for the analysis (measured 07-19): StatsAPI WP is ~20-45s behind wall-
  clock. So "PM leads WP" is likely a REFERENCE-LATENCY ARTIFACT, not efficiency —
  the correct test measures PM's move AFTER WP has jumped; any residual PM lag is a
  lag BEYOND ~30s = robust/tradeable. See FINDINGS.md "Screen 2 — the TIMING
  subtlety". Deep books (Screen 1) mean the PRIOR is "probably efficient".
- `analyze_mlb_lag.py` BUILT + run on the first overnight file. FIRST READ leans
  EFFICIENT but is NOT a verdict (sample too thin): 0 fresh divergences over 11 WP
  jumps; the one moving game (SF@SEA, extra innings) had PM shadowing WP ~1:1,
  |gap| median 2.8pts, no closing lag. Small stable PM<WP ~2pt bias (< cost; watch,
  don't chase). Full detail in FINDINGS.md "Screen 2 — FIRST READ".
- WHY THIN + THE FIX: collection started 10:36pm ET, so most games were already
  Final (only 4 had live ticks). **Start the collector BEFORE first pitch** and run
  long enough to cover the slate:
    nohup venv/bin/python mlb_collect.py 480 20 > mlb_collect.log 2>&1 &
  Collect a few such nights -> re-run analyze_mlb_lag.py -> read vs the Screen-2 bar.
  (Analyzer auto-drops doubleheaders + dedups restart re-emissions.)
- SECOND HYPOTHESIS (free, same data) — TESTED, NO edge. `mlb_collect.py` also polls
  PRE-GAME markets; `analyze_mlb_orderflow.py` ran on the full slate: big pre-game
  money (OI +40-450%/game) does NOT move the price (3/24 jumps moved it >=0.5c; no
  continuation); pre-game drift predicted winners 57% (n=7). Deep book absorbs size
  at fair value. NO order-flow edge. Details in FINDINGS "MLB: RULED OUT (B)".

## WHERE NEXT (decision point — both sport candidates efficient)
Tennis (niche/thin) AND MLB (major/deep) are both EFFICIENT on PM US -> strong
evidence liquid PM US sports moneylines are well-arbitraged. Honest options:
1. **STOP the sports-moneyline hunt (recommended default).** Two disciplined
   negatives for ~$0. The prior for a free-data directional edge here is now LOW.
   Reusable assets remain (hardened bot, core.py, full MLB pipeline + analyzers).
2. **$99 sharp feed (The Odds API / Pinnacle) — LOWER priority now.** The in-play
   efficiency result implies PM ~= sharp consensus already (the deep book is likely
   set by people watching Pinnacle), so PM lagging Pinnacle by a tradeable margin is
   unlikely. Only worth it as a cheap 1-month kill-test if strongly motivated.
3. **Different market TYPE, not moneylines.** Less-efficient corners (player props,
   less-liquid same-day markets, other event contracts) — but those reintroduce the
   tennis thin-book problem (can't trade size) and often are still efficient/messy.
4. **Different edge MECHANISM.** Not directional: market-making the spread (pro game
   at 0.5c spreads), or cross-venue arb PM US vs Kalshi vs books (latency/infra
   game). Both are a different project than "find a lagging price."
Recommendation: call MLB done; decide 1 vs a scoped test of 3/4 with the user. Do
NOT reflexively collect more nights — the signals aren't borderline (converge 46%
vs 60% bar; OI jumps move price 12%), more data won't flip them.

## PURSUING IDEA #1 — cross-venue arb (Polymarket US vs KALSHI). IN PROGRESS.
Different mechanism: not "PM lags a reference" (dead) but "two tradeable US venues
DISAGREE on the same game-winner binary" — model-free, speed-insensitive (a
standing gap is capturable at leisure), both legs tradeable (unlike Pinnacle).
Confirmed (2026-07-19, all free from the LOCAL box — Kalshi needs no droplet):
- **Kalshi public API**: `https://api.elections.kalshi.com/trade-api/v2` (no auth,
  not geo-blocked). Series `KXMLBGAME` = per-game winner, one market per SIDE.
  Ticker `KXMLBGAME-{YYMONDDHHMM}{MATCHUP}-{SIDE}`; team codes == StatsAPI abbr
  (MIA,CWS,SD,ATH,AZ,WSH...). P(away) = the -{AWAY} market's yes_bid/ask. Prices
  are in the `_dollars`/`_fp` fields (old yes_bid/volume are None).
- **Liquidity**: far-future games thin/wide (2-24c); NEAR-TERM/live games are TIGHT
  (~1c spreads, 12k-154k volume) — comparable to PM. So live cross-venue gaps are
  cleanly capturable.
- **`mlb_xvenue.py` BUILT** (Kalshi half tested locally; mapping perfect). Polls PM
  bbo + Kalshi KXMLBGAME per live game, logs both venues' bid/ask + gap_mid + the
  two executable arbs (arb_buyPM_sellK = k_bid-pm_ask; arb_buyK_sellPM = pm_bid-
  k_ask; >0 after fees = locked edge). Run on the DROPLET during live games:
    cd /home/deploy/polymarket-discord-bot && git pull origin hardening
    nohup venv/bin/python mlb_xvenue.py 300 20 > mlb_xvenue.log 2>&1 &
    # then: cp mlb_xvenue.jsonl /root/  and download
  NEXT after data: analyze the gap distribution vs round-trip cost on BOTH legs
  (PM ~0.5c + Kalshi ~1c + fees). Edge = gap frequently exceeds total cost. Need
  to confirm Kalshi's fee schedule (maker/taker) for the cost model. Also the
  gap's SIGN persistence (does one venue systematically lead?) informs execution.

## FIRST MESSAGE FOR THE NEW WINDOW (paste this)
"Continue the Sniperbot project. Tennis is ruled out (see HANDOFF.md + FINDINGS.md
in C:\Users\a00579503\Documents\Sniperbot). Now evaluate MLB on Polymarket US.
Start with the two CHEAP screens before building/spending: (1) is the Polymarket
MLB in-play order book actually LIQUID (real depth)? (2) does the Polymarket price
LAG a good MLB live win-prob (MLB StatsAPI is free) or track it efficiently like
tennis did? Reuse the analysis toolkit and the lag/convergence method. Set a
go/no-go bar first. Don't repeat the tennis mistakes (corrupted REST prices, no
outcomes, over-concluding from a small/filtered subset)."
