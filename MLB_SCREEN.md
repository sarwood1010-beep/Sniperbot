# MLB screen — go/no-go bar + method (set BEFORE building/spending)

Evaluating in-play **MLB on Polymarket US** as the next edge candidate after
tennis was ruled out. Two CHEAP screens gate everything; a real spend/build only
happens if BOTH pass. Paper-only throughout. Reuses `core.py` + the tennis
lag/convergence method (`analyze_edge_v2.py`, `diag_bookmovement.py`).

## Why MLB, and what "cheap" means here
- **Reference win-prob is FREE and already calibrated:** MLB StatsAPI publishes
  its own per-play win probability — `GET /api/v1/game/{gamePk}/winProbability`
  returns `homeTeamWinProbability` / `awayTeamWinProbability` per play, each
  stamped with `about.endTime` (ISO). Confirmed live from this box on a real game
  (Rays@RedSox 2026-07-17: WP walked 52→87→100 as it resolved). So — unlike
  tennis, where OUR model was the reference and it OVERSHOT — the efficiency
  screen uses a mature external WP, and data cost ≈ $0. **We do NOT build an MLB
  win-prob model in core.py until/unless the screen passes.**
- **Clean outcomes, unlike tennis:** StatsAPI final score + `status=Final` is a
  reliable settlement record. No "no outcomes" hole, no price-proxy guessing.
- In-season now (2026-07-18, full 15-game slate) → fast data collection.

## Operational reality (confirmed this session)
- **MLB StatsAPI: reachable from the local Windows box** (no auth). All WP +
  schedule + outcome analysis can be done locally on the FreeCAD python.
- **Polymarket international (gamma-api / clob.polymarket.com): 403 from the US
  box** (geo-blocked). Not usable here.
- **Polymarket US (`api.polymarket.us`): reachable from the US box but 401 (needs
  the Ed25519 API creds + SDK signing).** So the Polymarket half runs on the
  **droplet** (creds + `polymarket_us` SDK live there), same as the tennis work.
  User is the hands; downloads the log; assistant analyses locally.
- WS `wss://api.polymarket.us/v1/ws/markets` is the price source. The bot only
  ever subscribed `subscription_type: 2` = **MARKET_DATA_LITE** (top-of-book
  best_bid/best_ask only — NO depth ladder). The liquidity screen must probe the
  other subscription types for a full-depth book (`mlb_discover.py` does this).

## Anti-tennis-mistakes rules (baked into the method)
1. **Never trust the REST `market_p1/market_p2`** from `extract_aec_markets` — on
   tennis they were corrupted (p1≈p2, not complementary). Use the **WS book**
   (best_bid/best_ask → mid) as the market price.
2. **Capture REAL outcomes** — StatsAPI final score, not a last-snapshot proxy.
3. **No small/filtered subset** — cover ALL live MLB `aec-` markets across MANY
   distinct games; report N distinct games; do NOT gate on a "sane sum" filter
   (that biased tennis). Conclusions require breadth.

---

## THE GO/NO-GO BAR (both screens must pass)

### Screen 1 — Liquidity / depth  (the gate that thin books failed on tennis)
Tennis died partly on depth: token quotes ~10 shares; only rare markets had a
~1c spread. We want to scale past $2 flat bets, so the bar is stated in tradeable
size, sampled across several live games/innings (never one cherry-picked snapshot):

- **PASS** if, on the side we'd trade, at typical in-play moments:
  spread ≤ **3¢** AND ≥ **~$100 of resting size within 2¢ of the touch** (a
  $25–50 order fills at ~top-of-book), on a **majority** of sampled moments; and
  the market's OI/volume is clearly live (not a parked pre-game book).
- **NO-GO** if books look like tennis: top-of-book only tens of dollars / ~10
  shares, or spread routinely > **5¢**. Then STOP — can't trade size.

### Screen 2 — Efficiency / lag  (the thing that truly killed tennis)
Reuse the tennis lag metric. PM price = WS book mid; reference = StatsAPI live WP,
aligned in time. Across **≥ ~15 distinct live games** (breadth requirement):

- Divergence event: |PM − WP| ≥ **6 pts** (0.06). Focus on **FRESH** divergences
  created by a WP jump right after a play (run scores / big out) — the moment most
  likely to expose a lag.
- Metrics: over the next 1–10 min, `gap_closed` = fraction of the gap PM closes
  toward WP; `converge_rate` = % of events where PM moves toward WP.
- **PASS (edge)** if fresh post-play divergences show PM LAGGING: `gap_closed` ≥
  **+0.4** within ≤5 min AND `converge_rate` ≥ **60%**, AND the median capturable
  move exceeds the harsh `core.round_trip_cost` (spread + 2 fees + 2 slippage) by
  ≥ **2×** — i.e. net edge ≥ ~**2–3¢/trade** after costs.
- **NO-GO (efficient, like tennis)** if `gap_closed` ≈ 0 / `converge_rate` < ~50%,
  or PM LEADS WP (moves before the play resolves), or the net-of-cost edge doesn't
  clear the round-trip hurdle.

### Economic framing
StatsAPI is free, so the handoff's ">3–5× the data cost" rule is trivially met on
cost grounds. The binding constraints are therefore the two screens themselves:
(a) are the books deep enough to trade size, and (b) does a real, repeatable,
cost-clearing lag exist. If either fails → NO-GO, write it up, stop the spend
(exactly as tennis was closed for ~$0).

---

## Sequence (cheap screen is the gate; build second)
1. **Screen 1 first** — run `mlb_discover.py` on the droplet during a live game.
   It lists live MLB `aec-` markets and probes the WS subscription types for a
   real depth ladder. If books are tennis-thin → STOP, never build the collector.
2. **Only if liquidity passes** — build/run `mlb_collect.py` (WS book mid +
   StatsAPI WP + final outcome, matched by team+date, clean JSONL), collect a few
   nights across many games, then `analyze_mlb_lag.py` locally vs the bar above.
3. Only if BOTH pass — THEN consider an MLB win-prob function in core.py, the
   staged-funding plan, etc. Not before.
