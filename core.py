"""
core.py — pure decision logic for THE SHARP, extracted so it is unit-testable
without importing discord / polymarket_us / websockets.

Rules for this module:
  * stdlib ONLY (re, math, datetime). No third-party imports, no network, no I/O.
  * pure functions: inputs in, decision out, no globals, no side effects.
  * this is the SEAM. bot.py will import from here so there is one source of
    truth for the entry/exit/settlement decisions the whole system turns on.

IMPORTANT (harness-first): as first landed, these functions MIRROR bot.py's
CURRENT behavior byte-for-byte — including known bug #3 (trail-stop starved by a
low take-profit). The test suite pins that current behavior (characterization)
and separately asserts the DESIRED behavior via expectedFailure, so the fix has
a red test to turn green. Do not "fix" logic here without flipping the matching
test; the whole point is that behavior changes are visible.
"""
import re
import math
from datetime import datetime

# bot.py hardcodes the trail-arm gate as `hw > e + 0.03`. Named here so the
# eventual fix (Step 4) can move it without hunting a magic number.
TRAIL_ARM = 0.03

_FUTURE_KEYWORDS = ["champion", "mvp", "trophy", "winner", "award", "champ",
                    "pennant", "rookie", "cy-young", "allstar", "series-price"]

_POS_SIZE_KEYS = ("netPosition", "netPositionDecimal", "qtyAvailable", "size",
                  "quantity", "shares", "netQuantity", "position", "amount")


