#!/usr/bin/env python3
"""mlb_live_probe.py -- READ-ONLY. Answers the ONE question that gates the whole
MLB screen: does an IN-PLAY MLB market exist and trade on Polymarket US?

Why this exists: mlb_discover.py's search ("query":"mlb") returned ONLY future
pre-game markets (07-20/07-21) and dropped EVERY same-day market -- including
games that were live at run time (SF@SEA, PIT@CLE on 07-18). So the search feed
excludes same-day/in-play markets; it is the wrong discovery path.

Lucky fact: each team's MLB StatsAPI `abbreviation`, lowercased, equals its
Polymarket slug token (ath, az, cws, wsh, sd, laa all match). So we can build the
live slug directly -- aec-mlb-{away}-{home}-{ET-date} -- and reach the market on
the WS by slug, bypassing the broken search entirely.

What it does (all read-only, NO orders):
  1. StatsAPI: today's ET slate + live/final/pre-game status; build candidate
     slugs for every game.
  2. Introspect the SDK surface (dir(pm), sub-namespaces) and try alternative
     discovery paths (search by TEAM NAME, not "mlb") to see if the same-day
     market surfaces any other way.
  3. WS (type 2 = MARKET_DATA_LITE): subscribe to ALL of today's constructed
     slugs for ~75s; report per-slug ticks / best_bid / best_ask / spread, tagged
     with each game's StatsAPI status.
  4. Verdict hint: did any IN-PROGRESS same-day slug return a live ticking book?
     Yes -> in-play MLB market exists; discovery is the only fix (build slugs
     from StatsAPI). No book on any live slug -> deeper problem (DH suffix /
     different slug form / or no in-play market at all).

Run on the DROPLET during a live game:  venv/bin/python mlb_live_probe.py
"""
import os, sys, json, time, base64, asyncio, urllib.request
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    from polymarket_us import PolymarketUS
    import websockets
    from cryptography.hazmat.primitives.asymmetric import ed25519
except Exception as e:
    print("import error (run on the droplet venv):", e); sys.exit(1)

load_dotenv(override=True)
OUT = open("mlb_live_probe_out.txt", "w")
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s); OUT.write(s + "\n"); OUT.flush()

def find_env(names):
    for n in names:
        v = os.environ.get(n)
        if v: return v
    return None
key_id = find_env(["POLYMARKET_KEY_ID", "POLYMARKET_US_KEY_ID"])
secret = find_env(["POLYMARKET_SECRET_KEY", "POLYMARKET_US_SECRET"])
pm = PolymarketUS(key_id=key_id, secret_key=secret) if key_id and secret else PolymarketUS()
WS_MARKETS = "wss://api.polymarket.us/v1/ws/markets"

# ET is UTC-4 in July (EDT); the slug date == the game's ET date.
_now_et = datetime.now(timezone.utc) - timedelta(hours=4)
TODAY_ET = _now_et.strftime("%Y-%m-%d")

def ws_auth_headers():
    if not key_id or not secret: return []
    try:
        raw = base64.b64decode(secret)
        priv = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
        ts = str(int(time.time() * 1000))
        msg = f"{ts}GET/v1/ws/markets".encode()
        sig = base64.b64encode(priv.sign(msg)).decode()
        return [("X-PM-Access-Key", key_id), ("X-PM-Timestamp", ts), ("X-PM-Signature", sig)]
    except Exception as e:
        log("ws auth header err:", e); return []

def fv(o):
    if isinstance(o, dict):
        try: return float(o.get("value", 0) or 0)
        except Exception: return None
    try: return float(o)
    except Exception: return None

def parse_mdl(raw):
    try: d = json.loads(raw)
    except Exception: return None, None
    if "error" in d: return "ERROR", d.get("error")
    mdl = d.get("marketDataLite") or d.get("market_data_lite")
    if not mdl: return None, d
    slug = mdl.get("marketSlug") or mdl.get("market_slug")
    rec = {
        "best_bid": fv(mdl.get("bestBid") or mdl.get("best_bid")),
        "best_ask": fv(mdl.get("bestAsk") or mdl.get("best_ask")),
        "ask_depth": mdl.get("askDepth"), "bid_depth": mdl.get("bidDepth"),
        "oi": fv(mdl.get("openInterest") or mdl.get("open_interest")),
        "cur": fv(mdl.get("currentPx") or mdl.get("current_px")),
    }
    return slug, rec

