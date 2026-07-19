#!/usr/bin/env python3
"""mlb_book_probe.py -- READ-ONLY. The REAL Screen-1 depth read, using the REST
market endpoints discovered in the SDK surface (NOT the WS top-of-book lite feed).

Background: mlb_live_probe.py revealed pm.markets exposes retrieve_by_slug / book /
bbo / settlement / list. `book` should return a full depth ladder (what Screen 1
actually needs -- $ resting within 2c of the touch), and `settlement` should give
clean outcomes (fixing the tennis "no outcomes" hole). Same-day slugs are built
directly from StatsAPI (abbreviation.lower() == Polymarket slug token, all 30).

What it does (all read-only, places NO orders):
  1. Print exact signatures of the pm.markets.* methods (stop guessing the API).
  2. Try pm.markets.list(...) a few ways -> is there a clean live-market listing?
  3. For each IN-PROGRESS game: retrieve_by_slug (status/OI/vol/ids) + book (depth
     ladder, raw dump + best-effort parse) + bbo. Compute per side: spread and
     $ resting within 2c of the touch -> vs the Screen-1 bar.
  4. For one FINAL game: settlement(...) raw dump -> confirm clean outcomes exist.

Run on the DROPLET during a live game:  venv/bin/python mlb_book_probe.py
"""
import os, sys, json, time, base64, inspect, urllib.request
from datetime import datetime, timezone, timedelta

try:
    from dotenv import load_dotenv
    from polymarket_us import PolymarketUS
except Exception as e:
    print("import error (run on the droplet venv):", e); sys.exit(1)

load_dotenv(override=True)
OUT = open("mlb_book_probe_out.txt", "w")
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

_now_et = datetime.now(timezone.utc) - timedelta(hours=4)
TODAY_ET = _now_et.strftime("%Y-%m-%d")

def jd(o, n=1600):
    """Best-effort compact JSON/str dump, truncated."""
    try:
        if hasattr(o, "to_dict"): o = o.to_dict()
        elif hasattr(o, "model_dump"): o = o.model_dump()
        elif hasattr(o, "__dict__") and not isinstance(o, dict): o = vars(o)
        return json.dumps(o, default=str)[:n]
    except Exception:
        return str(o)[:n]

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

def statsapi_today():
    url = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="
           f"{TODAY_ET}&hydrate=linescore,team")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception as e:
        log("StatsAPI error:", e); return []
    dates = d.get("dates", [])
    if not dates: return []
    out = []
    for g in dates[0].get("games", []):
        st = g.get("status", {}).get("detailedState", "?")
        aw = g["teams"]["away"]["team"].get("abbreviation", "").lower()
        hm = g["teams"]["home"]["team"].get("abbreviation", "").lower()
        out.append({"away": aw, "home": hm, "status": st,
                    "live": st in ("In Progress", "Manager challenge", "Warmup"),
                    "final": st == "Final",
                    "slug": f"aec-mlb-{aw}-{hm}-{TODAY_ET}"})
    return out

def sigs():
    log("\n=== pm.markets method signatures ===")
    for name in ("retrieve_by_slug", "retrieve", "book", "bbo", "settlement", "list"):
        fn = getattr(pm.markets, name, None)
        if fn is None:
            log(f"  {name}: <absent>"); continue
        try: log(f"  {name}{inspect.signature(fn)}")
        except Exception as e: log(f"  {name}: sig? {e}")
    for name in ("list", "retrieve_by_slug"):
        fn = getattr(pm.events, name, None)
        if fn is not None:
            try: log(f"  events.{name}{inspect.signature(fn)}")
            except Exception: pass

def try_list_live():
    log("\n=== pm.markets.list / events.list -- clean live discovery? ===")
    attempts = [
        ("markets.list()", lambda: pm.markets.list()),
        ("markets.list(limit=5)", lambda: pm.markets.list(limit=5)),
        ("markets.list(status='open')", lambda: pm.markets.list(status="open")),
        ("markets.list(sport='mlb')", lambda: pm.markets.list(sport="mlb")),
        ("events.list()", lambda: pm.events.list()),
        ("events.list(limit=5)", lambda: pm.events.list(limit=5)),
    ]
    for label, fn in attempts:
        try:
            r = fn()
            rd = r if isinstance(r, (list, dict)) else as_dict(r)
            n = len(rd) if isinstance(rd, list) else (len(rd.get("data", rd.get("markets", rd.get("items", [])))) if isinstance(rd, dict) else "?")
            log(f"  {label}: OK  ~{n} items  sample={jd(r, 500)}")
        except TypeError as e:
            log(f"  {label}: TypeError {e}")
        except Exception as e:
            log(f"  {label}: {type(e).__name__} {str(e)[:120]}")

def call_first_ok(fn, args_list):
    """Try several arg tuples/kwargs; return (label, result) for the first OK."""
    for label, args, kw in args_list:
        try:
            return label, fn(*args, **kw)
        except Exception as e:
            log(f"      [{label}] {type(e).__name__}: {str(e)[:90]}")
    return None, None

