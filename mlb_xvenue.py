#!/usr/bin/env python3
"""mlb_xvenue.py -- READ-ONLY cross-venue collector: Polymarket US vs Kalshi for
the SAME MLB games. Places NO orders. Tests edge idea #1 (cross-venue arb): two
independent, both-tradeable US event exchanges pricing the identical game-winner
binary. A gap beyond round-trip cost = a model-free, speed-insensitive edge.

Why this is different from everything ruled out: we don't need PM to LAG a
reference -- we need the TWO venues to DISAGREE, and both legs are tradeable.

Venues:
  * Polymarket US: aec-mlb-{away}-{home}-{date}; pm.markets.bbo -> bid/ask/cur.
    P(away). Needs creds -> DROPLET. Deep/tight ($100k+ OI, ~0.5c spread).
  * Kalshi: series KXMLBGAME, market ...MATCHUP-{SIDE}; PUBLIC api (no auth),
    api.elections.kalshi.com. Team codes == StatsAPI abbr (MIA, CWS, SD, ATH, AZ,
    WSH ...). P(away) = the -{AWAY} market's yes_bid/ask. Thinner/wider than PM.

Per live game each poll logs BOTH venues' quotes and the two executable arbs:
  arb_buyPM_sellK = kalshi_yes_bid - pm_ask   (buy away on PM, sell away on Kalshi)
  arb_buyK_sellPM = pm_bid       - kalshi_yes_ask
Either > 0 (after fees) = a locked cross-venue profit. Also gap_mid = pm_mid-k_mid.

Run on the DROPLET during live games:  venv/bin/python mlb_xvenue.py [min] [sec]
"""
import os, sys, json, time, urllib.request, urllib.error, re
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    from polymarket_us import PolymarketUS
except Exception as e:
    print("import error (run on the droplet venv):", e); sys.exit(1)

load_dotenv(override=True)
OUTFILE = "mlb_xvenue.jsonl"
KB = "https://api.elections.kalshi.com/trade-api/v2"

def now_utc(): return datetime.now(timezone.utc)
def log(*a): print(f"[{now_utc().strftime('%H:%M:%S')}]", *a, flush=True)
def emit(rec):
    with open(OUTFILE, "a") as f: f.write(json.dumps(rec) + "\n")

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

# ---- HTTP ------------------------------------------------------------------
def http(url, tmo=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=tmo))
    except Exception as e:
        return {"_err": str(e)[:150]}

