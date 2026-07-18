"""Edge re-test on the FULL dataset (now that we know the market data is good).

Two decisive questions:
  1. CALIBRATION: does the MODEL predict outcomes better than the MARKET?
     (Brier score on resolved matches — lower is closer to reality.) If the model
     beats the market, that's a real informational edge.
  2. LAG / CONVERGENCE: when the model and market diverge, does the market later
     move TOWARD the model? That's the tradeable lag our thesis is built on.

Uses ALL records with a sane price sum (not just the tiny has_book subset).
Outcomes resolved reliably: a COMPLETED best-of-3 score, or a decisive final
price (>=0.9 / <=0.1). Read the PER-MATCH numbers.

Run: venv/bin/python analyze_edge_v2.py
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
    """Winner only from COMPLETED sets (in-progress sets ignored)."""
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


# ── resolve outcome per match (last snapshot) ──
byslug = defaultdict(list)
for r in recs:
    byslug[r.get("slug") or r.get("match")].append(r)
outcome = {}
for slug, rs in byslug.items():
    last = rs[-1]
    w = completed_winner(last.get("score", ""))
    if w is None:
        mp = num(last.get("market_p1"))
        if mp is not None and mp >= 0.9:
            w = "p1"
        elif mp is not None and mp <= 0.1:
            w = "p2"
    outcome[slug] = w
resolved = {s: w for s, w in outcome.items() if w}

print("=== edge re-test (full dataset) ===")
print(f"records: {len(recs)} | matches: {len(byslug)} | resolved: {len(resolved)}")
if not resolved:
    print("No resolved matches yet.")
    raise SystemExit

# ── CALIBRATION: model vs market Brier ──
# per match: average squared error over its (sane, non-settled) records, then
# average across matches (each match weighted equally).
per_match = {}   # slug -> (model_se_mean, market_se_mean, n)
cal_rows = []    # (model_p1, market_p1, p1_won) for the calibration table
for slug, w in resolved.items():
    y = 1 if w == "p1" else 0
    mse = kse = 0.0
    n = 0
    for r in byslug[slug]:
        if not healthy(r):
            continue
        m, mk = num(r.get("model_p1")), num(r.get("market_p1"))
        if m is None or mk is None:
            continue
        if mk <= 0.03 or mk >= 0.97:     # drop settlement snapshots
            continue
        mse += (m - y) ** 2
        kse += (mk - y) ** 2
        n += 1
        cal_rows.append((m, mk, y))
    if n:
        per_match[slug] = (mse / n, kse / n, n)

if per_match:
    mM = sum(v[0] for v in per_match.values()) / len(per_match)
    mK = sum(v[1] for v in per_match.values()) / len(per_match)
    print(f"\n--- CALIBRATION: does the model beat the market? "
          f"({len(per_match)} matches) ---")
    print(f"  per-match Brier (lower = closer to reality):")
    print(f"    MODEL : {mM:.4f}")
    print(f"    MARKET: {mK:.4f}")
    print(f"    reference (always 50%): 0.2500")
    if mM < mK:
        print(f"  -> MODEL predicts outcomes better -> POSSIBLE EDGE "
              f"(by {(mK-mM):.4f})")
    else:
        print(f"  -> MARKET predicts better -> no model edge overall "
              f"(model worse by {(mM-mK):.4f})")

    print("\n  calibration by model prob (P1), per-record:")
    print("   bucket  n   avg_model avg_market  actual")
    b = defaultdict(list)
    for m, mk, y in cal_rows:
        b[min(int(m * 10), 9)].append((m, mk, y))
    for k in sorted(b):
        rows = b[k]
        nn = len(rows)
        print(f"   {k*10:2d}-{k*10+10:<3d}{nn:4d}   {sum(x[0] for x in rows)/nn*100:5.0f}%"
              f"   {sum(x[1] for x in rows)/nn*100:5.0f}%    {sum(x[2] for x in rows)/nn*100:5.0f}%")

# ── LAG / CONVERGENCE: after model-market divergence, does market move toward model? ──
DIV = 0.08          # divergence threshold
HORIZON = 300       # seconds to look ahead
conv_vals = []
per_match_conv = defaultdict(list)
for slug, rs in byslug.items():
    for i, r in enumerate(rs):
        if not healthy(r):
            continue
        m, mk = num(r.get("model_p1")), num(r.get("market_p1"))
        if m is None or mk is None or mk <= 0.03 or mk >= 0.97:
            continue
        d = m - mk
        if abs(d) < DIV:
            continue
        # find a record ~HORIZON later in the same match
        fut = None
        for r2 in rs[i + 1:]:
            if r2["ts"] - r["ts"] >= HORIZON:
                fut = r2
                break
        if fut is None:
            continue
        mkf = num(fut.get("market_p1"))
        if mkf is None:
            continue
        conv = (1 if d > 0 else -1) * (mkf - mk)   # + = market moved toward model
        conv_vals.append(conv)
        per_match_conv[slug].append(conv)

if conv_vals:
    n = len(conv_vals)
    pos = sum(1 for c in conv_vals if c > 0)
    pm = [sum(v) / len(v) for v in per_match_conv.values()]
    pmpos = sum(1 for x in pm if x > 0)
    print(f"\n--- LAG / CONVERGENCE (after |model-market| >= {DIV*100:.0f}pts, "
          f"{HORIZON//60}min later) ---")
    print(f"  events: {n} across {len(per_match_conv)} matches")
    print(f"  per-event: market moved toward model {pos}/{n} ({100*pos/n:.0f}%), "
          f"avg {sum(conv_vals)/n*100:+.1f} pts")
    print(f"  per-match: {pmpos}/{len(pm)} matches ({100*pmpos/len(pm):.0f}%), "
          f"avg {sum(pm)/len(pm)*100:+.1f} pts")
    print("  (>50% and positive = the market lags and catches up to the model = edge)")
else:
    print("\nLAG/CONVERGENCE: not enough divergence events with follow-up yet.")

print("\nNOTE: market_p1 here is the REST price (tracks the score well per")
print("diag_bookmovement). Tradeability still needs the live book spread + real")
print("depth. This measures whether the SIGNAL is real; sizing is a separate step.")
