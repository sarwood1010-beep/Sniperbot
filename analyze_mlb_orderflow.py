#!/usr/bin/env python3
"""analyze_mlb_orderflow.py -- tests the PRE-GAME "smart money" order-flow
hypothesis on MLB / Polymarket US, from mlb_collect.py data (game_state=pregame).

Hypothesis B: a big pre-game openInterest jump ("smart money") precedes PM price
CONTINUATION -> follow it. For that to be tradeable, TWO things must hold:
  (1) big OI jumps must actually MOVE the price (else nothing to follow), and
  (2) that move must CONTINUE (not revert), and/or pre-game drift must predict the
      outcome (closing-line-value: sharp money makes the closing price sharper).

Tests, all on PM's own free OI/price feed (stdlib + core.py; no network):
  A. Per game: open->close pre-game price DRIFT vs OI growth vs the settled winner.
     Does pre-game drift predict the winner (info) at all?
  B. OI-JUMP events (dOI >= 10% OR >= ABS_OI shares): concurrent price move +
     forward price drift over HORIZON min. Continuation vs reversion vs flat.

Usage:  "C:\\Program Files\\FreeCAD 1.0\\bin\\python.exe" analyze_mlb_orderflow.py [path]
"""
import sys, json, os, re
from collections import defaultdict
from datetime import datetime, timedelta

PCT = 0.10        # OI jump threshold (relative)
ABS_OI = 5000.0   # OI jump threshold (absolute shares) -- catches big money on a big base
HORIZON = 5.0     # minutes to look for continuation after a jump
MOVE = 0.005      # min price move (0.5c) to count as "moved" (not noise)

def load(path):
    return [json.loads(l) for l in open(path) if l.strip()]

def date_of(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s or ""); return m.group(1) if m else "?"

def ts(s): return datetime.fromisoformat(s)

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "mlb_data.jsonl"
    recs = load(path)
    # date to analyze = the most common pregame date
    predates = defaultdict(int)
    for r in recs:
        if r.get("type") == "tick" and r.get("game_state") == "pregame":
            predates[date_of(r["slug"])] += 1
    if not predates:
        print("No pregame ticks in file (need a run started before first pitch)."); return
    day = max(predates, key=predates.get)
    print(f"=== MLB pre-game order-flow :: {os.path.basename(path)} :: {day} ===")
    # gather pregame series + winners + DH drop
    pre = defaultdict(list); pks = defaultdict(set); win = {}
    for r in recs:
        s = r.get("slug", "")
        if date_of(s) != day: continue
        pks[s].add(r.get("gamePk"))
        if r.get("type") == "settlement": win[s] = r.get("winner")
        elif r.get("type") == "tick" and r.get("game_state") == "pregame":
            pre[s].append(r)

    # ---- Test A: drift vs winner -------------------------------------------
    print("\n[A] pre-game price drift vs OI growth vs outcome")
    print(f"{'slug':26} {'open':>5} {'close':>5} {'drift':>6} {'OIgrow%':>8} {'winner':>6} {'pred?':>5}")
    hits = n = 0
    for s, tks in sorted(pre.items()):
        if len(pks[s]) > 1: continue  # doubleheader
        tks.sort(key=lambda r: r["poll_ts"])
        cur = [t for t in tks if t.get("currentPx") is not None]
        oi = [t for t in tks if t.get("oi")]
        if len(cur) < 5: continue
        drift = cur[-1]["currentPx"] - cur[0]["currentPx"]
        oig = (oi[-1]["oi"] - oi[0]["oi"]) / oi[0]["oi"] * 100 if oi else 0
        w = win.get(s, "?"); pred = "-"
        if w in ("away", "home") and abs(drift) > MOVE:
            fav = "away" if drift > 0 else "home"
            pred = "YES" if fav == w else "no"; hits += (fav == w); n += 1
        print(f"{s:26} {cur[0]['currentPx']:>5.2f} {cur[-1]['currentPx']:>5.2f} "
              f"{drift:>+6.2f} {oig:>8.1f} {w:>6} {pred:>5}")
    if n:
        print(f"  drift predicted winner: {hits}/{n} = {100*hits/n:.0f}%  (50% = no info; small n)")
    else:
        print("  no game drifted more than the noise floor -- price is flat pre-game.")

    # ---- Test B: OI-jump -> price continuation ------------------------------
    print(f"\n[B] OI-jump events (dOI>={PCT*100:.0f}% or >={ABS_OI:.0f} sh): does price move & continue?")
    cont = rev = flat = moved0 = jumps = 0
    for s, tks in sorted(pre.items()):
        if len(pks[s]) > 1: continue
        tks.sort(key=lambda r: r["poll_ts"])
        seq = [t for t in tks if t.get("oi") and t.get("currentPx") is not None]
        for i in range(1, len(seq)):
            doi = seq[i]["oi"] - seq[i-1]["oi"]
            base = seq[i-1]["oi"]
            if base and (doi/base >= PCT or doi >= ABS_OI):
                jumps += 1
                dpx = seq[i]["currentPx"] - seq[i-1]["currentPx"]   # concurrent move
                if abs(dpx) >= MOVE: moved0 += 1
                # forward move over HORIZON
                t0 = ts(seq[i]["poll_ts"]); px0 = seq[i]["currentPx"]
                fut = None
                for j in range(i+1, len(seq)):
                    if ts(seq[j]["poll_ts"]) - t0 >= timedelta(minutes=HORIZON):
                        fut = seq[j]["currentPx"]; break
                if fut is None: continue
                fdpx = fut - px0
                if abs(fdpx) < MOVE: flat += 1
                elif (fdpx > 0) == (dpx > 0) and abs(dpx) >= MOVE: cont += 1
                elif abs(dpx) >= MOVE: rev += 1
                else: flat += 1
    print(f"  OI-jump events: {jumps}")
    print(f"  ... that MOVED the price >={MOVE*100:.1f}c concurrently: {moved0}/{jumps} "
          f"({100*moved0/jumps:.0f}%)" if jumps else "  (no jumps)")
    print(f"  ... after a moving jump, next {HORIZON:.0f}min: continue={cont} reverse={rev} flat={flat}")

    print("\nVERDICT guide: EDGE needs OI jumps to MOVE price AND continue (or drift to "
          "predict winners > ~60%). If jumps leave price flat / drift ~50% -> efficient, "
          "big money is absorbed by the deep book -> NO tradeable order-flow edge.")

if __name__ == "__main__":
    main()
