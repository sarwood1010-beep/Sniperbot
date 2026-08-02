#!/usr/bin/env python3
"""analyze_mlb_xvenue.py -- cross-venue (Polymarket US vs Kalshi) gap analysis for
MLB, from mlb_xvenue.jsonl. Tests edge idea #1: do the two venues DISAGREE on the
same game-winner binary by enough to trade, after both venues' costs?

The edge is model-free and speed-insensitive, so the questions are:
  1. Gap distribution: pm_mid - k_mid -- median/tails, split pregame vs live.
  2. Executable arb: best = max(k_bid - pm_ask, pm_bid - k_ask). How often > 0 raw?
  3. NET of cost: subtract the Kalshi fee (ceil(0.07*P*(1-P)) per contract, ~2c at
     mid) + an assumed PM fee. How often does NET arb clear > 0? (PM fee unknown ->
     default 0 = BEST case; if even that rarely clears, it is a clean NO-GO.)
  4. Persistence: when a gap exists, how many consecutive 20s polls does it last
     (can we actually get both fills)?
  5. Lead: is pm_mid systematically above/below k_mid (which venue leads)?

Usage:  "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" analyze_mlb_xvenue.py [path]
"""
import sys, json, os, math
from collections import defaultdict
from datetime import datetime

PM_FEE = 0.0      # per-contract PM fee (unknown; 0 = best case for the edge)
HURDLE_EXTRA = 0.0

def load(path): return [json.loads(l) for l in open(path) if l.strip()]
def mid(b, a): return (b + a) / 2 if (b is not None and a is not None) else None
def kalshi_fee(p):
    if p is None: p = 0.5
    return math.ceil(0.07 * p * (1 - p) * 100) / 100.0   # ceil to cent

