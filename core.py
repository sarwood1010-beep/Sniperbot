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
import unicodedata
from datetime import datetime
from functools import lru_cache

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


@lru_cache(maxsize=200000)
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


def _tiebreak_first_serves(i):
    """In a tiebreak, does the player who served the FIRST point serve point i?
    Serve order is X, Y,Y, X,X, Y,Y, X,X, ... (0-indexed point i)."""
    if i == 0:
        return True
    return ((i - 1) // 2) % 2 == 1


@lru_cache(maxsize=200000)
def tiebreak_win_prob(pa, pb, a=0, b=0, first_is_A=True):
    """Probability player A wins a 7-point tiebreak from score a-b.
    pa/pb = each player's point-win-on-serve probability. first_is_A = A served
    the first point of the tiebreak. Serve rotation is modeled exactly; the
    >=6-6 deuce is collapsed with a 2-point-block closed form (each player serves
    one of the next two points) to keep recursion shallow."""
    if a >= 7 and a - b >= 2:
        return 1.0
    if b >= 7 and b - a >= 2:
        return 0.0
    if a >= 6 and b >= 6 and a == b:              # tiebreak deuce
        num = pa * (1 - pb)
        den = num + (1 - pa) * pb
        return num / den if den > 0 else 0.5
    i = a + b
    a_serves = (_tiebreak_first_serves(i) == first_is_A)
    win_pt = pa if a_serves else (1.0 - pb)       # prob A wins THIS point
    return (win_pt * tiebreak_win_prob(pa, pb, a + 1, b, first_is_A)
            + (1 - win_pt) * tiebreak_win_prob(pa, pb, a, b + 1, first_is_A))


@lru_cache(maxsize=200000)
def set_win_prob(pa, pb, a=0, b=0, a_serves=True):
    """Probability A wins the set from games score a-b, where a_serves = A serves
    the NEXT (fresh) game. First to 6 games, win by 2, tiebreak at 6-6. Partial
    games in progress are handled at the match level, not here."""
    if a >= 6 and a - b >= 2:
        return 1.0
    if b >= 6 and b - a >= 2:
        return 0.0
    if a == 6 and b == 6:
        return tiebreak_win_prob(pa, pb, 0, 0, first_is_A=a_serves)
    if a_serves:
        pA_game = game_win_prob(pa)               # A holds serve
    else:
        pA_game = 1.0 - game_win_prob(pb)         # A breaks (B fails to hold)
    return (pA_game * set_win_prob(pa, pb, a + 1, b, not a_serves)
            + (1 - pA_game) * set_win_prob(pa, pb, a, b + 1, not a_serves))


def match_win_prob_from_sets(p_set, sets_a=0, sets_b=0, sets_to_win=2):
    """Probability A wins the match given P(A wins one set)=p_set and the current
    sets score. sets_to_win = 2 for best-of-3 (WTA/ATP non-slam), 3 for
    best-of-5. Treats sets as independent — a standard simplification."""
    if sets_a >= sets_to_win:
        return 1.0
    if sets_b >= sets_to_win:
        return 0.0
    return (p_set * match_win_prob_from_sets(p_set, sets_a + 1, sets_b, sets_to_win)
            + (1 - p_set) * match_win_prob_from_sets(p_set, sets_a, sets_b + 1, sets_to_win))


# ─── live state: parse the feed and produce a live "true price" ──────────────
# Maps the tennis-data-feed fields onto the model. Feed shape (confirmed live):
#   score     "6-4,3-6,0-0"   completed sets then current-set games (last entry)
#   points    "40-A"          current game; 0/15/30/40, A=advantage, 40-40=deuce
#   indicator "1,0"           one-hot server flag; '1' first => player 1 serving
_PTS = {"0": 0, "15": 1, "30": 2, "40": 3, "A": 4, "AD": 4}


def parse_live_score(score, points, indicator):
    """Parse the feed's live fields into a structured state dict, or None if the
    strings don't parse (caller should log loudly rather than trade on garbage).
    Returns: sets_p1, sets_p2, games_p1, games_p2, pts_p1, pts_p2 (0..4, 4=adv),
    server (1 or 2), in_tiebreak (bool)."""
    try:
        sets = [s for s in str(score).split(",") if s.strip() != ""]
        if not sets:
            return None
        completed, current = sets[:-1], sets[-1]
        sp1 = sp2 = 0
        for s in completed:
            a, b = s.split("-")
            a, b = int(a), int(b)
            if a > b:
                sp1 += 1
            elif b > a:
                sp2 += 1
        ga, gb = current.split("-")
        ga, gb = int(ga), int(gb)
        in_tb = (ga == 6 and gb == 6)
        pa_t, pb_t = str(points).strip().split("-")
        if in_tb:
            pa, pb = int(pa_t), int(pb_t)          # tiebreak points are integers
        else:
            pa = _PTS[pa_t.strip().upper()]
            pb = _PTS[pb_t.strip().upper()]
        ind = str(indicator).split(",")
        server = 1 if ind[0].strip() == "1" else 2
        return {"sets_p1": sp1, "sets_p2": sp2, "games_p1": ga, "games_p2": gb,
                "pts_p1": pa, "pts_p2": pb, "server": server, "in_tiebreak": in_tb}
    except (ValueError, KeyError, IndexError, AttributeError):
        return None


def _match_after_set(p1s, p2s, st, p1_wins_current_set, sets_to_win):
    """Combine the (exact-ish) current-set win prob with future sets (iid
    approximation) into a match win probability for player 1."""
    s1, s2 = st["sets_p1"], st["sets_p2"]
    p_set_future = set_win_prob(p1s, p2s, 0, 0, a_serves=True)
    win_if = match_win_prob_from_sets(p_set_future, s1 + 1, s2, sets_to_win)
    lose_if = match_win_prob_from_sets(p_set_future, s1, s2 + 1, sets_to_win)
    return p1_wins_current_set * win_if + (1 - p1_wins_current_set) * lose_if


def live_match_win_prob(p1_serve, p2_serve, st, sets_to_win=2):
    """Probability PLAYER 1 wins the match, given each player's point-win-on-serve
    probability and the parsed live state `st`. Composes: current game/tiebreak ->
    current set -> match. This is the model's live "fair value" for the market."""
    if st is None:
        return None
    g1, g2, server = st["games_p1"], st["games_p2"], st["server"]
    if st["in_tiebreak"]:
        p1_set = tiebreak_win_prob(p1_serve, p2_serve, st["pts_p1"], st["pts_p2"],
                                   first_is_A=(server == 1))
        return _match_after_set(p1_serve, p2_serve, st, p1_set, sets_to_win)
    # normal game in progress: P(player 1 wins the current game)
    if server == 1:
        p1_game = game_win_prob(p1_serve, st["pts_p1"], st["pts_p2"])
    else:
        p1_game = 1.0 - game_win_prob(p2_serve, st["pts_p2"], st["pts_p1"])
    # serve alternates to the other player for the next game
    next1 = (server == 2)
    set_if_p1 = set_win_prob(p1_serve, p2_serve, g1 + 1, g2, a_serves=next1)
    set_if_p2 = set_win_prob(p1_serve, p2_serve, g1, g2 + 1, a_serves=next1)
    p1_set = p1_game * set_if_p1 + (1 - p1_game) * set_if_p2
    return _match_after_set(p1_serve, p2_serve, st, p1_set, sets_to_win)


# ─── serve priors: anchor the model to the match's opening price ──────────────
# We don't need an independent serve-stats source to start. Instead we fix the
# tour-average serve level and solve for the serve GAP between the two players so
# that the model's PRE-MATCH probability equals the market's opening price. Then
# the model tracks the live score; any in-play divergence from the market is the
# edge we're testing for. Approx tour-average share of service points won:
TOUR_BASE_SERVE = {"atp": 0.64, "wta": 0.56}

_START_STATE = {"sets_p1": 0, "sets_p2": 0, "games_p1": 0, "games_p2": 0,
                "pts_p1": 0, "pts_p2": 0, "server": 1, "in_tiebreak": False}


def implied_serve_priors(target_p1_matchup_prob, tour="atp", sets_to_win=2):
    """Return (p1_serve, p2_serve) such that the model's pre-match win prob for
    player 1 equals target_p1_matchup_prob. Fixes the average serve level at the
    tour baseline and solves for the gap by bisection (live_match_win_prob is
    monotonic in the gap)."""
    base = TOUR_BASE_SERVE.get(tour, 0.63)
    target = max(0.001, min(0.999, target_p1_matchup_prob))
    span = min(base - 0.02, 0.98 - base)      # keep both serves in (0.02, 0.98)
    lo, hi = -span, span
    for _ in range(40):
        d = (lo + hi) / 2.0
        p = live_match_win_prob(base + d, base - d, _START_STATE, sets_to_win)
        if p < target:
            lo = d
        else:
            hi = d
    d = (lo + hi) / 2.0
    return base + d, base - d


# ─── harsh paper-fill simulator (honest execution) ───────────────────────────
# Naive paper trading fills at a free mid price and looks profitable that never
# survives real money. This models the WORST plausible fill so a proven edge has
# already paid the costs that kill fake edges: BUY pays the ask, SELL receives
# the bid (the spread), plus slippage against us and a fee, and a fill-or-kill
# order that would need a worse-than-limit price simply MISSES (no fill).
def simulate_fill(side, best_bid, best_ask, slippage=0.0, fee=0.0, limit_price=None):
    """Return (filled: bool, fill_price: float|None). fee is a fraction of price
    (e.g. 0.01 = 1%); slippage is an absolute price moved against us. Missing book
    (bid/ask None) => no fill, conservatively (never invent a price)."""
    if best_bid is None or best_ask is None:
        return False, None
    s = str(side).lower()
    if s == "buy":
        price = best_ask * (1.0 + fee) + slippage        # pay the ask, worse
        if limit_price is not None and price > limit_price:
            return False, None                            # FOK: would need worse than limit
        return True, price
    if s == "sell":
        price = best_bid * (1.0 - fee) - slippage         # receive the bid, worse
        if limit_price is not None and price < limit_price:
            return False, None
        return True, price
    return False, None


def round_trip_cost(best_bid, best_ask, slippage=0.0, fee=0.0):
    """Total per-share cost of entering AND exiting at these quotes under the
    harsh model: the spread + two fees + two slippages. This is the hurdle an
    edge must clear to be real. Returns None if the book is missing."""
    buy_ok, buy_px = simulate_fill("buy", best_bid, best_ask, slippage, fee)
    sell_ok, sell_px = simulate_fill("sell", best_bid, best_ask, slippage, fee)
    if not (buy_ok and sell_ok):
        return None
    return buy_px - sell_px


# ─── edge decision: model fair value vs market price, after costs ────────────
def edge_signal(fair_prob, best_bid, best_ask, min_edge, slippage=0.0, fee=0.0):
    """Decide whether BUYING this side is worth it. In a binary market a winning
    share pays $1, so the fair value of a share == fair_prob (the model's win
    probability for this side). We fire only if fair value exceeds the harsh BUY
    price (ask + fee + slippage) by at least min_edge. Returns a dict:
      fire (bool), edge (fair_prob - buy_price), buy_price, reason.
    Pure — the caller supplies fair_prob from the model and bid/ask from the book,
    and applies this to each side of the match."""
    ok, buy_px = simulate_fill("buy", best_bid, best_ask, slippage, fee)
    if not ok or buy_px is None:
        return {"fire": False, "edge": None, "buy_price": None, "reason": "no_fill"}
    edge = fair_prob - buy_px
    if not (0.0 < buy_px < 1.0):
        return {"fire": False, "edge": edge, "buy_price": buy_px,
                "reason": "price_out_of_range"}
    fire = edge >= min_edge
    return {"fire": fire, "edge": edge, "buy_price": buy_px,
            "reason": "edge" if fire else "insufficient_edge"}


# ─── player-name helpers (feed <-> Polymarket matching) ──────────────────────
def normalize_name(name):
    """Lowercase, strip accents and punctuation, collapse whitespace — so
    'Stéfanos Tsitsipás' and 'Stefanos Tsitsipas' compare equal."""
    n = unicodedata.normalize("NFKD", str(name))
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z0-9 ]", " ", n.lower())
    return re.sub(r"\s+", " ", n).strip()


def name_tokens(name):
    """Set of normalized word tokens in a name."""
    return set(t for t in normalize_name(name).split() if t)


def last_name(name):
    """Best-guess surname: the last normalized token (empty string if none)."""
    toks = normalize_name(name).split()
    return toks[-1] if toks else ""


def both_players_present(feed_p1, feed_p2, market_text):
    """True if both players' surnames appear as tokens in market_text (a
    Polymarket title / side descriptions, order-independent). A first-pass
    matcher; refined once we see the real market format."""
    toks = name_tokens(market_text)
    ln1, ln2 = last_name(feed_p1), last_name(feed_p2)
    return bool(ln1) and bool(ln2) and ln1 in toks and ln2 in toks
