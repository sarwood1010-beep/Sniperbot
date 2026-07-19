#!/usr/bin/env python3
"""mlb_collect.py -- READ-ONLY Screen-2 collector for MLB on Polymarket US. Places
NO orders. Gathers the clean, timestamped data the lag/efficiency test needs.

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
            "final": st == "Final",
            "inning": ls.get("currentInning"), "half": ls.get("inningState"),
            "away_score": (ls.get("teams", {}).get("away", {}) or {}).get("runs"),
            "home_score": (ls.get("teams", {}).get("home", {}) or {}).get("runs"),
            "outs": ls.get("outs"),
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

def poll_game(g):
    poll_ts = now_utc().isoformat()
    rec = {"type": "tick", "poll_ts": poll_ts, "slug": g["slug"],
           "gamePk": g["gamePk"], "away": g["away"], "home": g["home"],
           "status": g["status"], "inning": g["inning"], "half": g["half"],
           "away_score": g["away_score"], "home_score": g["home_score"],
           "outs": g["outs"], "price_side": "away"}
    rec.update(bbo(g["slug"]))
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
    while time.time() < end:
        loop_start = time.time()
        date = et_date()
        try:
            games = schedule(date)
        except Exception as e:
            log("schedule loop err:", e); games = []
        live = [g for g in games if g["live"]]
        # settlement records for games that just went Final
        for g in games:
            if g["final"] and g["slug"] not in settled:
                r = settle(g); settled.add(g["slug"])
                log(f"SETTLED {g['slug']} winner={r['winner']} "
                    f"{g['away_score']}-{g['home_score']} finalPx={r.get('currentPx')}")
        for g in live:
            try:
                r = poll_game(g); ticks += 1
                gap = None
                if r.get("currentPx") is not None and r.get("wp_away") is not None:
                    gap = r["currentPx"] - r["wp_away"] / 100.0
                log(f"{g['slug'][:26]:26} {g['half'] or '':6} {g['inning'] or '?'} "
                    f"{g['away_score']}-{g['home_score']}  bid={r.get('best_bid')} "
                    f"ask={r.get('best_ask')} cur={r.get('currentPx')} "
                    f"wpAway={r.get('wp_away')} gap={None if gap is None else round(gap,3)}")
            except Exception as e:
                log(f"poll err {g['slug']}: {repr(e)[:120]}")
        if not live:
            log(f"no live games ({len(games)} on slate). waiting...")
        # sleep the remainder of the interval
        dt = interval - (time.time() - loop_start)
        if dt > 0 and time.time() + dt < end:
            time.sleep(dt)
        elif dt > 0:
            break
    log(f"done. {ticks} ticks written to {OUTFILE}. settled={len(settled)}.")

if __name__ == "__main__":
    main()
