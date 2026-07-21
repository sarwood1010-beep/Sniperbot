#!/usr/bin/env python3
"""mlb_arb_paper.py -- FAST cross-venue (Polymarket US vs Kalshi) PAPER arb model.

*** PAPER / READ-ONLY. Places NO real orders on either venue. Every "fill" is a
    SIMULATION. Safe to deploy. Never enables live trading. ***

Purpose: pressure-test edge idea #1 (a real in-play PM-vs-Kalshi arb was found, but
gaps close in ~20-60s -- FINDINGS "CROSS-VENUE"). This polls BOTH venues FAST
(~3s) and models realistic execution:
  detect arb -> wait LATENCY seconds (you can't fill instantly) -> re-check: is the
  arb STILL profitable? If yes -> PAPER-FILL at the delayed price (book the shrunk
  net); if no -> MISSED (gap closed first). This answers the only question that
  matters: after realistic latency + leg risk, how much is actually capturable?

Outputs: JSONL log (mlb_arb_paper.jsonl), a running paper P&L, and optional Discord
webhook alerts (set DISCORD_WEBHOOK_URL) -- deployable as a systemd service exactly
like sniper-bot (see mlb-arb-paper.service + DEPLOY_RUNBOOK).

Arb (both legs on P(away), one venue each; locked regardless of winner):
  a1 = k_bid - pm_ask   (buy PM away@ask, sell Kalshi away@bid=buy NO)
  a2 = pm_bid - k_ask   (buy Kalshi away@ask, sell PM away@bid)
  net = max(a1,a2) - kalshi_fee(k_mid) - PM_FEE

Run on the DROPLET during live games:
  venv/bin/python mlb_arb_paper.py [run_min] [interval_s] [latency_s] [entry_cents]
  e.g. venv/bin/python mlb_arb_paper.py 300 3 4 2
"""
import os, sys, json, time, math, urllib.request, urllib.error, re
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    from polymarket_us import PolymarketUS
except Exception as e:
    print("import error (run on the droplet venv):", e); sys.exit(1)

load_dotenv(override=True)
OUTFILE = "mlb_arb_paper.jsonl"
KB = "https://api.elections.kalshi.com/trade-api/v2"
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
PM_FEE = 0.0   # unknown; 0 = best case (loudly flagged in the summary)

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
def mid(b, a): return (b + a) / 2 if (b is not None and a is not None) else None
def kalshi_fee(p):
    if p is None: p = 0.5
    return math.ceil(0.07 * p * (1 - p) * 100) / 100.0

