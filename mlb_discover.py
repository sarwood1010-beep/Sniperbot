#!/usr/bin/env python3
"""mlb_discover.py -- READ-ONLY liquidity screen (Screen 1) for MLB on Polymarket
US. Run on the DROPLET during a LIVE MLB game (evening ET). Places NO orders.

What it does (all read-only):
  1. REST: find today's live aec-mlb-* markets.
  2. WS: subscribe (subscription_type 2 = MARKET_DATA_LITE, top-of-book) and
     collect for ~90s -> per-market best_bid/ask, spread, bid/ask depth counts,
     open interest, volume, tick count.
  3. Probe other subscription_type values (1,3,4,5) on one market to discover
     whether a FULL depth ladder is available (MARKET_DATA_LITE = top-of-book
     only). Captures the raw shapes.
  4. Prints a summary + a PASS/NO-GO hint, and writes mlb_discover_out.txt.

Screen-1 bar (see MLB_SCREEN.md): PASS if, on the side we'd trade, spread <= ~3c
AND real resting size near the touch on a majority of sampled moments. NO-GO if
books look like tennis (token quotes ~10 shares, spread routinely > 5c).

Run:  venv/bin/python mlb_discover.py
"""
import os, sys, json, time, base64, asyncio
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    from polymarket_us import PolymarketUS
    import websockets
    from cryptography.hazmat.primitives.asymmetric import ed25519
except Exception as e:
    print("import error (run on the droplet venv):", e); sys.exit(1)

load_dotenv(override=True)
OUT = open("mlb_discover_out.txt", "w")
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

# ET is UTC-4 in July (EDT); the slug date == the game's ET date.
_now_et = datetime.now(timezone.utc) - timedelta(hours=4)
TODAY_ET = _now_et.strftime("%Y-%m-%d")
YEST_ET = (_now_et - timedelta(days=1)).strftime("%Y-%m-%d")

def slug_liveness(slug):
    """Best-effort live/future flag from the date embedded in the slug
    (aec-mlb-{away}-{home}-{date}). Team-token shape is not assumed -- we just
    look for today's/yesterday's ET date string. A TODAY-dated market is a
    candidate LIVE game; whether it actually TICKS on the WS is the real
    in-play signal (a parked pre-game book won't tick)."""
    if TODAY_ET in slug: return "TODAY"
    if YEST_ET in slug: return "YEST"
    return "OTHER"

def find_mlb_markets():
    """List open aec-mlb-* markets via REST search, flagged live(TODAY)/future."""
    out = {}
    try:
        r = pm.search.query({"query": "mlb", "limit": 100})
        events = []
        if isinstance(r, dict):
            for v in r.values():
                if isinstance(v, list): events = v; break
        elif isinstance(r, list):
            events = r
    except Exception as e:
        log("REST search error:", e); return out
    for ev in events:
        if not isinstance(ev, dict): continue
        title = ev.get("title", ev.get("question", "?"))
        for mkt in (ev.get("markets") or []):
            if not isinstance(mkt, dict): continue
            slug = mkt.get("slug", "")
            if not slug.startswith("aec-mlb-") or mkt.get("closed"):
                continue
            sides = [s.get("description", "?") for s in (mkt.get("marketSides") or [])]
            # capture whatever liveness-ish fields exist (field names unknown --
            # keep only those present, so tonight's output tells us what's live).
            state = {k: mkt[k] for k in ("status", "state", "active", "live",
                     "gameStartTime", "startTime", "startDate") if k in mkt}
            out[slug] = {"title": title, "sides": sides,
                         "oi": mkt.get("openInterest"), "vol": mkt.get("volume"),
                         "live": slug_liveness(slug), "state": state}
    return out

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
        "shares": fv(mdl.get("sharesTraded") or mdl.get("shares_traded")),
        "cur": fv(mdl.get("currentPx") or mdl.get("current_px")),
    }
    return slug, rec

async def collect_mdl(slugs, seconds=90):
    """Subscribe type 2 to all slugs, collect for `seconds`. Returns per-slug stats."""
    stats = {s: {"ticks": 0, "spread_sum": 0.0, "spread_n": 0,
                 "min_spread": None, "ad": [], "bd": [], "last": None} for s in slugs}
    try:
        async with websockets.connect(WS_MARKETS, additional_headers=ws_auth_headers(),
                                       ping_interval=20, ping_timeout=15, close_timeout=5) as ws:
            sub = {"subscribe": {"request_id": f"disc-{int(time.time())}",
                                 "subscription_type": 2, "market_slugs": list(slugs)}}
            await ws.send(json.dumps(sub))
            log(f"[ws] subscribed {len(slugs)} MLB markets (type 2), collecting {seconds}s...")
            end = time.time() + seconds
            # wait_for, not `async for`: a silent server (0 frames) would block the
            # async iterator forever and never re-check the deadline.
            while time.time() < end:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(5.0, max(0.2, end - time.time())))
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break
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
        log("[ws] collect error:", repr(e)[:200])
    return stats

