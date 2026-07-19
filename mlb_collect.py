#!/usr/bin/env python3
"""mlb_collect.py -- READ-ONLY collector for MLB on Polymarket US. Places NO orders.
Gathers clean, timestamped data to test TWO edge hypotheses from one run:
  (A) IN-PLAY lag: does PM price lag the StatsAPI live win-prob? (Screen 2)
  (B) PRE-GAME order-flow: does a big pre-game openInterest jump ("smart money")
      precede PM price continuation? Poll pregame OI/price; flag >=10% OI jumps.
Every tick is tagged game_state = "live" | "pregame" so analysis can split them.

Screen 2 (see MLB_SCREEN.md) asks: does the Polymarket in-play price LAG the
StatsAPI live win-probability (edge) or track it (efficient, like tennis)? The
DECISIVE subtlety is TIMING: PM must lag a reference that is itself timely. So
every record carries three clocks -- poll wall-time, the WP play's about.endTime,
and the score/inning state -- so analysis can separate "PM is efficient" from
"our WP reference is just slow" (a data-latency artifact, not a real no-edge).

Design (all read-only):
  * Discover live games from StatsAPI (status In Progress); build the slug
    aec-mlb-{away}-{home}-{ET-date} (abbreviation.lower() == PM slug token).
  * Every INTERVAL seconds, for each live game, one poll:
      - pm.markets.bbo(slug) -> best_bid/best_ask/currentPx/OI/sharesTraded (CLEAN
        price; replaces the corrupted tennis marketSides).
      - StatsAPI winProbability latest play -> home/awayWP + about.endTime.
      - StatsAPI linescore -> inning/half/score/outs (context + play detection).
    Append one JSONL record to mlb_data.jsonl.
  * On a game going Final, append a settlement record (winner + final score from
    StatsAPI -- the clean outcome; PM's own settlement endpoint is unreliable).

price_side convention: the aec-mlb binary market is priced for the FIRST-named
(AWAY) team, so currentPx ~= P(away wins). We LOG this assumption but do NOT trust
it -- analysis self-calibrates via the settled outcome (winner's price -> ~1.0).

Run on the DROPLET during live games (bounded; safe to Ctrl-C):
    venv/bin/python mlb_collect.py [minutes] [interval_sec]
    e.g. venv/bin/python mlb_collect.py 120 20
"""
import os, sys, json, time, urllib.request
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    from polymarket_us import PolymarketUS
except Exception as e:
    print("import error (run on the droplet venv):", e); sys.exit(1)

load_dotenv(override=True)
OUTFILE = "mlb_data.jsonl"

def now_utc():
    return datetime.now(timezone.utc)

def log(*a):
    print(f"[{now_utc().strftime('%H:%M:%S')}]", *a, flush=True)

def emit(rec):
    with open(OUTFILE, "a") as f:
        f.write(json.dumps(rec) + "\n")

def find_env(names):
    for n in names:
        v = os.environ.get(n)
        if v: return v
    return None
key_id = find_env(["POLYMARKET_KEY_ID", "POLYMARKET_US_KEY_ID"])
secret = find_env(["POLYMARKET_SECRET_KEY", "POLYMARKET_US_SECRET"])
pm = PolymarketUS(key_id=key_id, secret_key=secret) if key_id and secret else PolymarketUS()

def as_dict(o):
    if isinstance(o, dict): return o
    for m in ("to_dict", "model_dump"):
        if hasattr(o, m):
            try: return getattr(o, m)()
            except Exception: pass
    if hasattr(o, "__dict__"): return dict(vars(o))
    return {}

def fnum(x):
    if isinstance(x, dict): x = x.get("value", x.get("price", x.get("size")))
    try: return float(x)
    except Exception: return None

# ---- StatsAPI helpers (public, no auth) -------------------------------------
def sget(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=20))

def et_date():
    return (now_utc() - timedelta(hours=4)).strftime("%Y-%m-%d")

def schedule(date):
    try:
        d = sget(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
                 "&hydrate=linescore,team")
    except Exception as e:
        log("StatsAPI schedule error:", e); return []
    dates = d.get("dates", [])
    if not dates: return []
    out = []
    for g in dates[0].get("games", []):
        st = g.get("status", {}).get("detailedState", "?")
        aw = g["teams"]["away"]["team"].get("abbreviation", "").lower()
        hm = g["teams"]["home"]["team"].get("abbreviation", "").lower()
        ls = g.get("linescore", {})
        out.append({
            "gamePk": g.get("gamePk"), "away": aw, "home": hm, "status": st,
            "live": st in ("In Progress", "Manager challenge"),
            "final": st in ("Final", "Game Over", "Completed Early"),
            "pregame": st in ("Scheduled", "Pre-Game", "Pre Game", "Warmup",
                              "Delayed Start", "Delayed", "Delayed: Rain"),
            "inning": ls.get("currentInning"), "half": ls.get("inningState"),
            "away_score": (ls.get("teams", {}).get("away", {}) or {}).get("runs"),
            "home_score": (ls.get("teams", {}).get("home", {}) or {}).get("runs"),
            "outs": ls.get("outs"),
            "start": g.get("gameDate"),  # ISO first-pitch time (UTC)
            "slug": f"aec-mlb-{aw}-{hm}-{date}",
        })
    return out

