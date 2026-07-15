# Deploy Runbook — first deploy of the `hardening` work

Plain-English, copy-paste steps to deploy tonight. Do them **together** so we can
watch the logs. Nothing here places a real trade — the bot starts in **paper**,
and the new edge-measurement is **read-only**.

## What this deploy contains
- Safety fixes: the live-sell bug fixed, loud structured logging, the bot rewired
  onto the tested `core.py` brain.
- The tennis fair-value model + the Stage-1 **edge measurement** (reads the tennis
  feed, compares the model to Polymarket's price, logs the result). Measurement is
  gated on `RAPIDAPI_KEY` and never trades.

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
- `/config` → should show the new `measure_*` keys.
- `/edge` → recent model-vs-market records (may say "no records yet" until a
  listed match goes live — that's expected).

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
