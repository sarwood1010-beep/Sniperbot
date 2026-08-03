#!/usr/bin/env python3
"""analyze_mlb_bias.py -- edge idea #3: is the Kalshi MLB pre-game price
systematically BIASED? Reads mlb_bias_data.jsonl (from mlb_bias_fetch.py).

Why this idea is worth testing after the others died: it needs NO speed and NO
two-source timing (the artifact that killed the cross-venue "arb"). It is one
venue, one price per game, one outcome -- so a bias, if real, is capturable at
leisure. But it needs a BIG sample, hence the whole-season pull.

Tests (each with a significance check, because a 3-4pt effect is what "tradeable"
looks like and noise at n~900 is ~1.7pt):
  1. CALIBRATION / favorite-longshot bias: bucket by pre-game price; compare the
     implied probability to the realized win rate. Classic retail bias = longshots
     overpriced (lose more than priced) and heavy favorites underpriced.
  2. POPULAR-TEAM bias: are big-market/high-following teams overpriced (fan money)?
     Tested as a GROUP (not 30 separate tests -> avoids multiple-comparison mining).
  3. HOME/AWAY bias.
  4. Every effect is judged against a COST HURDLE, not just p<0.05: an edge must
     clear the spread + fees to be real money.

z-test on a proportion: z = (actual - implied) / sqrt(p(1-p)/n). |z|>2 ~ p<0.05.
NOTE ON MULTIPLE COMPARISONS: several buckets are tested; with ~8 buckets, one
|z|>2 by chance is expected ~1 in 3 runs. Treat a single bucket hit as noise unless
the pattern is MONOTONIC across buckets (the signature of a real bias).

Usage:  "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" analyze_mlb_bias.py [path]
"""
import sys, json, os, math
from collections import defaultdict

# Big-market / high-following clubs (national fanbases -> most "fan money").
# Fixed BEFORE looking at results, to avoid fitting the list to the data.
POPULAR = {"NYY", "LAD", "BOS", "CHC", "NYM", "STL", "SF", "PHI", "ATL", "HOU"}
FEE_HURDLE = 0.02   # ~Kalshi fee at mid + spread; an edge must beat this

def load(path):
    rows = []
    for l in open(path):
        l = l.strip()
        if not l: continue
        try:
            r = json.loads(l)
            if r.get("pregame_px") is not None and r.get("result_away_win") in (0, 1):
                rows.append(r)
        except Exception: pass
    return rows

def z_of(actual, implied, n):
    if n == 0 or implied <= 0 or implied >= 1: return 0.0
    se = math.sqrt(implied * (1 - implied) / n)
    return (actual - implied) / se if se else 0.0

def report(label, wins, n, implied_sum):
    if n == 0:
        print(f"  {label:22} n=0"); return None
    act = wins / n; imp = implied_sum / n
    z = z_of(act, imp, n)
    edge = act - imp
    flag = "  <== beats cost" if (abs(edge) > FEE_HURDLE and abs(z) > 2) else ""
    print(f"  {label:22} n={n:>4} implied={imp*100:5.1f}% actual={act*100:5.1f}% "
          f"edge={edge*100:+5.1f}pt z={z:+5.2f}{flag}")
    return edge, z, n

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "mlb_bias_data.jsonl"
    rows = load(path)
    print(f"=== MLB pre-game bias :: {os.path.basename(path)} ===")
    print(f"games with pregame price + outcome: {len(rows)}")
    if len(rows) < 50:
        print("too few games -- run mlb_bias_fetch.py first."); return
    dates = sorted(set(r["date"] for r in rows))
    print(f"date range: {dates[0]} -> {dates[-1]}  ({len(dates)} dates)")
    # sanity: overall away win rate vs average implied
    n = len(rows); wins = sum(r["result_away_win"] for r in rows)
    imp = sum(r["pregame_px"] for r in rows) / n
    print(f"\noverall: away win rate={100*wins/n:.1f}%  mean implied={100*imp:.1f}%  "
          f"(vig makes implied slightly high)")
    print(f"power: at n={n}, 1 SE ~ {100*math.sqrt(0.25/n):.1f}pt -> can only trust effects >~{2*100*math.sqrt(0.25/n):.1f}pt")

    # ---- 1. calibration / favorite-longshot ------------------------------
    print("\n[1] CALIBRATION by pre-game price (favorite-longshot bias)")
    buckets = [(0.0,0.25),(0.25,0.35),(0.35,0.45),(0.45,0.55),(0.55,0.65),(0.65,0.75),(0.75,1.01)]
    res = []
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r["pregame_px"] < hi]
        if not sub: continue
        res.append((f"{lo:.2f}-{hi:.2f}", report(f"px {lo:.2f}-{hi:.2f}",
                    sum(r["result_away_win"] for r in sub), len(sub),
                    sum(r["pregame_px"] for r in sub))))
    edges = [v[1][0] for v in res if v[1]]
    if len(edges) >= 4:
        mono_up = all(edges[i] <= edges[i+1] + 1e-9 for i in range(len(edges)-1))
        mono_dn = all(edges[i] >= edges[i+1] - 1e-9 for i in range(len(edges)-1))
        print(f"  monotonic across buckets? {'YES-increasing' if mono_up else 'YES-decreasing' if mono_dn else 'no'}"
              "   (monotonic = real bias signature; scattered = noise)")

    # ---- 2. popular-team bias -------------------------------------------
    print("\n[2] POPULAR-TEAM bias (fan money on big-market clubs)")
    print(f"  popular set (fixed in advance): {sorted(POPULAR)}")
    for label, pick in (("popular = AWAY", lambda r: r["away"] in POPULAR and r["home"] not in POPULAR),
                        ("popular = HOME", lambda r: r["home"] in POPULAR and r["away"] not in POPULAR)):
        sub = [r for r in rows if pick(r)]
        if not sub: continue
        if label.endswith("AWAY"):
            report(label, sum(r["result_away_win"] for r in sub), len(sub),
                   sum(r["pregame_px"] for r in sub))
        else:   # measure the popular (home) side: win = 1 - away_win, implied = 1 - px
            report(label, sum(1-r["result_away_win"] for r in sub), len(sub),
                   sum(1-r["pregame_px"] for r in sub))

    # ---- 3. home/away ----------------------------------------------------
    print("\n[3] HOME/AWAY bias (all games, measured on the AWAY side)")
    report("all away sides", wins, n, sum(r["pregame_px"] for r in rows))

    # ---- 4. verdict ------------------------------------------------------
    print(f"\nVERDICT guide: a tradeable bias needs |edge| > ~{FEE_HURDLE*100:.0f}pt (cost) AND "
          f"|z| > 2 AND a monotonic/structural pattern -- not one lucky bucket.")

if __name__ == "__main__":
    main()
