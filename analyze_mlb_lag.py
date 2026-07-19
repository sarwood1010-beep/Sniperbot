#!/usr/bin/env python3
"""analyze_mlb_lag.py -- Screen-2 lag/efficiency analysis for MLB on Polymarket US.
Reads mlb_data.jsonl (from mlb_collect.py) and asks: does the PM price LAG the
StatsAPI live win-prob (edge) or track it (efficient, like tennis)?

Runs LOCALLY on the FreeCAD python (stdlib + core.py only; no network).

Method (reuses the tennis lag/convergence idea, adapted for MLB + the ~30s WP
reference latency):
  * Build per-game series (poll_ts, pm=currentPx=P(away), wp=wp_away/100).
  * DROP doubleheader slugs (>1 gamePk under one slug -> PM price and WP are for
    different games). Light-dedup the double-run overlap.
  * WP-JUMP events: a play where wp moves >= JUMP (a run / big out) -- the moment
    most likely to expose a lag. At the first tick AFTER the jump, gap0 = pm - wp.
    A FRESH divergence = |gap0| >= DIVERGE.
  * Over the next 1..HORIZON min, measure gap_closed = (|gap0|-|gap_h|)/|gap0| and
    converge (did PM move toward wp). Aggregate median gap_closed + converge_rate.
  * IMPORTANT (latency): StatsAPI WP is ~30s behind live TV. If PM has ALREADY
    moved by the time the WP jump appears (few/no fresh divergences), that is
    EFFICIENCY, not a bug. A real edge = many fresh divergences that PM then closes.
  * Cost hurdle: median capturable move vs core.round_trip_cost on real quotes.

Usage:  "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" analyze_mlb_lag.py [path]
"""
import sys, json, os
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

# ---- tunables (stated up front; the bar lives in MLB_SCREEN.md) -------------
JUMP = 0.03       # min |wp change| to count a play as a repricing "jump"
DIVERGE = 0.06    # |pm - wp| that counts as a divergence (the 6pt bar)
HORIZON = 5.0     # minutes to watch PM close the gap
FEE = 0.02        # per-side fee fraction assumed for the cost hurdle (harsh)
SLIP = 0.005      # per-side slippage assumed (harsh)

def load(path):
    recs = [json.loads(l) for l in open(path) if l.strip()]
    ticks = [r for r in recs if r.get("type") == "tick"]
    setts = [r for r in recs if r.get("type") == "settlement"]
    return ticks, setts

def ts(s):
    return datetime.fromisoformat(s)

def build_series(ticks, setts=()):
    """slug -> sorted list of dicts, dropping DH slugs + light dedup. A slug is a
    doubleheader/ambiguous mapping if MORE THAN ONE gamePk is associated with it
    across ticks AND settlements (the PM price and the joined WP are then for
    different games -- e.g. pit-cle: ticks=game2 WP, settlement=game1 market)."""
    bySlug = defaultdict(list)
    for t in ticks:
        bySlug[t["slug"]].append(t)
    settle_pks = defaultdict(set)
    for s in setts:
        settle_pks[s["slug"]].add(s["gamePk"])
    series = {}
    dropped = {}
    for slug, ts_list in bySlug.items():
        pks = set(t["gamePk"] for t in ts_list) | settle_pks.get(slug, set())
        if len(pks) > 1:
            dropped[slug] = f"doubleheader ({len(pks)} gamePks {sorted(pks)})"
            continue
        ts_list.sort(key=lambda t: t["poll_ts"])
        # light dedup: collapse ticks < 5s apart with identical pm+wp
        clean, last = [], None
        for t in ts_list:
            if t.get("currentPx") is None or t.get("wp_away") is None:
                continue
            key = (round(t["currentPx"], 4), t["wp_away"])
            if last and key == last[0] and (ts(t["poll_ts"]) - last[1]).total_seconds() < 5:
                continue
            clean.append(t); last = (key, ts(t["poll_ts"]))
        if len(clean) >= 3:
            series[slug] = clean
        else:
            dropped[slug] = f"too few usable ticks ({len(clean)})"
    return series, dropped

def pm_at(series_ticks, t0, horizon_min):
    """PM currentPx at the tick closest to t0 + horizon (within +/-40s)."""
    target = t0 + _minutes(horizon_min)
    best, bestdt = None, 1e9
    for t in series_ticks:
        dt = abs((ts(t["poll_ts"]) - target).total_seconds())
        if dt < bestdt:
            bestdt, best = dt, t
    return (best["currentPx"] if best and bestdt <= 40 else None)

def _minutes(m):
    from datetime import timedelta
    return timedelta(minutes=m)

