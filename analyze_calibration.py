"""Calibration analysis of the Stage-1 measurement log — the tool that actually
answers "is there an edge?"

The core test: on matches that finished, whose probability was closer to what
ACTUALLY happened — our MODEL or the MARKET? If the model is consistently closer,
that's a real edge. If the market is closer, our model has no edge (it just
disagrees, and it's the one that's wrong).

Reliable outcome reconstruction (fixes the in-progress-set bug in the earlier
convergence tool): a match counts as RESOLVED only if its last snapshot is
genuinely decisive — a COMPLETED best-of-3 score, or a market price >= 0.9 /
<= 0.1. Everything else is 'unresolved' (feed dropped it mid-match) and is
excluded, so we never score against a guessed outcome.

Read the PER-MATCH numbers. The sample is small and correlated; this is a
DIRECTIONAL instrument that sharpens as data grows, not a verdict.

Run: venv/bin/python analyze_calibration.py
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


def num(v):
    return v if isinstance(v, (int, float)) else None


def completed_winner(score):
    """Winner from a COMPLETED best-of-3 only. In-progress sets contribute
    nothing (this is the fix). Returns 'p1' / 'p2' / None."""
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


def state_bucket(score):
    """Coarse in-play situation label from the score."""
    sets = [s for s in str(score).split(",") if s.strip()]
    if not sets:
        return "?"
    sp1 = sp2 = 0
    for s in sets[:-1]:
        try:
            a, b = map(int, s.split("-"))
        except Exception:
            return "?"
        if a > b:
            sp1 += 1
        elif b > a:
            sp2 += 1
    try:
        g1, g2 = map(int, sets[-1].split("-"))
    except Exception:
        return "?"
    sd, gd = sp1 - sp2, g1 - g2
    if sp1 + sp2 == 0 and abs(gd) <= 1 and g1 + g2 <= 2:
        return "early_even"
    if sd > 0 and gd >= 1:
        return "p1_set+lead"
    if sd < 0 and gd <= -1:
        return "p2_set+lead"
    if sd > 0:
        return "p1_set_up"
    if sd < 0:
        return "p2_set_up"
    if gd >= 2:
        return "p1_break_up"
    if gd <= -2:
        return "p2_break_up"
    return "even"


# ── reliable outcome per match (from the LAST snapshot) ──
last = {}
for r in recs:
    slug = r.get("slug") or r.get("match")
    if slug:
        last[slug] = r      # ts-sorted -> ends on the latest
outcome = {}
for slug, r in last.items():
    w = completed_winner(r.get("score", ""))
    if w is None:
        mp = num(r.get("market_p1"))
        if mp is not None:
            if mp >= 0.9:
                w = "p1"
            elif mp <= 0.1:
                w = "p2"
    outcome[slug] = w
resolved = {s: w for s, w in outcome.items() if w}

print("=== calibration analysis ===")
print(f"records: {len(recs)} | matches: {len(last)} | "
      f"RESOLVED: {len(resolved)} | unresolved: {len(last)-len(resolved)}")
if not resolved:
    print("\nNo matches resolved to a decisive end yet — need the bot to track")
    print("matches through to completion. Nothing to calibrate. Let data grow.")
    raise SystemExit

# trustworthy records on resolved matches
good = []
for r in recs:
    if not (r.get("has_book") and healthy(r)):
        continue
    slug = r.get("slug") or r.get("match")
    if slug not in resolved:
        continue
    m, mk = num(r.get("model_p1")), num(r.get("market_p1"))
    if m is None or mk is None:
        continue
    good.append((slug, m, mk, 1 if resolved[slug] == "p1" else 0,
                 state_bucket(r.get("score", ""))))
print(f"trustworthy records on resolved matches: {len(good)} "
      f"across {len(set(g[0] for g in good))} matches")
if not good:
    print("(no trustworthy records on resolved matches yet)")
    raise SystemExit


def brier(rows, idx):
    return sum((r[idx] - r[3]) ** 2 for r in rows) / len(rows)


# ── THE headline: is the model or the market closer to reality? ──
print("\n--- WHOSE PROBABILITY IS CLOSER TO REALITY? (Brier score, lower=better) ---")
print(f"per-record ({len(good)} records, correlated):")
print(f"  model  Brier: {brier(good, 1):.4f}")
print(f"  market Brier: {brier(good, 2):.4f}")
# per-match: one representative record per match (the last trustworthy one)
per_match = {}
for g in good:
    per_match[g[0]] = g      # last wins
pm = list(per_match.values())
print(f"per-match ({len(pm)} matches, de-correlated — the honest read):")
print(f"  model  Brier: {brier(pm, 1):.4f}")
print(f"  market Brier: {brier(pm, 2):.4f}")
verdict = "MODEL closer -> possible edge" if brier(pm, 1) < brier(pm, 2) else \
          "MARKET closer -> model has no edge here"
print(f"  -> {verdict}")
# a coin-flip baseline (always 0.5) Brier = 0.25 for reference
print(f"  (reference: always-50%% Brier = 0.2500)")

# ── calibration table by model probability bucket ──
print("\n--- CALIBRATION by model prob (P1), per-record ---")
print("  bucket   n   avg_model avg_market  actual  model_err market_err")
buckets = {}
for g in good:
    b = min(int(g[1] * 10), 9)
    buckets.setdefault(b, []).append(g)
for b in sorted(buckets):
    rows = buckets[b]
    n = len(rows)
    am = sum(r[1] for r in rows) / n
    amk = sum(r[2] for r in rows) / n
    aw = sum(r[3] for r in rows) / n
    print(f"  {b*10:2d}-{b*10+10:<3d} {n:3d}   {am*100:5.0f}%   {amk*100:5.0f}%    "
          f"{aw*100:5.0f}%   {(am-aw)*100:+5.0f}     {(amk-aw)*100:+5.0f}")
print("  (model_err/market_err = avg predicted - actual; + = overconfident on P1)")

# ── by in-play state ──
print("\n--- by in-play state (per-record) ---")
print("  state           n   avg_model avg_market  actual")
st = {}
for g in good:
    st.setdefault(g[4], []).append(g)
for s in sorted(st, key=lambda k: -len(st[k])):
    rows = st[s]
    n = len(rows)
    print(f"  {s:15s} {n:3d}   {sum(r[1] for r in rows)/n*100:5.0f}%   "
          f"{sum(r[2] for r in rows)/n*100:5.0f}%    {sum(r[3] for r in rows)/n*100:5.0f}%")

print("\nNOTE: small, correlated sample — DIRECTIONAL only. The Brier comparison is")
print("the number to watch as data grows: if the model stays below the market, we")
print("have a real edge; if not, the model is just disagreeing and losing.")
