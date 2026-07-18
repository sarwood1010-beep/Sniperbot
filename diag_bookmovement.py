"""Decisive check: did the Polymarket price EVER track the live match, or was it
frozen ~50% (dead market / wrong-market pairing)?

For each match we compute, over its IN-PLAY snapshots (excluding near-settled
prices), the correlation between our MODEL (which tracks the score) and the
MARKET price. If the market tracked the game, that correlation is strongly
positive. If the market ignored the game (dead, or we paired to a pre-match /
wrong-date market), it's ~0.

Also prints each match's slug + slug-date, so we can see whether live matches
were paired to FUTURE-dated (pre-match) markets — the confound that would fake a
"dead market" reading.

Run: venv/bin/python diag_bookmovement.py
"""
import json
import os
import re
from collections import defaultdict

PATHS = ["/home/deploy/polymarket-discord-bot/measurement_log.jsonl",
         "measurement_log.jsonl"]
path = next((p for p in PATHS if os.path.exists(p)), None)
if not path:
    print("No measurement_log.jsonl found.")
    raise SystemExit

recs = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if isinstance(r.get("ts"), (int, float)):
                recs.append(r)
        except Exception:
            pass
recs.sort(key=lambda r: r["ts"])


def num(v):
    return v if isinstance(v, (int, float)) else None


def slug_date(slug):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", slug or "")
    return m.group(1) if m else "?"


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return 0.0  # one side never moved -> no tracking
    return sxy / (sxx ** 0.5 * syy ** 0.5)


byslug = defaultdict(list)
for r in recs:
    byslug[r.get("slug") or r.get("match")].append(r)

rows = []
for slug, rs in byslug.items():
    # in-play snapshots: exclude near-settled prices (0/1) and require a model value
    ip = []
    for r in rs:
        mk = num(r.get("market_p1"))
        m = num(r.get("model_p1"))
        if mk is None or m is None:
            continue
        if mk <= 0.03 or mk >= 0.97:      # drop settlement/near-settled
            continue
        ip.append((m, mk, r))
    if len(ip) < 5:
        continue
    models = [x[0] for x in ip]
    markets = [x[1] for x in ip]
    corr = pearson(models, markets)
    rows.append({
        "match": rs[-1].get("match", "?"),
        "slug": slug,
        "date": slug_date(slug),
        "n": len(ip),
        "model_rng": max(models) - min(models),
        "market_rng": max(markets) - min(markets),
        "market_max_dev": max(abs(x - 0.5) for x in markets),
        "corr": corr,
    })

if not rows:
    print("Not enough in-play data per match to judge. Let it collect more.")
    raise SystemExit

rows.sort(key=lambda r: -(r["corr"] if r["corr"] is not None else -9))
print("Did the MARKET price track the live match? (corr of market vs model)")
print(f"{'match':30} {'date':11} {'n':>3} {'model_rng':>9} {'mkt_rng':>8} {'mkt_dev':>8} {'corr':>6}")
for r in rows:
    c = "n/a" if r["corr"] is None else f"{r['corr']:+.2f}"
    print(f"{r['match'][:30]:30} {r['date']:11} {r['n']:3d} "
          f"{r['model_rng']:9.2f} {r['market_rng']:8.2f} {r['market_max_dev']:8.2f} {c:>6}")

tracked = [r for r in rows if (r["corr"] or 0) >= 0.4 and r["market_rng"] >= 0.12]
moved = [r for r in rows if r["market_rng"] >= 0.15]
print(f"\nmatches analyzed: {len(rows)}")
print(f"market price MOVED >=0.15 during play: {len(moved)}")
print(f"market TRACKED the score (corr>=0.4 & moved): {len(tracked)}")
avg_corr = sum(r["corr"] for r in rows if r["corr"] is not None) / \
    max(1, sum(1 for r in rows if r["corr"] is not None))
print(f"average corr(market, model): {avg_corr:+.2f}")
print("\nVERDICT:")
if tracked:
    print(f"  -> {len(tracked)} market(s) DID track the game. The venue is NOT dead;")
    print("     our pairing/coverage was the problem. Tennis is NOT ruled out.")
else:
    print("  -> No market tracked the game. Consistent with dead/wrong-market pairing.")
    print("     Check the 'date' column: if slugs are FUTURE-dated vs the match day,")
    print("     we were pairing live matches to pre-match markets (a fixable bug),")
    print("     NOT proof the venue is dead.")
