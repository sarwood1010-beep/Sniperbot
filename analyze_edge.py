"""Summarize the whole Stage-1 measurement log (not just the last 15).

Run on the droplet, from the bot folder:
    venv/bin/python analyze_edge.py

Reports volume, data quality (how much has a real live book and a sane price
sum vs. thin-market noise), would-fire counts, spread distribution, and the
trustworthy candidates — the ones on a liquid market where the realizable edge
(model minus the ask you'd actually pay) cleared the bar."""
import json
import os
import time

PATHS = ["/home/deploy/polymarket-discord-bot/measurement_log.jsonl",
         "measurement_log.jsonl"]
path = next((p for p in PATHS if os.path.exists(p)), None)
if not path:
    print("No measurement_log.jsonl found yet.")
    raise SystemExit

recs = []
with open(path) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except Exception:
            pass

n = len(recs)
print(f"=== Stage-1 measurement log ===")
print(f"total records: {n}")
if not n:
    raise SystemExit

ts = [r.get("ts") for r in recs if isinstance(r.get("ts"), (int, float))]
if ts:
    span_h = (max(ts) - min(ts)) / 3600.0
    print(f"time span: {span_h:.1f} h  "
          f"(first {time.strftime('%m-%d %H:%M', time.gmtime(min(ts)))}Z "
          f"-> last {time.strftime('%m-%d %H:%M', time.gmtime(max(ts)))}Z)")
    rate = n / span_h if span_h > 0 else n
    print(f"rate: ~{rate:.0f} records/hour")


def healthy(r):
    s = r.get("market_sum")
    return isinstance(s, (int, float)) and 0.9 <= s <= 1.1


matches = set(r.get("slug") or r.get("match") for r in recs)
with_book = [r for r in recs if r.get("has_book")]
healthy_recs = [r for r in recs if healthy(r)]
usable = [r for r in recs if r.get("has_book") and healthy(r)]
fires = [r for r in recs if r.get("fire_p1") or r.get("fire_p2")]
good_fires = [r for r in fires if r.get("has_book") and healthy(r)]

print()
print(f"distinct matches seen:           {len(matches)}")
print(f"records with a live book:        {len(with_book)}  ({100*len(with_book)/n:.0f}%)")
print(f"records with sane price sum:     {len(healthy_recs)}  ({100*len(healthy_recs)/n:.0f}%)")
print(f"USABLE (book + sane sum):        {len(usable)}  ({100*len(usable)/n:.0f}%)")
print(f"would-fire flags (any):          {len(fires)}")
print(f"  of those, TRUSTWORTHY:         {len(good_fires)}  (book + sane sum)")

spreads = sorted(r.get("spread") for r in with_book
                 if isinstance(r.get("spread"), (int, float)))
if spreads:
    mid = spreads[len(spreads) // 2]
    print(f"spread on book records:          "
          f"min {min(spreads)*100:.0f}c | median {mid*100:.0f}c | max {max(spreads)*100:.0f}c")

if good_fires:
    print("\n--- trustworthy would-fire candidates (most recent 12) ---")
    for r in good_fires[-12:]:
        re1, re2 = r.get("redge_p1"), r.get("redge_p2")
        re = re1 if (re1 is not None and (re2 is None or re1 >= re2)) else re2
        print(f"  {r.get('match','?')[:30]:30} {str(r.get('score','')):13} "
              f"model {r.get('model_p1',0)*100:3.0f}% mkt {r.get('market_p1',0)*100:3.0f}% "
              f"spread {r.get('spread',0)*100:2.0f}c  realizable-edge {re}")
else:
    print("\nNo trustworthy candidates yet — need a LIQUID, WS-watched match to")
    print("match a live feed match. So far the matches are thin/lower-tier (no book).")