# ---- Step 1: StatsAPI slate + candidate slugs -------------------------------
def statsapi_today():
    """Return list of {away,home,status,live,slug} for today's ET slate."""
    url = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="
           f"{TODAY_ET}&hydrate=linescore,team")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception as e:
        log("StatsAPI error:", e); return []
    dates = d.get("dates", [])
    if not dates: return []
    games = []
    for g in dates[0].get("games", []):
        st = g.get("status", {}).get("detailedState", "?")
        aw = g["teams"]["away"]["team"].get("abbreviation", "").lower()
        hm = g["teams"]["home"]["team"].get("abbreviation", "").lower()
        ls = g.get("linescore", {})
        live = st in ("In Progress", "Manager challenge", "Warmup")
        games.append({
            "away": aw, "home": hm, "status": st, "live": live,
            "inn": f"{ls.get('inningState','')} {ls.get('currentInning','')}".strip(),
            "slug": f"aec-mlb-{aw}-{hm}-{TODAY_ET}",
            "gamePk": g.get("gamePk"),
        })
    return games

# ---- Step 2: SDK introspection + alt discovery ------------------------------
def introspect_sdk(live_games):
    log("\n=== SDK surface (dir(pm)) ===")
    try:
        attrs = [a for a in dir(pm) if not a.startswith("_")]
        log("  pm:", attrs)
        for a in attrs:
            try:
                obj = getattr(pm, a)
            except Exception:
                continue
            if not callable(obj) and hasattr(obj, "__dict__") or "namespace" in type(obj).__name__.lower():
                sub = [x for x in dir(obj) if not x.startswith("_")]
                if sub: log(f"    pm.{a}:", sub)
    except Exception as e:
        log("  introspect error:", e)
    # Alternative discovery: search by TEAM NAME instead of "mlb".
    log("\n=== alt discovery: does a same-day slug surface any other way? ===")
    queries = ["mlb baseball", "baseball"]
    for g in live_games[:2]:
        queries.append(g["home"])   # e.g. "sea"
    seen = set()
    for q in queries:
        try:
            r = pm.search.query({"query": q, "limit": 50})
            events = []
            if isinstance(r, dict):
                for v in r.values():
                    if isinstance(v, list): events = v; break
            elif isinstance(r, list):
                events = r
            today_hits = []
            for ev in events:
                if not isinstance(ev, dict): continue
                for mkt in (ev.get("markets") or []):
                    if not isinstance(mkt, dict): continue
                    slug = mkt.get("slug", "")
                    if slug.startswith("aec-mlb-") and TODAY_ET in slug:
                        today_hits.append(slug); seen.add(slug)
            log(f'  query {q!r}: {len(today_hits)} same-day aec-mlb slug(s) -> {today_hits[:6]}')
        except Exception as e:
            log(f"  query {q!r}: error {e}")
    if seen:
        log("  => a search path DOES surface same-day markets:", sorted(seen)[:8])
    else:
        log("  => NO search variant surfaced a same-day market; rely on built slugs.")