def pct(xs, q):
    if not xs: return None
    xs = sorted(xs); i = min(len(xs)-1, int(q*len(xs)))
    return xs[i]

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "mlb_xvenue.jsonl"
    recs = load(path)
    rows = [r for r in recs if r.get("pm_bid") is not None and r.get("pm_ask") is not None
            and r.get("k_bid") is not None and r.get("k_ask") is not None]
    print(f"=== cross-venue PM vs Kalshi :: {os.path.basename(path)} ===")
    print(f"records={len(recs)}  both-quoted rows={len(rows)}")

    for state in ("pregame", "live", "ALL"):
        sub = [r for r in rows if state == "ALL" or r.get("game_state") == state]
        if not sub: continue
        gaps, bests, nets = [], [], []
        cross_raw = cross_net = 0
        for r in sub:
            pm_m = mid(r["pm_bid"], r["pm_ask"]); k_m = mid(r["k_bid"], r["k_ask"])
            gaps.append(pm_m - k_m)
            a1 = r["k_bid"] - r["pm_ask"]      # buy PM ask, sell Kalshi bid
            a2 = r["pm_bid"] - r["k_ask"]      # buy Kalshi ask, sell PM bid
            best = max(a1, a2); bests.append(best)
            fee = kalshi_fee(k_m) + PM_FEE
            net = best - fee; nets.append(net)
            if best > 0: cross_raw += 1
            if net > 0: cross_net += 1
        aG = [abs(g) for g in gaps]
        n = len(sub)
        print(f"\n--- {state}  (n={n}) ---")
        print(f"  gap pm_mid-k_mid: mean={sum(gaps)/n:+.4f}  median={pct(gaps,0.5):+.4f}  "
              f"|gap| p90={pct(aG,0.90):.3f} p99={pct(aG,0.99):.3f} max={max(aG):.3f}")
        print(f"  best executable arb: median={pct(bests,0.5):+.4f}  p99={pct(bests,0.99):+.4f}  max={max(bests):+.4f}")
        print(f"  raw crossings (best>0): {cross_raw}/{n} ({100*cross_raw/n:.1f}%)")
        print(f"  NET>0 after Kalshi fee (+PM fee={PM_FEE}): {cross_net}/{n} ({100*cross_net/n:.2f}%)")

    # tails: show the biggest NET arbs (where any real opportunity would be)
    scored = []
    for r in rows:
        k_m = mid(r["k_bid"], r["k_ask"])
        best = max(r["k_bid"] - r["pm_ask"], r["pm_bid"] - r["k_ask"])
        scored.append((best - kalshi_fee(k_m) - PM_FEE, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    print("\n=== top 12 NET-arb moments (net cents, after Kalshi fee) ===")
    print(f"  {'net':>6} {'state':7} {'slug':26} {'PMbid/ask':>12} {'Kbid/ask':>12}")
    for net, r in scored[:12]:
        print(f"  {net*100:>+5.1f}c {r.get('game_state','?'):7} {r['slug']:26} "
              f"{r['pm_bid']:.3f}/{r['pm_ask']:.3f}   {r['k_bid']:.3f}/{r['k_ask']:.3f}")

    # persistence of positive NET arbs (consecutive 20s polls per slug)
    byslug = defaultdict(list)
    for r in rows: byslug[r["slug"]].append(r)
    runs = []
    for s, rs in byslug.items():
        rs.sort(key=lambda r: r["poll_ts"])
        cur = 0
        for r in rs:
            k_m = mid(r["k_bid"], r["k_ask"])
            net = max(r["k_bid"]-r["pm_ask"], r["pm_bid"]-r["k_ask"]) - kalshi_fee(k_m) - PM_FEE
            if net > 0: cur += 1
            else:
                if cur: runs.append(cur)
                cur = 0
        if cur: runs.append(cur)
    if runs:
        print(f"\npositive-NET-arb streaks: {len(runs)} streaks, "
              f"median {sorted(runs)[len(runs)//2]} polls (~{sorted(runs)[len(runs)//2]*20}s), max {max(runs)} polls")
    else:
        print("\nNO positive-NET-arb streaks at all -- venues never cross after fees.")
    # ---- THE CONTROL THAT MATTERS: velocity ---------------------------------
    # The two venues are polled at slightly DIFFERENT instants. When the price is
    # repricing fast, that time skew alone manufactures a phantom "gap" that is
    # NOT tradeable. Any apparent arb must therefore be judged at QUIET moments.
    print("\n=== VELOCITY CONTROL (phantom-arb test) ===")
    byslug = defaultdict(list)
    for r in rows: byslug[r["slug"]].append(r)
    buckets = [(0, 0.005), (0.005, 0.02), (0.02, 0.05), (0.05, 9)]
    stats = {b: [0, 0, 0.0] for b in buckets}   # n, n_arb, sum|gap|
    for s, rs in byslug.items():
        rs.sort(key=lambda r: r["poll_ts"])
        for i in range(1, len(rs)):
            r, p = rs[i], rs[i-1]
            pm, k = mid(r["pm_bid"], r["pm_ask"]), mid(r["k_bid"], r["k_ask"])
            pm0, k0 = mid(p["pm_bid"], p["pm_ask"]), mid(p["k_bid"], p["k_ask"])
            vel = max(abs(pm-pm0), abs(k-k0))
            net = max(r["k_bid"]-r["pm_ask"], r["pm_bid"]-r["k_ask"]) - kalshi_fee(k) - PM_FEE
            for b in buckets:
                if b[0] <= vel < b[1]:
                    stats[b][0] += 1; stats[b][1] += (net > 0); stats[b][2] += abs(pm-k); break
    print(f"  {'move since last tick':>22} {'n':>6} {'mean|gap|':>10} {'NET>0 rate':>11}")
    for b in buckets:
        n, na, sg = stats[b]
        if not n: continue
        lbl = f"{b[0]*100:.1f}-{b[1]*100:.0f}c" if b[1] < 9 else f">{b[0]*100:.0f}c"
        print(f"  {lbl:>22} {n:>6} {sg/n*100:>9.2f}c {100*na/n:>10.2f}%")
    print("  If NET>0 rate is ~0 at QUIET moments but high at FAST moments, the 'arb'")
    print("  is a POLLING TIME-SKEW ARTIFACT (two venues sampled at different instants),")
    print("  NOT a real cross-venue disagreement. Only the quiet-moment rate is real.")

    print("\nVERDICT guide: EDGE only if NET>0 is common AT QUIET MOMENTS and persists "
          ">= a few polls. High NET>0 confined to fast-repricing moments = phantom.")

if __name__ == "__main__":
    main()
