"""Diagnostic: dump the raw resolved matches so we can see WHY the calibration
looks broken. Specifically: does the recorded market price track the score, and
are the score / price / outcome consistent about who is winning?

Run: venv/bin/python diag_resolved.py
"""
import json
import os
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


def healthy(r):
    s = r.get("market_sum")
    return isinstance(s, (int, float)) and 0.9 <= s <= 1.1


def completed_winner(score):
    sp1 = sp2 = 0
    for s in str(score).split(","):
        s = s.strip()
        if not s:
            continue
        try:
            a, b = map(int, s.split("-"))
        except Exception:
            return None
        w = None
        if a >= 6 and a - b >= 2:
            w = "p1"
        elif b >= 6 and b - a >= 2:
            w = "p2"
        elif a == 7 and b in (5, 6):
            w = "p1"
        elif b == 7 and a in (5, 6):
            w = "p2"
        if w == "p1":
            sp1 += 1
        elif w == "p2":
            sp2 += 1
    if sp1 >= 2:
        return "p1"
    if sp2 >= 2:
        return "p2"
    return None


byslug = defaultdict(list)
for r in recs:
    byslug[r.get("slug") or r.get("match")].append(r)

shown = 0
for slug, rs in byslug.items():
    trust = [r for r in rs if r.get("has_book") and healthy(r)]
    if not trust:
        continue
    last = rs[-1]
    w = completed_winner(last.get("score", ""))
    src = "score"
    if w is None:
        mp = num(last.get("market_p1"))
        if mp is not None and mp >= 0.9:
            w, src = "p1", "price"
        elif mp is not None and mp <= 0.1:
            w, src = "p2", "price"
        else:
            w, src = "?", "none"
    print(f"\n=== {last.get('match','?')} ===")
    print(f"  outcome={w} (via {src}) | final score='{last.get('score')}' "
          f"final mkt_p1={last.get('market_p1')} mkt_p2={last.get('market_p2')}")
    idxs = sorted(set([0, len(trust) // 2, len(trust) - 1]))
    for i in idxs:
        r = trust[i]
        print(f"   score='{str(r.get('score','')):<18}' model_p1={r.get('model_p1')} "
              f"mkt_p1={r.get('market_p1')} bid/ask={r.get('best_bid')}/{r.get('best_ask')} "
              f"redge1={r.get('redge_p1')}")
    shown += 1
    if shown >= 10:
        break

if shown == 0:
    print("No resolved matches with trustworthy records to inspect yet.")