async def probe_types(slug):
    """Send subscribe with types 1,3,4,5 on one slug; capture the raw shapes to
    discover whether a full depth ladder exists."""
    log("\n=== subscription-type probe (looking for a depth ladder) ===")
    for t in (1, 3, 4, 5):
        try:
            async with websockets.connect(WS_MARKETS, additional_headers=ws_auth_headers(),
                                           ping_interval=20, close_timeout=5) as ws:
                await ws.send(json.dumps({"subscribe": {"request_id": f"probe-{t}",
                              "subscription_type": t, "market_slugs": [slug]}}))
                got = []
                end = time.time() + 6
                while time.time() < end and len(got) < 3:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(0.2, end - time.time()))
                    except (asyncio.TimeoutError, Exception):
                        break
                    if isinstance(raw, dict): raw = json.dumps(raw)
                    if '"heartbeat"' in raw: continue
                    got.append(raw[:600])
                if got:
                    try: keys = list(json.loads(got[0]).keys())
                    except Exception: keys = "?"
                    log(f"  type {t}: top-level keys={keys}")
                    log(f"    sample: {got[0][:400]}")
                else:
                    log(f"  type {t}: no data / rejected")
        except Exception as e:
            log(f"  type {t}: error {repr(e)[:100]}")

async def main():
    log(f"=== MLB Screen-1 liquidity probe @ {datetime.now(timezone.utc).isoformat()} ===")
    if not (key_id and secret):
        log("!! no Polymarket US creds in env -- run on the droplet."); return
    markets = find_mlb_markets()
    log(f"open aec-mlb-* markets found: {len(markets)}   (ET today={TODAY_ET})")
    if not markets:
        log("No open MLB markets right now. Run during a live game (evening ET)."); return
    # Sort TODAY-dated (candidate live) first so they are never truncated out of
    # the WS subscription / type-probe below.
    order = {"TODAY": 0, "YEST": 1, "OTHER": 2}
    ordered = sorted(markets.items(), key=lambda kv: order.get(kv[1]["live"], 3))
    n_today = sum(1 for _, m in ordered if m["live"] == "TODAY")
    log(f"markets dated TODAY (candidate live games): {n_today}")
    if n_today == 0:
        log("!! WARNING: 0 markets dated today. The in-progress game is NOT")
        log("!! surfacing from the search -- find_mlb_markets() needs a tweak")
        log("!! (broader query / different endpoint) before the liquidity read is valid.")
    for s, m in ordered[:30]:
        log(f"  [{m['live']:5}] {s[:44]:44} OI={m['oi']} vol={m['vol']} state={m['state']} sides={m['sides']}")
    slugs = [s for s, _ in ordered][:20]
    stats = await collect_mdl(slugs, seconds=90)
    log("\n=== per-market top-of-book liquidity (from MARKET_DATA_LITE) ===")
    log(f"{'slug':44} {'ticks':>5} {'avg_spread':>10} {'min_sp':>7} {'askDepth':>8} {'bidDepth':>8}")
    liquid = 0
    for s in slugs:
        st = stats[s]
        if not st["ticks"]:
            log(f"{s[:44]:44} {'0':>5}  (no ticks)"); continue
        avg = (st["spread_sum"] / st["spread_n"]) if st["spread_n"] else None
        ad = sorted(st["ad"])[len(st["ad"]) // 2] if st["ad"] else "-"
        bd = sorted(st["bd"])[len(st["bd"]) // 2] if st["bd"] else "-"
        avgs = f"{avg*100:.1f}c" if avg is not None else "-"
        mins = f"{st['min_spread']*100:.1f}c" if st["min_spread"] is not None else "-"
        log(f"{s[:44]:44} {st['ticks']:>5} {avgs:>10} {mins:>7} {str(ad):>8} {str(bd):>8}")
        try:
            if avg is not None and avg <= 0.03 and ad != "-" and float(ad) >= 50:
                liquid += 1
        except Exception:
            pass
    log(f"\nmarkets meeting rough bar (avg spread<=3c AND median askDepth>=50): {liquid}/{len(slugs)}")
    log("NOTE: askDepth/bidDepth are COUNTS at the touch, not $. A depth ladder")
    log("(from the type-probe below) is needed to size $ within 2c. First read:")
    log("tennis-thin (tens of shares / >5c) = NO-GO; deep+tight = build the collector.")
    if slugs:
        await probe_types(slugs[0])
    log("\nDone. Download mlb_discover_out.txt for review.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        OUT.close()
