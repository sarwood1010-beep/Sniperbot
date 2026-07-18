# HANDOFF — read this first (context-window bridge)

You are picking up a Polymarket US in-play **tennis** trading research project.
Goal: find a REAL, defined edge and grow principal slowly. **Paper only — never
enable live trading.** Read `FINDINGS.md` for the full journey; this file is the
state + the plan.

## THE DEFINED EDGE (decided) — pursue this, not the model
**"Polymarket US in-play tennis lags a sharp book (Pinnacle). Buy the side
Pinnacle prices higher than Polymarket does; profit as Polymarket converges."**
- Our home-grown win-prob model is a WEAK edge (it knows only the score; the
  market knows more and tracks the score at +0.64 corr). Do NOT rely on it as the
  edge — keep it only as a sanity check.
- Sharp reference = **The Odds API, Business tier ($99/mo, 200k req/mo)** which
  includes **Pinnacle** in-play tennis match-winner odds. Legal to CONSUME as
  data (we trade on Polymarket, not Pinnacle). See `RESEARCH_sharp_reference.md`.

## HARD CONSTRAINTS
1. Never enable live trading (default paper). 2. Never commit `.env`/secrets.
3. `config.json` is gitignored + runtime-mutable. 4. `py_compile` must pass
before deploy. 5. Loud failure over silent degradation.

## STATE
- **Bot is deployed & running in PAPER on the droplet**, on the `hardening`
  branch (NOT merged to main — main is the instant rollback). Hardening is done:
  R1 sell-bug fixed, tested `core.py`, structured logging, budget-safe
  measurement loop, `/edge` `/feedtest` commands, deploy runbook.
- Feed: RapidAPI tennis (`tennis-api-atp-wta-itf`), key in droplet `.env` as
  `RAPIDAPI_KEY`. Endpoint `/tennis/v2/extend/api/events/live` = all live matches.
  Free tier 50/day; **user is upgrading to Pro $29/mo (150k/mo)**.
- Measurement loop logs to `/home/deploy/polymarket-discord-bot/measurement_log.jsonl`.

## THE BLOCKER (must fix before any edge can be measured)
Our logged market price is CORRUPTED (confirmed by analyzing the downloaded log):
- `market_p1` and `market_p2` from `extract_aec_markets` (REST `marketSides`
  "price") are near-DUPLICATES (mean |diff| 0.07), NOT complementary. `market_p2`
  and `market_sum` are garbage. The `healthy()` sum-in-[0.9,1.1] filter therefore
  selected a broken subset -> ALL prior calibration/Brier/edge results are VOID.
- Settlement prices flip/collapse -> cannot extract winners from price; completed
  scores almost never captured (1/71) -> NO reliable outcomes.
- **What IS good:** `market_p1` during play tracks the score (venue is ALIVE);
  the WS order book (`best_bid`/`best_ask`, and `longQuote`/`shortQuote` in `/raw`)
  IS complementary and is the CLEAN price.

## PLAN FOR THIS NEW WINDOW (execution)
1. **Fix the clean Polymarket price:** use the WS order-book mid (from
   `best_bid`/`best_ask`, mapping long/short to the right player via `sides_info`)
   as the market price, NOT the REST `marketSides` price. Expand WS book coverage
   if needed. Drop `market_p2`/`market_sum`-based filters.
2. **Add the Pinnacle reference:** integrate The Odds API (Business) in-play
   tennis. Match its match to the Polymarket market by player names (reuse
   `core.match_market_to_feed`). Log Pinnacle implied prob (de-vigged) alongside
   the Polymarket book price, per match, over time.
3. **Measure the edge (no model, no outcomes needed for the core signal):** does
   Polymarket's price lag Pinnacle's? By how much, and does Polymarket converge
   toward Pinnacle? Also check LIQUIDITY (book depth) — a real-but-untradeable
   gap is not an edge.
4. Only after a clean gap is confirmed: paper-trade the gap with the harsh fill
   sim (`core.simulate_fill`) + settlement P&L. Then the staged funding plan.

## OPEN DECISION FOR THE USER
Spend **$99/mo** for The Odds API Business (Pinnacle) to pursue edge #3 (strong
prior) — RECOMMENDED — vs. keep trying to salvage the model (weak). User is
leaning "prove then fund"; wants an edge defined before more building (done: it's
#3). Confirm the $99/mo spend before integrating Pinnacle.

## OPERATIONAL FACTS (critical for a fresh context)
- **No droplet access** for the assistant. User runs commands (Termius) or
  downloads files. To analyze the log locally: user downloads
  `measurement_log.jsonl` to their PC (last at
  `C:\Users\a00579503\Downloads\measurement_log.jsonl`), assistant reads it.
- **Repo is LOCAL at `C:\Users\a00579503\Documents\Sniperbot`** (shell cwd
  defaults to the Git install dir — always use absolute paths).
- **No usable Python locally except FreeCAD's:**
  `"C:\Program Files\FreeCAD 1.0\bin\python.exe"` (3.11, stdlib only). Use it for
  `py_compile` and running the stdlib `unittest`/analysis scripts.
- **`git push` fails from the Bash tool (broken PATH); use the PowerShell tool**
  for git commit/push. (Its output wraps git stderr as a red "error" but exit 0 =
  success; check for the `->` ref line.)
- **Deploy** = user pulls the `hardening` branch on the droplet + restarts; see
  `DEPLOY_RUNBOOK.md`. Droplet loads secrets via
  `EnvironmentFile=/home/deploy/polymarket-discord-bot/.env`; `load_dotenv(
  override=True)` so `.env` wins. Feed calls need `User-Agent` header (backend
  blocks urllib default).
- Analysis tools (run on droplet or locally): `analyze_edge.py`,
  `analyze_edge_v2.py`, `analyze_calibration.py`, `diag_bookmovement.py`
  (proved venue alive), `diag_resolved.py`. Note: all outcome-based results are
  VOID until the price/outcome bugs are fixed.
- Memory files (persist across sessions): `sniperbot-env.md`,
  `sniperbot-hardening.md` under the project memory dir.

## FIRST MESSAGE FOR THE NEW WINDOW
"Continue the Sniperbot tennis edge project. Read HANDOFF.md and FINDINGS.md.
The plan: fix the clean Polymarket WS-book price, add the Pinnacle reference (The
Odds API), and measure whether Polymarket lags Pinnacle. Confirm the $99/mo spend
first, then start with step 1 (clean price)."