def http(url, tmo=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try: return json.load(urllib.request.urlopen(req, timeout=tmo))
    except Exception as e: return {"_err": str(e)[:120]}

def discord(msg):
    if not WEBHOOK: return
    try:
        data = json.dumps({"content": msg[:1900]}).encode()
        req = urllib.request.Request(WEBHOOK, data=data, method="POST",
              headers={"Content-Type": "application/json", "User-Agent": "mlb-arb-paper"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log("discord err:", str(e)[:100])

def et_date(): return (now_utc() - timedelta(hours=4)).strftime("%Y-%m-%d")
def live_games():
    d = http(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={et_date()}&hydrate=linescore,team")
    out = []
    for g in (d.get("dates", [{}])[0].get("games", []) if d.get("dates") else []):
        st = g.get("status", {}).get("detailedState", "?")
        if st not in ("In Progress", "Manager challenge"): continue
        aw = g["teams"]["away"]["team"].get("abbreviation", "").lower()
        hm = g["teams"]["home"]["team"].get("abbreviation", "").lower()
        ls = g.get("linescore", {})
        out.append({"away": aw, "home": hm, "slug": f"aec-mlb-{aw}-{hm}-{et_date()}",
                    "state": f"{ls.get('inningState','')}{ls.get('currentInning','')}"})
    return out

_MONS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
def to_date7(d): y, m, dd = d.split("-"); return f"{y[2:]}{_MONS[int(m)-1]}{dd}"
def kalshi_index():
    idx = {}; cur = ""
    for _ in range(6):
        u = f"{KB}/markets?series_ticker=KXMLBGAME&status=open&limit=200" + (f"&cursor={cur}" if cur else "")
        d = http(u)
        for m in d.get("markets", []):
            p = m.get("ticker", "").split("-")
            if len(p) < 3: continue
            mm = re.match(r"^(\d{2}[A-Z]{3}\d{2})\d{4}(.+)$", p[1])
            if mm: idx[(mm.group(1), mm.group(2), p[2])] = m
        cur = d.get("cursor") or ""
        if not cur: break
    return idx
def kalshi_quote(idx, away, home, date):
    m = idx.get((to_date7(date), away.upper() + home.upper(), away.upper()))
    if not m: return None, None
    return fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
def pm_quote(slug):
    try: r = pm.markets.bbo(slug)
    except Exception: return None, None
    d = as_dict(r); d = as_dict(d.get("marketData", d))
    return fnum(d.get("bestBid")), fnum(d.get("bestAsk"))

def arb(pm_b, pm_a, k_b, k_a):
    """Return (best_net, direction, gross) for P(away). None if unquotable."""
    if None in (pm_b, pm_a, k_b, k_a): return None
    a1 = k_b - pm_a   # buy PM, sell Kalshi
    a2 = pm_b - k_a   # buy Kalshi, sell PM
    gross, direction = (a1, "buyPM_sellK") if a1 >= a2 else (a2, "buyK_sellPM")
    net = gross - kalshi_fee(mid(k_b, k_a)) - PM_FEE
    return net, direction, gross

def main():
    run_min = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    latency = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    entry = (float(sys.argv[4]) if len(sys.argv) > 4 else 2.0) / 100.0
    log(f"PAPER x-venue arb model | run {run_min}m poll {interval}s latency {latency}s "
        f"entry {entry*100:.1f}c | webhook={'ON' if WEBHOOK else 'off'} | READ-ONLY, NO ORDERS")
    if not (key_id and secret):
        log("!! no PM creds -- run on the droplet."); return
    discord(f":satellite: **MLB x-venue PAPER arb model started** (poll {interval}s, "
            f"latency {latency}s, entry {entry*100:.0f}c). Paper only, no real orders.")
    end = float("inf") if run_min <= 0 else time.time() + run_min * 60  # <=0 => run forever (service)
    pending = {}   # slug -> {t, direction, detect_net, pm/k quotes}
    n_detect = n_fill = n_miss = 0
    pnl = 0.0
    last_summary = time.time()
    while time.time() < end:
        loop = time.time()
        games = live_games()
        kidx = kalshi_index() if games else {}
        for g in games:
            date = "-".join(g["slug"].split("-")[-3:])
            pm_b, pm_a = pm_quote(g["slug"])
            k_b, k_a = kalshi_quote(kidx, g["away"], g["home"], date)
            r = arb(pm_b, pm_a, k_b, k_a)
            if r is None: continue
            net, direction, gross = r
            slug = g["slug"]
            if slug in pending:
                p = pending[slug]
                if time.time() - p["t"] >= latency:
                    # try to fill NOW at current quotes, same direction
                    if direction == p["direction"] and net > 0:
                        pnl += net; n_fill += 1
                        rec = {"type": "fill", "ts": now_utc().isoformat(), "slug": slug,
                               "state": g["state"], "dir": direction,
                               "detect_net": round(p["detect_net"], 4), "fill_net": round(net, 4),
                               "held_s": round(time.time() - p["t"], 1), "pnl": round(pnl, 4)}
                        emit(rec)
                        log(f"FILL  {slug[:24]} {g['state']} {direction} detect={p['detect_net']*100:+.1f}c "
                            f"fill={net*100:+.1f}c pnl={pnl*100:+.1f}c")
                        discord(f":white_check_mark: **PAPER FILL** `{slug}` {g['state']} "
                                f"{direction} | detect {p['detect_net']*100:+.1f}c -> fill "
                                f"{net*100:+.1f}c | run P&L {pnl*100:+.1f}c/contract")
                    else:
                        n_miss += 1
                        emit({"type": "miss", "ts": now_utc().isoformat(), "slug": slug,
                              "detect_net": round(p["detect_net"], 4), "net_at_fill": round(net, 4)})
                        log(f"MISS  {slug[:24]} detect={p['detect_net']*100:+.1f}c gap closed (now {net*100:+.1f}c)")
                    del pending[slug]
            elif net >= entry:
                n_detect += 1
                pending[slug] = {"t": time.time(), "direction": direction, "detect_net": net}
                emit({"type": "detect", "ts": now_utc().isoformat(), "slug": slug, "state": g["state"],
                      "dir": direction, "net": round(net, 4), "pm": [pm_b, pm_a], "k": [k_b, k_a]})
                log(f"ARB?  {slug[:24]} {g['state']} {direction} net={net*100:+.1f}c "
                    f"PM[{pm_b}/{pm_a}] K[{k_b}/{k_a}] -> waiting {latency}s")
        if not games:
            log("no live games. waiting...")
        if time.time() - last_summary >= 600:
            cap = (100*n_fill/n_detect) if n_detect else 0
            discord(f":bar_chart: **60-min-ish summary** detected={n_detect} filled={n_fill} "
                    f"({cap:.0f}% capture) missed={n_miss} | paper P&L {pnl*100:+.1f}c/contract "
                    f"(PM fee assumed {PM_FEE})")
            last_summary = time.time()
        dt = interval - (time.time() - loop)
        if dt > 0 and time.time() + dt < end: time.sleep(dt)
        elif dt > 0: break
    cap = (100*n_fill/n_detect) if n_detect else 0
    msg = (f"detected={n_detect} filled={n_fill} ({cap:.0f}% capture) missed={n_miss} | "
           f"paper P&L {pnl*100:+.1f}c/contract | PM fee assumed {PM_FEE} (real fee lowers this)")
    log("DONE.", msg)
    discord(f":checkered_flag: **PAPER arb model stopped.** {msg}")

if __name__ == "__main__":
    main()