def latest_wp(gamePk):
    """Latest play's win probability + its about.endTime (the reference clock)."""
    try:
        wp = sget(f"https://statsapi.mlb.com/api/v1/game/{gamePk}/winProbability")
    except Exception:
        return None
    if not isinstance(wp, list) or not wp:
        return None
    p = wp[-1]
    ab = p.get("about", {})
    return {
        "wp_home": p.get("homeTeamWinProbability"),
        "wp_away": p.get("awayTeamWinProbability"),
        "wp_endTime": ab.get("endTime"),
        "wp_inning": ab.get("inning"), "wp_half": ab.get("halfInning"),
        "wp_nplays": len(wp),
    }

# ---- Polymarket price (clean) ----------------------------------------------
def bbo(slug):
    try:
        r = pm.markets.bbo(slug)
    except Exception as e:
        return {"bbo_err": str(e)[:120]}
    d = as_dict(r); d = as_dict(d.get("marketData", d))
    return {
        "best_bid": fnum(d.get("bestBid")), "best_ask": fnum(d.get("bestAsk")),
        "currentPx": fnum(d.get("currentPx")), "lastTradePx": fnum(d.get("lastTradePx")),
        "settlementPx": fnum(d.get("settlementPx")),
        "oi": fnum(d.get("openInterest")), "shares": fnum(d.get("sharesTraded")),
        "askDepth": d.get("askDepth"), "bidDepth": d.get("bidDepth"),
    }

def poll_game(g, state):
    """state = 'live' or 'pregame'. WP is only fetched live (no plays pre-game)."""
    rec = {"type": "tick", "poll_ts": now_utc().isoformat(), "slug": g["slug"],
           "gamePk": g["gamePk"], "away": g["away"], "home": g["home"],
           "status": g["status"], "game_state": state, "start": g.get("start"),
           "inning": g["inning"], "half": g["half"],
           "away_score": g["away_score"], "home_score": g["home_score"],
           "outs": g["outs"], "price_side": "away"}
    rec.update(bbo(g["slug"]))
    if state == "live":
        wp = latest_wp(g["gamePk"])
        if wp: rec.update(wp)
    emit(rec)
    return rec

def settle(g):
    rec = {"type": "settlement", "poll_ts": now_utc().isoformat(),
           "slug": g["slug"], "gamePk": g["gamePk"], "away": g["away"],
           "home": g["home"], "status": g["status"],
           "away_score": g["away_score"], "home_score": g["home_score"]}
    a, h = g["away_score"], g["home_score"]
    rec["winner"] = ("away" if a > h else "home") if (a is not None and h is not None and a != h) else "tie/unknown"
    # attach the final book snapshot (does PM price -> ~1.0 for the winner?)
    rec.update(bbo(g["slug"]))
    emit(rec)
    return rec

def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    log(f"MLB collector -> {OUTFILE}  (run {minutes}m, poll {interval}s)  READ-ONLY")
    if not (key_id and secret):
        log("!! no Polymarket US creds -- run on the droplet."); return
    end = time.time() + minutes * 60
    settled = set()
    ticks = 0
    prev_oi = {}   # slug -> last openInterest, to flag >=10% jumps live
    def oi_flag(slug, oi):
        p = prev_oi.get(slug)
        d = (100.0 * (oi - p) / p) if (oi and p) else 0.0
        if oi: prev_oi[slug] = oi
        return d, ("  <== OI +%.0f%%" % d if abs(d) >= 10 else "")
    while time.time() < end:
        loop_start = time.time()
        date = et_date()
        try:
            games = schedule(date)
        except Exception as e:
            log("schedule loop err:", e); games = []
        live = [g for g in games if g["live"]]
        pregame = [g for g in games if g.get("pregame")]
        # settlement records for games that just went Final
        for g in games:
            if g["final"] and g["slug"] not in settled:
                r = settle(g); settled.add(g["slug"])
                log(f"SETTLED {g['slug']} winner={r['winner']} "
                    f"{g['away_score']}-{g['home_score']} finalPx={r.get('currentPx')}")
        # LIVE polls (WP-lag hypothesis)
        for g in live:
            try:
                r = poll_game(g, "live"); ticks += 1
                gap = None
                if r.get("currentPx") is not None and r.get("wp_away") is not None:
                    gap = r["currentPx"] - r["wp_away"] / 100.0
                _, of = oi_flag(g["slug"], r.get("oi"))
                log(f"LIVE {g['slug'][:24]:24} {g['half'] or '':6} {g['inning'] or '?'} "
                    f"{g['away_score']}-{g['home_score']}  cur={r.get('currentPx')} "
                    f"wpAway={r.get('wp_away')} gap={None if gap is None else round(gap,3)}{of}")
            except Exception as e:
                log(f"poll err {g['slug']}: {repr(e)[:120]}")
        # PRE-GAME polls (order-flow / smart-money hypothesis)
        for g in pregame:
            try:
                r = poll_game(g, "pregame"); ticks += 1
                d, of = oi_flag(g["slug"], r.get("oi"))
                log(f"PRE  {g['slug'][:24]:24} start={str(g.get('start'))[11:16]} "
                    f"bid={r.get('best_bid')} ask={r.get('best_ask')} cur={r.get('currentPx')} "
                    f"oi={r.get('oi')} vol={r.get('shares')}{of}")
            except Exception as e:
                log(f"poll err {g['slug']}: {repr(e)[:120]}")
        if not live and not pregame:
            log(f"no live/pregame games ({len(games)} on slate). waiting...")
        # sleep the remainder of the interval
        dt = interval - (time.time() - loop_start)
        if dt > 0 and time.time() + dt < end:
            time.sleep(dt)
        elif dt > 0:
            break
    log(f"done. {ticks} ticks written to {OUTFILE}. settled={len(settled)}.")

if __name__ == "__main__":
    main()