# ---- StatsAPI slate --------------------------------------------------------
def et_date(): return (now_utc() - timedelta(hours=4)).strftime("%Y-%m-%d")
def schedule(date):
    d = http(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=linescore,team")
    out = []
    for g in (d.get("dates", [{}])[0].get("games", []) if d.get("dates") else []):
        st = g.get("status", {}).get("detailedState", "?")
        aw = g["teams"]["away"]["team"].get("abbreviation", "").lower()
        hm = g["teams"]["home"]["team"].get("abbreviation", "").lower()
        ls = g.get("linescore", {})
        out.append({"gamePk": g.get("gamePk"), "away": aw, "home": hm, "status": st,
                    "live": st in ("In Progress", "Manager challenge"),
                    "pregame": st in ("Scheduled", "Pre-Game", "Pre Game", "Warmup",
                                      "Delayed Start", "Delayed", "Delayed: Rain"),
                    "inning": ls.get("currentInning"), "half": ls.get("inningState"),
                    "slug": f"aec-mlb-{aw}-{hm}-{date}"})
    return out

# ---- Kalshi ----------------------------------------------------------------
_MONS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
def to_date7(d):  # '2026-07-20' -> '26JUL20' (Kalshi ticker date, in ET)
    y, m, dd = d.split("-"); return f"{y[2:]}{_MONS[int(m)-1]}{dd}"

def kalshi_index():
    """(date7, matchup, side) -> market, over all open KXMLBGAME markets (paged).
    Keying by DATE avoids matching a live game to a same-matchup future series game
    or the other half of a doubleheader."""
    idx = {}; cursor = ""
    for _ in range(6):
        u = f"{KB}/markets?series_ticker=KXMLBGAME&status=open&limit=200"
        if cursor: u += f"&cursor={cursor}"
        d = http(u)
        for m in d.get("markets", []):
            parts = m.get("ticker", "").split("-")
            if len(parts) < 3: continue
            mm = re.match(r"^(\d{2}[A-Z]{3}\d{2})\d{4}(.+)$", parts[1])  # date7 + matchup
            if not mm: continue
            idx[(mm.group(1), mm.group(2), parts[2])] = m
        cursor = d.get("cursor") or ""
        if not cursor: break
    return idx

def kalshi_quote(idx, away, home, date):
    m = idx.get((to_date7(date), away.upper() + home.upper(), away.upper()))  # P(away)
    if not m: return None
    return {"k_bid": fnum(m.get("yes_bid_dollars")), "k_ask": fnum(m.get("yes_ask_dollars")),
            "k_last": fnum(m.get("last_price_dollars")), "k_vol": fnum(m.get("volume_fp")),
            "k_oi": fnum(m.get("open_interest_fp")), "k_ticker": m.get("ticker")}

# ---- Polymarket ------------------------------------------------------------
def pm_quote(slug):
    try: r = pm.markets.bbo(slug)
    except Exception as e: return {"pm_err": str(e)[:120]}
    d = as_dict(r); d = as_dict(d.get("marketData", d))
    return {"pm_bid": fnum(d.get("bestBid")), "pm_ask": fnum(d.get("bestAsk")),
            "pm_cur": fnum(d.get("currentPx")), "pm_oi": fnum(d.get("openInterest"))}

def mid(b, a): return (b + a) / 2 if (b is not None and a is not None) else None

def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    log(f"MLB x-venue (PM vs Kalshi) -> {OUTFILE}  ({minutes}m, {interval}s)  READ-ONLY")
    if not (key_id and secret):
        log("!! no Polymarket US creds -- run on the droplet."); return
    end = time.time() + minutes * 60
    n = 0
    while time.time() < end:
        loop = time.time()
        games = schedule(et_date())
        pollable = [g for g in games if g["live"] or g.get("pregame")]
        kidx = kalshi_index()
        for g in pollable:
            rec = {"poll_ts": now_utc().isoformat(), "slug": g["slug"], "gamePk": g["gamePk"],
                   "away": g["away"], "home": g["home"], "inning": g["inning"], "half": g["half"],
                   "game_state": "live" if g["live"] else "pregame"}
            rec.update(pm_quote(g["slug"]))
            gdate = "-".join(g["slug"].split("-")[-3:])  # date from the slug
            kq = kalshi_quote(kidx, g["away"], g["home"], gdate)
            if kq: rec.update(kq)
            pm_m = mid(rec.get("pm_bid"), rec.get("pm_ask"))
            k_m = mid(rec.get("k_bid"), rec.get("k_ask"))
            rec["gap_mid"] = (pm_m - k_m) if (pm_m is not None and k_m is not None) else None
            # executable arbs (both legs on P(away)); >0 = locked edge before fees
            if rec.get("k_bid") is not None and rec.get("pm_ask") is not None:
                rec["arb_buyPM_sellK"] = rec["k_bid"] - rec["pm_ask"]
            if rec.get("pm_bid") is not None and rec.get("k_ask") is not None:
                rec["arb_buyK_sellPM"] = rec["pm_bid"] - rec["k_ask"]
            emit(rec); n += 1
            best = max([x for x in (rec.get("arb_buyPM_sellK"), rec.get("arb_buyK_sellPM")) if x is not None], default=None)
            flag = f"  <== ARB {best*100:+.1f}c" if (best is not None and best > 0) else ""
            tag = "LIVE" if g["live"] else "PRE "
            log(f"{tag} {g['slug'][:24]:24} "
                f"PM[{rec.get('pm_bid')}/{rec.get('pm_ask')}] K[{rec.get('k_bid')}/{rec.get('k_ask')}] "
                f"gap={None if rec['gap_mid'] is None else round(rec['gap_mid'],3)}{flag}")
        if not pollable:
            log(f"no live/pregame games ({len(games)} on slate). waiting...")
        dt = interval - (time.time() - loop)
        if dt > 0 and time.time() + dt < end: time.sleep(dt)
        elif dt > 0: break
    log(f"done. {n} x-venue ticks -> {OUTFILE}.")

if __name__ == "__main__":
    main()
