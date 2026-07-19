# HANDOFF — read this first (context-window bridge)

You are picking up a Polymarket US sports-trading research project. Goal: find a
REAL, defined edge and grow principal slowly. **Paper only — never enable live
trading. Never commit secrets.** Full history in `FINDINGS.md`.

## STATUS: Tennis RULED OUT. MLB Screen 1 (liquidity) PASSED. Next = Screen 2 (lag).
## MLB in-play books are deep+tight (unlike tennis). Now measure if PM price LAGS
## the free StatsAPI win-prob (edge) or tracks it (efficient). Build mlb_collect.py.
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
- NEXT (decisive) = SCREEN 2 (efficiency/lag). Build `mlb_collect.py`: on a
  schedule during live games, for each in-progress slug poll `bbo` (book mid) +
  StatsAPI live winProbability (GET /api/v1/game/{gamePk}/winProbability, stamped
  about.endTime) aligned in time, write clean JSONL; capture StatsAPI final as the
  outcome. Run a few nights, then `analyze_mlb_lag.py` vs MLB_SCREEN.md Screen-2
  bar (reuse `core.round_trip_cost` + the tennis lag/convergence method). PASS only
  if fresh post-play divergences show PM LAGGING WP by >= the cost hurdle.

## FIRST MESSAGE FOR THE NEW WINDOW (paste this)
"Continue the Sniperbot project. Tennis is ruled out (see HANDOFF.md + FINDINGS.md
in C:\Users\a00579503\Documents\Sniperbot). Now evaluate MLB on Polymarket US.
Start with the two CHEAP screens before building/spending: (1) is the Polymarket
MLB in-play order book actually LIQUID (real depth)? (2) does the Polymarket price
LAG a good MLB live win-prob (MLB StatsAPI is free) or track it efficiently like
tennis did? Reuse the analysis toolkit and the lag/convergence method. Set a
go/no-go bar first. Don't repeat the tennis mistakes (corrupted REST prices, no
outcomes, over-concluding from a small/filtered subset)."