# ─── slug / date helpers ─────────────────────────────────────────────────────
def slug_date_str(slug):
    """Extract YYYY-MM-DD substring from a slug, or '' if none found."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", slug or "")
    return m.group(1) if m else ""


def is_future_market(slug, title=""):
    """True if slug/title looks like a futures/props market (champion, MVP...)."""
    blob = (str(slug) + " " + str(title)).lower()
    return any(k in blob for k in _FUTURE_KEYWORDS)


def is_today_slug(slug, today, back=1, fwd=0):
    """Configurable today-only check. `today` is a datetime.date (injected — the
    caller decides the timezone). Accepts slug dates in [-back, +fwd] days of it.
    Fails closed (False) if no valid date can be parsed from the slug."""
    sd = slug_date_str(slug)
    if not sd:
        return False
    try:
        slug_date = datetime.strptime(sd, "%Y-%m-%d").date()
    except ValueError:
        return False
    delta = (slug_date - today).days
    return -back <= delta <= fwd


# ─── sizing / edge ───────────────────────────────────────────────────────────
def safe_quantity(price, max_dollars):
    """Largest integer share count whose cost stays <= max_dollars. 0 if the
    price is out of (0,1] or a single share already exceeds the budget."""
    if price <= 0 or price > 1:
        return 0
    q = math.floor(max_dollars / price)
    while q > 0 and q * price > max_dollars:
        q -= 1
    return max(0, q)


def edge_ok(price, edge, min_entry, max_entry, min_edge):
    """Entry gate: price inside the band AND edge at/above the minimum."""
    if price < min_entry or price > max_entry:
        return False
    return edge >= min_edge


# ─── entry (drop) decision ladder ────────────────────────────────────────────
def evaluate_drop_decision(*, drop, price, threshold, max_drop, in_cooldown,
                           quality_ok, min_entry, max_entry, has_open,
                           daily_limit_hit, revert_pct, min_edge):
    """Pure form of the FIRE ladder in on_market_tick. Returns one of:
       None        -> drop below threshold, not a threshold-crossing decision
       "FIRE"      -> all gates passed, would open a trade
       a reason str-> a blocked threshold-crossing ("cooldown", "band", ...)
    Mirrors bot.py's if/elif order exactly so the reason precedence is identical.
    Side-effecting facts (cooldown, open-position, daily-limit, market quality)
    are passed in as booleans by the caller — this function stays pure."""
    if drop < threshold:
        return None
    if drop > max_drop:
        return "drop too large"
    if in_cooldown:
        return "cooldown"
    if not quality_ok:
        return "dead market"
    if price < min_entry or price > max_entry:
        return "band"
    if has_open:
        return "open trade"
    if daily_limit_hit:
        return "daily limit"
    revert = price + drop * revert_pct
    edge = revert - price
    if edge < min_edge:
        return "edge"
    return "FIRE"


# ─── exit decision matrix ────────────────────────────────────────────────────
def evaluate_exit(entry, high_water, current, take_profit, stop_loss,
                  trail_stop, trail_arm=TRAIL_ARM):
    """Decide whether an open position should exit on this tick.

    Returns (reason, new_high_water) where reason is one of
    "take-profit" / "stop-loss" / "trail-stop" / None.

    MIRRORS bot.py check_exits() exactly, including the if/elif precedence that
    lets take-profit preempt the trail (bug #3). Do not reorder without a test.

      g = current - entry
      1. g >= take_profit            -> take-profit
      2. g <= -stop_loss             -> stop-loss
      3. hw > entry+trail_arm and current <= hw - trail_stop -> trail-stop
    """
    hw = entry if high_water is None else high_water
    if current > hw:
        hw = current
    g = current - entry
    reason = None
    if g >= take_profit:
        reason = "take-profit"
    elif g <= -stop_loss:
        reason = "stop-loss"
    elif hw > entry + trail_arm and current <= hw - trail_stop:
        reason = "trail-stop"
    return reason, hw


def run_exit_path(entry, prices, take_profit, stop_loss, trail_stop,
                  trail_arm=TRAIL_ARM):
    """Feed a sequence of tick prices through evaluate_exit, carrying high-water
    forward, and return (reason, exit_price) at the FIRST tick that exits, or
    (None, None) if the path never triggers an exit. This is how a position
    actually experiences the market tick-by-tick — the thing the trail bug hides
    from a single-point check."""
    hw = entry
    for px in prices:
        reason, hw = evaluate_exit(entry, hw, px, take_profit, stop_loss,
                                   trail_stop, trail_arm)
        if reason:
            return reason, px
    return None, None


# ─── settlement / activity parsing ───────────────────────────────────────────
def classify_settlement(pnl, eps=0.005):
    """won / lost / scratch from a realized pnl. NOTE: bot.py currently folds
    'scratch' (|pnl|<eps) into losses; this classifier keeps it distinct so the
    fix (bug R12) has a clean primitive."""
    if pnl > eps:
        return "won"
    if pnl < -eps:
        return "lost"
    return "scratch"


def parse_position_resolution(activity):
    """(slug, realized_pnl) for an ACTIVITY_TYPE_POSITION_RESOLUTION record, else
    None. Mirrors resolve_loop's nested extraction; realized value is a STRING."""
    if activity.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
        return None
    pr = activity.get("positionResolution") or {}
    slug = pr.get("marketSlug")
    if not slug:
        return None
    raw = ((pr.get("afterPosition") or {}).get("realized") or {}).get("value")
    try:
        pnl = float(raw)
    except (TypeError, ValueError):
        pnl = 0.0
    return slug, pnl


def parse_trade_close(activity, eps=0.005):
    """(slug, realized_pnl) for a closing ACTIVITY_TYPE_TRADE (non-null, non-zero
    realizedPnl distinguishes a close from an opening buy), else None. Do NOT key
    on trade.side or state — both are unreliable on closing fills."""
    if activity.get("type") != "ACTIVITY_TYPE_TRADE":
        return None
    tr = activity.get("trade") or {}
    slug = tr.get("marketSlug")
    if not slug:
        return None
    raw = (tr.get("realizedPnl") or {}).get("value")
    try:
        pnl = float(raw)
    except (TypeError, ValueError):
        pnl = 0.0
    if abs(pnl) < eps:
        return None
    return slug, pnl


# ─── reconcile: position size extraction ─────────────────────────────────────
def pos_size(p):
    """Best-effort size from an unknown-shape position dict; 0.0 if none of the
    known keys yield a nonzero float. MIRRORS bot.py _pos_size (bug R3: returns
    0.0 == 'flat' for a shape whose size lives under an unknown key)."""
    for k in _POS_SIZE_KEYS:
        if k in p:
            try:
                f = float(p[k])
                if f != 0:
                    return f
            except (TypeError, ValueError):
                pass
    return 0.0


def pos_size_safe(p):
    """Fail-closed variant (the R3 fix target): returns
       (size, known)  where known is False if the dict is non-empty but no known
    size key was found — the caller must treat known=False as UNKNOWN/unsafe,
    never as flat. Not yet wired into bot.py; the test asserts the property so we
    can adopt it deliberately."""
    for k in _POS_SIZE_KEYS:
        if k in p:
            try:
                return float(p[k]), True
            except (TypeError, ValueError):
                pass
    # non-empty payload but no recognizable size field -> unknown, not flat
    if p and not any(k in p for k in _POS_SIZE_KEYS):
        return 0.0, False
    return 0.0, True


# ─── tennis win-probability model (the "fair value" brain) ───────────────────
# Point -> game -> set -> match, all derived from ONE input per player: their
# probability of winning a single point ON SERVE. Tennis is unusually modelable
# because a small per-point serve edge compounds into a large game/set/match
# edge, and the current score fully determines the situation. This is the
# standard hierarchical model (cf. O'Malley 2008 / Barnett & Clarke). Pure and
# stdlib-only like the rest of core; the live score that drives it will come
# from the tennis data feed. Built bottom-up: game level first (this commit),
# set and match to follow.

def _deuce_win(p):
    """Probability the server eventually wins from deuce, given per-point win
    probability p. Closed form for the infinite deuce/advantage sequence."""
    q = 1.0 - p
    denom = p * p + q * q
    if denom == 0:
        return 0.0
    return p * p / denom


def game_win_prob(p, a=0, b=0):
    """Probability the SERVER wins the current game.

    p = server's probability of winning a single point.
    a = points the server has (0,1,2,3 = 0/15/30/40, higher = advantage).
    b = points the returner has.
    Deuce (both >=3, i.e. 40-40) is resolved in closed form; other scores
    recurse one point forward. Returns a probability in [0, 1].
    """
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    # terminal: someone reached >=4 points with a >=2 margin
    if a >= 4 and a - b >= 2:
        return 1.0
    if b >= 4 and b - a >= 2:
        return 0.0
    # deuce / advantage region (both players at 40 or beyond)
    if a >= 3 and b >= 3:
        d = _deuce_win(p)
        if a == b:
            return d                 # deuce
        if a > b:
            return p + (1.0 - p) * d  # advantage server
        return p * d                  # advantage returner
    # otherwise play one more point and recurse
    return p * game_win_prob(p, a + 1, b) + (1.0 - p) * game_win_prob(p, a, b + 1)


def hold_prob(p):
    """Probability of holding serve from love-all (game_win_prob at 0-0)."""
    return game_win_prob(p, 0, 0)
