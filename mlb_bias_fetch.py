#!/usr/bin/env python3
"""mlb_bias_fetch.py -- READ-ONLY. Builds the historical dataset for edge idea #3
(behavioral bias): for every SETTLED Kalshi MLB game market, the genuine PRE-GAME
price + the actual outcome.

Runs LOCALLY (Kalshi's API is public, no auth, not geo-blocked) -- no droplet.

Why not the obvious fields: `previous_price_dollars` / `last_price_dollars` on a
settled market are SETTLEMENT-contaminated (97.7% are <=2c or >=98c) -- using them
would "prove" a fake edge (a 0.99 price predicts a win, tautologically). So we take
the price from the CANDLESTICK history: the last candle that CLOSES BEFORE first
pitch. First pitch is encoded in the ticker: KXMLBGAME-{YYMONDD}{HHMM}{MATCHUP}-{SIDE}
where HHMM is ET.

Output: mlb_bias_data.jsonl, one row per game (AWAY side):
  {game, date, away, home, pregame_px, pregame_bid/ask, result_away_win,
   vol, oi, candles_before}
Resumable: existing rows are skipped on re-run.

Usage:  "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" mlb_bias_fetch.py [max_games]
"""
import sys, os, json, time, re, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

KB = "https://api.elections.kalshi.com/trade-api/v2"
OUT = "mlb_bias_data.jsonl"
_MONS = {m: i+1 for i, m in enumerate(
    ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"])}

def http(u, tmo=30, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0",
                                                     "Accept": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=tmo))
        except urllib.error.HTTPError as e:
            if e.code == 429:          # rate limited -> back off
                time.sleep(2 * (k + 1)); continue
            return {"_err": f"HTTP {e.code}"}
        except Exception as e:
            time.sleep(1);
            if k == tries - 1: return {"_err": str(e)[:100]}
    return {"_err": "retries exhausted"}

def fnum(x):
    try: return float(x)
    except Exception: return None

def parse_ticker(tk):
    """KXMLBGAME-26AUG012140DETATH-DET -> (dt_utc_first_pitch, matchup, side, key)."""
    p = tk.split("-")
    if len(p) < 3: return None
    m = re.match(r"^(\d{2})([A-Z]{3})(\d{2})(\d{2})(\d{2})(.+)$", p[1])
    if not m: return None
    yy, mon, dd, hh, mi, matchup = m.groups()
    try:
        # ticker time is ET (UTC-4 in season); convert to UTC
        et = datetime(2000+int(yy), _MONS[mon], int(dd), int(hh), int(mi))
        return et.replace(tzinfo=timezone.utc) + timedelta(hours=4), matchup, p[2], p[1]
    except Exception:
        return None

def settled_markets():
    out = []; cur = ""
    while True:
        d = http(f"{KB}/markets?series_ticker=KXMLBGAME&status=settled&limit=1000"
                 + (f"&cursor={cur}" if cur else ""))
        ms = d.get("markets", [])
        if not ms: break
        out += ms
        cur = d.get("cursor") or ""
        if not cur: break
    return out

def pregame_price(tk, first_pitch):
    """Last candle CLOSING BEFORE first pitch -> (close, bid, ask, vol, oi, n_before)."""
    start = int((first_pitch - timedelta(hours=30)).timestamp())
    end = int(first_pitch.timestamp())
    d = http(f"{KB}/series/KXMLBGAME/markets/{tk}/candlesticks"
             f"?start_ts={start}&end_ts={end}&period_interval=60")
    cs = d.get("candlesticks", [])
    best = None
    for c in cs:
        ts = c.get("end_period_ts")
        if ts is None or ts > end:      # strictly before first pitch
            continue
        px = fnum((c.get("price") or {}).get("close_dollars"))
        if px is None or px <= 0:       # no trade in that candle
            continue
        best = (px, fnum(c.get("yes_bid")), fnum(c.get("yes_ask")),
                fnum(c.get("volume_fp")), fnum(c.get("open_interest_fp")))
    n_before = sum(1 for c in cs if (c.get("end_period_ts") or 0) <= end)
    return (best or (None,)*5) + (n_before,)

def main():
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 10**9
    done = set()
    if os.path.exists(OUT):
        for l in open(OUT):
            try: done.add(json.loads(l)["game"])
            except Exception: pass
    print(f"already have {len(done)} games in {OUT}")
    ms = settled_markets()
    print(f"settled markets: {len(ms)}")
    # group by game key; keep the AWAY side (matchup = AWAY+HOME, side == prefix)
    games = {}
    for m in ms:
        pt = parse_ticker(m.get("ticker", ""))
        if not pt: continue
        fp, matchup, side, key = pt
        if not matchup.startswith(side):     # away side only
            continue
        games[key] = (m, fp, matchup, side)
    print(f"games (away side identified): {len(games)}")
    todo = [(k, v) for k, v in sorted(games.items()) if k not in done][:cap]
    print(f"fetching {len(todo)} ...")
    t0 = time.time(); n_ok = n_skip = 0
    with open(OUT, "a") as f:
        for i, (key, (m, fp, matchup, side)) in enumerate(todo, 1):
            res = m.get("result")
            if res not in ("yes", "no"):
                n_skip += 1; continue
            px, bid, ask, vol, oi, nb = pregame_price(m["ticker"], fp)
            if px is None:
                n_skip += 1
            else:
                away = side; home = matchup[len(side):]
                f.write(json.dumps({
                    "game": key, "ticker": m["ticker"],
                    "date": fp.astimezone(timezone.utc).strftime("%Y-%m-%d"),
                    "first_pitch_utc": fp.isoformat(),
                    "away": away, "home": home,
                    "pregame_px": px, "pregame_bid": bid, "pregame_ask": ask,
                    "result_away_win": 1 if res == "yes" else 0,
                    "vol": vol, "oi": oi, "candles_before": nb,
                }) + "\n"); f.flush(); n_ok += 1
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}  ok={n_ok} skip={n_skip}  {time.time()-t0:.0f}s", flush=True)
            time.sleep(0.12)    # be polite to the public API
    print(f"done. wrote {n_ok}, skipped {n_skip}, {time.time()-t0:.0f}s -> {OUT}")

if __name__ == "__main__":
    main()
