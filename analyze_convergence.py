"""Convergence backtest of the Stage-1 trustworthy signals.

For each trustworthy would-fire (live book + sane price sum + a realizable-edge
fire), we "buy" the side the model thinks is underpriced, then ask:

  1. CONVERGENCE — did the market later move TOWARD our model (validating the
     call), or away from it (model was wrong)?
  2. CLAIMED vs REALIZED — the model promised some edge at entry; how much of
     that actually materialized? If realized << claimed, the model is
     overconfident.
  3. OUTCOME PROXY — on matches that reached a decided final price, did the side
     we would have bought actually win?

DISCIPLINE: this is a DIRECTIONAL read on a SMALL, CORRELATED sample (many
snapshots of a few matches). Read the PER-MATCH numbers, not per-signal, and do
not treat a good result as a green light for real money.

Run: venv/bin/python analyze_convergence.py
"""
import json
import os

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


def healthy(r):
    s = r.get("market_sum")
    return isinstance(s, (int, float)) and 0.9 <= s <= 1.1


def mp1(r):
    v = r.get("market_p1")
    return float(v) if isinstance(v, (int, float)) else None


def winner_from_final_score(score):
    """Winner ('p1'/'p2') if the final score shows a completed best-of-3, else None."""
    sp1 = sp2 = 0
    for s in str(score).split(","):
        s = s.strip()
        if not s:
            continue
        try:
            a, b = s.split("-")
            a, b = int(a), int(b)
        except Exception:
            return None
        if a > b:
            sp1 += 1
        elif b > a:
            sp2 += 1
    if sp1 >= 2:
        return "p1"
    if sp2 >= 2:
        return "p2"
    return None


# latest (final) snapshot per match — our outcome proxy
final = {}
for r in recs:
    slug = r.get("slug") or r.get("match")
    if slug and mp1(r) is not None:
        final[slug] = r      # recs are ts-sorted, so this ends on the latest

DECIDED_HI, DECIDED_LO = 0.85, 0.15
signals = []
for r in recs:
    if not (r.get("has_book") and healthy(r)):
        continue
    if r.get("fire_p1"):
        buy, claimed = "p1", r.get("redge_p1")
    elif r.get("fire_p2"):
        buy, claimed = "p2", r.get("redge_p2")
    else:
        continue
    slug = r.get("slug") or r.get("match")
    entry = mp1(r)
    fr = final.get(slug)
    if entry is None or fr is None or fr is r or fr["ts"] <= r["ts"]:
        continue
    fp = mp1(fr)
    if fp is None:
        continue
    d = 1.0 if buy == "p1" else -1.0
    conv = d * (fp - entry)                      # + = market moved toward model
    # outcome: prefer a completed final score; fall back to a decided final price
    w = winner_from_final_score(fr.get("score", ""))
    if w is None:
        if fp >= DECIDED_HI:
            w = "p1"
        elif fp <= DECIDED_LO:
            w = "p2"
    hit = (buy == w) if w else None
    signals.append({"slug": slug, "match": r.get("match", "?"), "buy": buy,
                    "entry": entry, "final": fp, "conv": conv,
                    "claimed": (float(claimed) if isinstance(claimed, (int, float)) else None),
                    "hit": hit})

n = len(signals)
print("=== convergence backtest (trustworthy signals) ===")
print(f"signals with follow-up data: {n}")
if not n:
    print("No trustworthy signals with later snapshots yet.")
    raise SystemExit
matches = sorted(set(s["slug"] for s in signals))
print(f"distinct matches (the REAL sample size): {len(matches)}")

pos = sum(1 for s in signals if s["conv"] > 0)
avg_conv = sum(s["conv"] for s in signals) / n
claimed_vals = [s["claimed"] for s in signals if s["claimed"] is not None]
avg_claimed = sum(claimed_vals) / len(claimed_vals) if claimed_vals else 0.0
print("\nPER-SIGNAL (correlated — read with caution):")
print(f"  market moved toward model: {pos}/{n} ({100*pos/n:.0f}%)")
print(f"  avg realized move toward model: {avg_conv*100:+.1f} pts")
print(f"  avg CLAIMED edge at entry:      {avg_claimed*100:+.1f} pts")
if avg_claimed:
    print(f"  realized / claimed: {avg_conv/avg_claimed:+.2f}   "
          f"({'model looks OVERCONFIDENT' if avg_conv < 0.5*avg_claimed else 'holding up so far'})")

# per-match (de-correlated) — the honest read
print("\nPER-MATCH (de-correlated — the honest read):")
pm_conv = []
for slug in matches:
    ss = [s for s in signals if s["slug"] == slug]
    pm_conv.append(sum(s["conv"] for s in ss) / len(ss))
pm_pos = sum(1 for m in pm_conv if m > 0)
print(f"  matches where market moved toward model: {pm_pos}/{len(matches)} ({100*pm_pos/len(matches):.0f}%)")
print(f"  avg per-match move toward model: {sum(pm_conv)/len(pm_conv)*100:+.1f} pts")

# outcome proxy
decided = [s for s in signals if s["hit"] is not None]
if decided:
    hits = sum(1 for s in decided if s["hit"])
    dm = sorted(set(s["slug"] for s in decided))
    print("\nOUTCOME PROXY (matches that reached a decided/completed state):")
    print(f"  decided signals: {len(decided)} across {len(dm)} matches")
    print(f"  model's pick won: {hits}/{len(decided)} ({100*hits/len(decided):.0f}%)")
else:
    print("\nOUTCOME PROXY: no matches reached a decided state in the log yet.")

print("\n--- sample signals (entry -> final of that match) ---")
for s in signals[-12:]:
    tag = "" if s["hit"] is None else ("WON " if s["hit"] else "LOST")
    cl = f"{s['claimed']*100:+.0f}" if s["claimed"] is not None else "  ?"
    print(f"  {s['match'][:26]:26} buy {s['buy']} {s['entry']*100:3.0f}%->{s['final']*100:3.0f}% "
          f"conv {s['conv']*100:+4.0f} claim {cl} {tag}")

print("\nNOTE: 'final snapshot' is a rough outcome proxy (the feed can drop a match")
print("before it ends). A clean settlement record is the next add. DIRECTIONAL ONLY —")
print("read per-match numbers, and let the sample grow before believing any of it.")