def parse_book(bk):
    """Pull (bids, asks) as lists of (price, size). Real shape (confirmed):
    {"marketData": {"bids": [{"px": {"value": "0.495"}, "qty": "181271.1"}, ...],
                    "offers": [...]}}  -- asks live under 'offers', px is nested."""
    d = as_dict(bk) if not isinstance(bk, (list, dict)) else bk
    if isinstance(d, dict) and "marketData" in d:
        d = as_dict(d["marketData"])
    if not isinstance(d, dict):
        return None, None
    def side(*keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, list) and v: return v
        return []
    bids = side("bids", "buys", "bid")
    asks = side("offers", "asks", "sells", "ask")
    def norm(levels):
        out = []
        for lv in levels:
            ld = lv if isinstance(lv, dict) else {}
            p = fnum(ld.get("px", ld.get("price", ld.get("p"))))
            sz = fnum(ld.get("qty", ld.get("size", ld.get("shares", ld.get("quantity")))))
            if p is not None and sz is not None: out.append((p, sz))
        return out
    return norm(bids), norm(asks)

def walk_fill(levels, usd, is_buy):
    """Avg fill price for a market order spending `usd`, walking best-first."""
    lv = sorted(levels, key=lambda x: x[0], reverse=not is_buy)  # buy hits low asks
    spent = shares = 0.0
    for p, s in lv:
        room = p * s
        if spent + room >= usd:
            shares += (usd - spent) / p; return usd / shares
        spent += room; shares += s
    return (spent / shares) if shares else None

def screen1_depth(bids, asks):
    """Print spread + $ within 2c of the touch + $25/$50 fill walk (Screen-1 bar)."""
    if not bids or not asks:
        log("      (could not parse ladder -- see raw dump above)"); return
    bb = max(p for p, _ in bids); ba = min(p for p, _ in asks)
    spread = ba - bb; mid = (bb + ba) / 2
    bid_usd = sum(p * s for p, s in bids if p >= bb - 0.02)
    ask_usd = sum(p * s for p, s in asks if p <= ba + 0.02)
    log(f"      best_bid={bb:.3f} best_ask={ba:.3f} spread={spread*100:.1f}c")
    log(f"      $ within 2c of touch:  bid-side=${bid_usd:,.0f}  ask-side=${ask_usd:,.0f}")
    for usd in (25, 50):
        buy = walk_fill(asks, usd, True); sell = walk_fill(bids, usd, False)
        bs = f"{(buy-mid)*100:+.1f}c" if buy else "-"; ss = f"{(mid-sell)*100:+.1f}c" if sell else "-"
        log(f"      ${usd:>3} order slip vs mid:  buy {bs}   sell {ss}")
    ok = spread <= 0.03 and min(bid_usd, ask_usd) >= 100
    log(f"      Screen-1 (<=3c AND >=~$100/side within 2c): {'PASS' if ok else 'below bar'}")

def probe_market(g, do_book=True):
    slug = g["slug"]
    log(f"\n--- {slug}  [{g['status']}] ---")
    label, m = call_first_ok(pm.markets.retrieve_by_slug,
        [("slug-pos", (slug,), {}), ("slug-kw", (), {"slug": slug})])
    if m is None:
        log("  retrieve_by_slug FAILED for this slug."); return
    md = as_dict(m)
    if isinstance(md.get("market"), dict): md = md["market"]  # unwrap {"market": {...}}
    log(f"  StatsAPI status={g['status']}  id={md.get('id')}  question={md.get('question','')[:70]}")
    if not do_book:
        return
    # bbo (one cheap call: clean bid/ask/currentPx/OI/sharesTraded)
    _, bb = call_first_ok(pm.markets.bbo, [("slug", (slug,), {})])
    if bb is not None:
        bd = as_dict(bb); bd = as_dict(bd.get("marketData", bd))
        log(f"  bbo: bid={jd(bd.get('bestBid'),40)} ask={jd(bd.get('bestAsk'),40)} "
            f"cur={jd(bd.get('currentPx'),40)} OI={bd.get('openInterest')} traded={bd.get('sharesTraded')}")
    # book (signature is book(slug) -> depth ladder)
    _, bk = call_first_ok(pm.markets.book, [("slug", (slug,), {})])
    if bk is None:
        log("  book failed (see errors above)."); return
    log(f"  book raw: {jd(bk, 1200)}")
    bids, asks = parse_book(bk)
    screen1_depth(bids, asks)

def main():
    log(f"=== MLB book/depth probe @ {datetime.now(timezone.utc).isoformat()} (ET {TODAY_ET}) ===")
    if not (key_id and secret):
        log("!! no Polymarket US creds -- run on the droplet."); return
    games = statsapi_today()
    live = [g for g in games if g["live"]]
    finals = [g for g in games if g["final"]]
    log(f"today: {len(games)} games, {len(live)} live, {len(finals)} final")
    sigs()
    try_list_live()
    log("\n=== IN-PROGRESS markets: status + depth ladder (Screen-1 read) ===")
    if not live:
        log("  none live now -- re-run mid-game. Probing one final for shape anyway.")
        if finals: probe_market(finals[0])
    for g in live:
        probe_market(g)
    if finals:
        log("\n=== settlement on a FINAL game (confirm clean outcomes for Screen 2) ===")
        g = finals[0]
        lbl, s = call_first_ok(pm.markets.settlement,
            [("slug", (g["slug"],), {}), ("slug-kw", (), {"slug": g["slug"]})])
        if s is not None:
            log(f"  settlement[{lbl}] {g['slug']}: {jd(s, 900)}")
        else:
            log(f"  settlement failed for {g['slug']} (see errors above).")
    log("\nDone. Download mlb_book_probe_out.txt for review.")

if __name__ == "__main__":
    try:
        main()
    finally:
        OUT.close()