# ---- Step 3: WS probe of constructed same-day slugs -------------------------
async def probe_slugs(games, seconds=75):
    slugs = [g["slug"] for g in games]
    stats = {s: {"ticks": 0, "spread_sum": 0.0, "spread_n": 0,
                 "min_spread": None, "ad": [], "bd": [], "last": None,
                 "err": None} for s in slugs}
    try:
        async with websockets.connect(WS_MARKETS, additional_headers=ws_auth_headers(),
                                       ping_interval=20, ping_timeout=15, close_timeout=5) as ws:
            sub = {"subscribe": {"request_id": f"live-{int(time.time())}",
                                 "subscription_type": 2, "market_slugs": slugs}}
            await ws.send(json.dumps(sub))
            log(f"\n[ws] subscribed {len(slugs)} same-day slugs (type 2), collecting {seconds}s...")
            end = time.time() + seconds
            async for raw in ws:
                if time.time() >= end: break
                if isinstance(raw, dict): raw = json.dumps(raw)
                if '"heartbeat"' in raw:
                    try: await ws.send(json.dumps({"heartbeat": {}}))
                    except Exception: pass
                    continue
                slug, rec = parse_mdl(raw)
                if slug == "ERROR":
                    log("[ws] server error:", rec); continue
                if not slug or slug not in stats or not isinstance(rec, dict):
                    continue
                st = stats[slug]; st["ticks"] += 1; st["last"] = rec
                bb, ba = rec["best_bid"], rec["best_ask"]
                if bb is not None and ba is not None:
                    sp = ba - bb
                    st["spread_sum"] += sp; st["spread_n"] += 1
                    st["min_spread"] = sp if st["min_spread"] is None else min(st["min_spread"], sp)
                if rec["ask_depth"] is not None: st["ad"].append(rec["ask_depth"])
                if rec["bid_depth"] is not None: st["bd"].append(rec["bid_depth"])
    except Exception as e:
        log("[ws] probe error:", repr(e)[:200])
    return stats

async def main():
    log(f"=== MLB LIVE probe @ {datetime.now(timezone.utc).isoformat()} (ET date {TODAY_ET}) ===")
    if not (key_id and secret):
        log("!! no Polymarket US creds in env -- run on the droplet."); return
    games = statsapi_today()
    if not games:
        log("StatsAPI returned no games for today. Abort."); return
    live = [g for g in games if g["live"]]
    log(f"today's slate: {len(games)} games, {len(live)} IN PROGRESS")
    for g in games:
        tag = "LIVE" if g["live"] else g["status"][:12]
        log(f"  [{tag:12}] {g['slug']:34} {g['inn']}")
    if not live:
        log("\n!! No games IN PROGRESS right now -- re-run mid-game for the in-play read.")
        log("   (Still probing all same-day slugs below to test market existence.)")
    introspect_sdk(live)
    stats = await probe_slugs(games, seconds=75)
    log("\n=== per-slug WS result (constructed same-day slugs) ===")
    log(f"{'slug':34} {'status':12} {'ticks':>5} {'avg_sp':>7} {'min_sp':>7} {'bid':>5} {'ask':>5}")
    live_with_book = 0
    for g in games:
        s = g["slug"]; st = stats[s]
        avg = (st["spread_sum"] / st["spread_n"]) if st["spread_n"] else None
        avgs = f"{avg*100:.1f}c" if avg is not None else "-"
        mins = f"{st['min_spread']*100:.1f}c" if st["min_spread"] is not None else "-"
        last = st["last"] or {}
        bb = last.get("best_bid"); ba = last.get("best_ask")
        bbs = f"{bb:.2f}" if bb is not None else "-"
        bas = f"{ba:.2f}" if ba is not None else "-"
        tag = "LIVE" if g["live"] else g["status"][:12]
        log(f"{s:34} {tag:12} {st['ticks']:>5} {avgs:>7} {mins:>7} {bbs:>5} {bas:>5}")
        if g["live"] and st["ticks"] > 0 and (bb is not None or ba is not None):
            live_with_book += 1
    log(f"\nIN-PROGRESS games returning a live book: {live_with_book}/{len(live)}")
    if live_with_book > 0:
        log("=> VERDICT: an in-play MLB market EXISTS and is reachable by built slug.")
        log("   Discovery was the only blocker -> build slugs from StatsAPI, not search.")
        log("   Next: read the live spread/depth above vs the Screen-1 bar.")
    elif live:
        log("=> VERDICT: live games exist but NO book on their same-day slugs.")
        log("   Investigate: doubleheader slug suffix? different slug form? OR")
        log("   Polymarket US may not offer an in-play MLB market (would be NO-GO).")
    log("\nDone. Download mlb_live_probe_out.txt for review.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        OUT.close()