def analyze(series):
    events = []
    for slug, tks in series.items():
        prev_wp = None
        for i, t in enumerate(tks):
            wp = t["wp_away"] / 100.0
            pm = t["currentPx"]
            t0 = ts(t["poll_ts"])
            if prev_wp is not None and abs(wp - prev_wp) >= JUMP:
                gap0 = pm - wp                       # PM minus (new) reference
                pm_h = pm_at(tks, t0, HORIZON)
                if pm_h is not None:
                    gap_h = pm_h - wp
                    closed = (abs(gap0) - abs(gap_h)) / abs(gap0) if abs(gap0) > 1e-9 else 0.0
                    events.append({
                        "slug": slug, "t": t["poll_ts"], "wp_jump": wp - prev_wp,
                        "gap0": gap0, "gap_h": gap_h, "closed": closed,
                        "fresh": abs(gap0) >= DIVERGE,
                        "converge": abs(gap_h) < abs(gap0),
                        "bid": t.get("best_bid"), "ask": t.get("best_ask"),
                    })
            prev_wp = wp
    return events

def median(xs):
    xs = sorted(xs)
    return xs[len(xs)//2] if xs else None

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "mlb_data.jsonl"
    ticks, setts = load(path)
    series, dropped = build_series(ticks, setts)
    # unique settlements (dedup the restart re-emissions)
    uniq_settle = {(s["slug"], s["gamePk"]): s for s in setts}
    print(f"=== MLB Screen-2 lag analysis :: {os.path.basename(path)} ===")
    print(f"ticks={len(ticks)}  settlements(raw={len(setts)}, unique={len(uniq_settle)})")
    print(f"games with usable tick series: {len(series)}   dropped: {len(dropped)}")
    for slug, why in dropped.items():
        print(f"  drop {slug}: {why}")
    print()
    # coverage + movement per game
    print(f"{'slug':30} {'ticks':>5} {'wpAway%':>12} {'PM(away)':>12} {'moved?':>7}")
    for slug, tks in series.items():
        wps = [t["wp_away"] for t in tks]; pms = [t["currentPx"] for t in tks]
        moved = (max(pms) - min(pms)) >= 0.05
        print(f"{slug:30} {len(tks):>5} {min(wps):>5.0f}-{max(wps):<5.0f} "
              f"{min(pms):>5.2f}-{max(pms):<5.2f} {'YES' if moved else 'flat':>7}")
    # events
    events = analyze(series)
    fresh = [e for e in events if e["fresh"]]
    print(f"\nWP-jump events (|dwp|>={JUMP*100:.0f}pts): {len(events)}   "
          f"of which FRESH divergences (|gap|>={DIVERGE*100:.0f}pts): {len(fresh)}")
    if events:
        allmid = median([e["closed"] for e in events])
        allconv = sum(e["converge"] for e in events) / len(events)
        print(f"  ALL jumps: median gap_closed={allmid:+.2f}  converge_rate={allconv*100:.0f}%")
    if fresh:
        fmid = median([e["closed"] for e in fresh])
        fconv = sum(e["converge"] for e in fresh) / len(fresh)
        print(f"  FRESH   : median gap_closed={fmid:+.2f}  converge_rate={fconv*100:.0f}%")
        print(f"\n  {'slug':26} {'t':8} {'dwp':>5} {'gap0':>6} {'gap_h':>6} {'closed':>6} {'conv':>4}")
        for e in fresh:
            hh = e["t"][11:19]
            print(f"  {e['slug']:26} {hh} {e['wp_jump']*100:>+5.0f} {e['gap0']*100:>+6.0f} "
                  f"{e['gap_h']*100:>+6.0f} {e['closed']:>+6.2f} {'Y' if e['converge'] else 'n':>4}")
    # cost hurdle from a representative live quote
    quotes = [(t.get("best_bid"), t.get("best_ask")) for tks in series.values() for t in tks
              if t.get("best_bid") and t.get("best_ask")]
    if quotes:
        costs = [core.round_trip_cost(b, a, SLIP, FEE) for b, a in quotes]
        costs = [c for c in costs if c is not None]
        if costs:
            print(f"\nround-trip cost hurdle (spread+2fee+2slip, fee={FEE},slip={SLIP}): "
                  f"median={median(costs)*100:.1f}c across {len(costs)} quotes")
    print("\nverdict guide: EDGE needs FRESH gap_closed >= +0.40 AND converge >= 60% "
          f"AND capturable move > 2x cost. Efficient = few fresh / gap_closed ~0 / PM leads.")

if __name__ == "__main__":
    main()
