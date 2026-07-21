# Deploy Runbook — first deploy of the `hardening` work

> **>>> STATUS 2026-07-20: `sniper-bot` is INTENTIONALLY STOPPED + DISABLED. <<<**
> Tennis is RULED OUT, so the bot's tennis loops were deliberately parked — it was
> pinging Polymarket's atp/wta discovery search on a timer (seen hitting HTTP 429)
> AND the RapidAPI tennis feed. Parked with:
> `sudo systemctl stop sniper-bot && sudo systemctl disable sniper-bot`.
> Do NOT restart it expecting useful work — the current effort (MLB, then
> cross-venue PM-vs-Kalshi) runs as standalone `mlb_*.py` scripts, NOT the bot.
> It's only the paper-trading harness, kept for possible later reuse; revive with
> `sudo systemctl enable --now sniper-bot` if a future strategy needs it. (The
> RapidAPI tennis subscription should also be cancelled on RapidAPI's site — that
> is separate from stopping the pings.)

Plain-English, copy-paste steps to deploy tonight. Do them **together** so we can
watch the logs. Nothing here places a real trade — the bot starts in **paper**,
and the new edge-measurement is **read-only**.

## What this deploy contains
- Safety fixes: the live-sell bug fixed, loud structured logging, the bot rewired
  onto the tested `core.py` brain.
- The tennis fair-value model + the Stage-1 **edge measurement** (reads the tennis
  feed, compares the model to Polymarket's price, logs the result). Measurement is
  gated on `RAPIDAPI_KEY` and never trades.

## Deploy the MLB x-venue PAPER arb model (Discord-alerting, like sniper-bot)
`mlb_arb_paper.py` is READ-ONLY (simulated fills, NO real orders) — safe to run as
a service. It posts to Discord and by default **REUSES the sniper-bot's existing
`DISCORD_TOKEN` + `ALERTS_CHANNEL_ID`** already in `.env` (REST posting; works even
while sniper-bot is stopped). So NO webhook and no new setup is needed.
1. Discord: nothing to do — it auto-uses `DISCORD_TOKEN` + `ALERTS_CHANNEL_ID`.
   - To post to a DIFFERENT channel, set `MLB_ARB_CHANNEL_ID=<id>` in `.env`.
   - To use a standalone webhook instead, set `DISCORD_WEBHOOK_URL=<real url>`.
   - If none resolve, it just logs locally (still fully functional).
   - (Remove any bogus placeholder line, e.g. `DISCORD_WEBHOOK_URL=<paste-real-url>`:
     `sed -i '/DISCORD_WEBHOOK_URL/d' /home/deploy/polymarket-discord-bot/.env`)
   The startup log line prints `discord=bot->chan <id>` / `webhook` / `off`.
2. Install + start the service:
     `sudo cp /home/deploy/polymarket-discord-bot/mlb-arb-paper.service /etc/systemd/system/`
     `sudo systemctl daemon-reload`
     `sudo systemctl enable --now mlb-arb-paper`
     `journalctl -u mlb-arb-paper -f`   # watch it; or tail mlb_arb_paper.log
3. It idles when no games are live and detects during games. Args in the unit:
   `run_min(0=forever) interval_s latency_s entry_cents` (default `0 3 4 2`).
   Data -> `mlb_arb_paper.jsonl`; download to analyze capture-rate + paper P&L.
4. Stop/remove: `sudo systemctl disable --now mlb-arb-paper`.
NOTE: this is the PAPER pressure-test of the one real edge found (FINDINGS
"CROSS-VENUE"). It NEVER places real orders. Going live would be a separate,
explicit, user-driven decision — not this service.

## Strategy: deploy the BRANCH, keep `main` as the rollback
We put the new code on the droplet by switching it to the `hardening` branch.
`main` (your current known-good code) stays untouched, so rolling back is one
command. Once we've watched it run cleanly for a day or two, we make it official
by merging `hardening` into `main`.

---

## Pre-flight (before touching the droplet)
1. On GitHub, open the repo → **Actions** tab. The latest `hardening` commit
   should show a **green check** (py_compile + 91 tests passed in CI).
2. That's the gate. If it's red, STOP and tell me.

---

## Deploy steps (run on the droplet over SSH)

**1. Go to the bot folder:**
```
cd /home/deploy/polymarket-discord-bot
```

**2. Check the working tree is clean** (no leftover hand-edits):
```
git status
```
- Expected: `nothing to commit, working tree clean` (ignoring untracked runtime
  files like `config.json`, `signal_history.json`).
- ⚠️ If it lists **modified `bot.py`** (or other tracked files), that means there
  are un-pushed hand-patches on the droplet. **STOP and tell me** — we must not
  pull over them. (We'll reconcile first.)

**3. Get the new code and switch to the branch:**
```
git fetch origin
git checkout hardening
git pull origin hardening
```

**4. Final gate — run the checks in the real environment** (the droplet venv has
all the libraries):
```
venv/bin/python -m py_compile bot.py core.py
venv/bin/python -m unittest discover -s tests -t .
```
- `py_compile` must print nothing (success).
- The tests must end with `OK (expected failures=1)` — the 1 expected failure is
  the documented trail-stop bug, on purpose.
- ⚠️ If either fails, **do not restart.** Roll back (see below) and tell me.

**5. Add your tennis key to `.env`** (only if not already there):
```
nano .env
```
Add one new line at the bottom (use your regenerated key):
```
RAPIDAPI_KEY=your_new_key_here
```
Save: `Ctrl+O`, Enter, then `Ctrl+X`. (Don't change the other lines.)

**6. Restart the bot:**
```
sudo systemctl restart sniper-bot
```

**7. Watch the logs live:**
```
journalctl -u sniper-bot -f
```
What GOOD looks like (within ~30–60s):
- A startup line and the Discord "THE SHARP v16.15 online" banner.
- `discovery.tick subscribed=N` lines.
- `measure.tick feed_live=… pm_markets=… matched=…` lines (proves the feed +
  measurement are alive). `matched=0` is fine when no Polymarket-listed match is
  live right now.
- You should **NOT** see `measure.disabled` (that would mean the key isn't being
  read — recheck step 5).
Press `Ctrl+C` to stop watching the logs (the bot keeps running).

**8. Verify from Discord:**
- `/status` → should say **PAPER**, WS connected.
- `/config` → should show the new `measure_*` keys and `feed_daily_cap`.
- `/feedtest` → makes ONE tennis-feed call to confirm the key works from the bot
  and shows how many feed calls you've used today. (Free tier = 50/day; my
  testing already used ~25 today, so keep this to a couple of runs.)
- `/edge` → recent model-vs-market records. Likely "no records yet": the current
  Polymarket matches are dated tomorrow/next day, so the loop will log
  `measure.idle` and spend **zero** feed calls until a listed match is dated
  today. That's the budget guard working, not a fault.

---

## Rollback (if anything looks wrong at any point)
```
cd /home/deploy/polymarket-discord-bot
git checkout main
sudo systemctl restart sniper-bot
```
That instantly returns to your previous known-good code. Then tell me what you saw.

---

## Safety reminders
- The bot starts in **PAPER**. Do **not** run `/golive` — Stage 1 is paper-only.
- The measurement loop is **read-only**; it places no orders.
- If the bot starts **paused** with a "startup reconciliation" alert about
  positions it doesn't recognize, that's the safety net doing its job — leave it
  paused and tell me; don't `/resume` until we've looked.
- Evidence accumulates in `measurement_log.jsonl` and via `/edge`. Stage 1 is
  just: let it run, watch the model-vs-market gap, and see if a real edge shows up.
