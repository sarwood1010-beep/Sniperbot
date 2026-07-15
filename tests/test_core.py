"""
Characterization + spec tests for core.py.

Two kinds of test live here:
  * Characterization tests pin bot.py's CURRENT behavior so the extraction into
    core.py (and the later rewire of bot.py to import it) is provably
    behavior-preserving.
  * expectedFailure ("BUG-N") tests encode DESIRED behavior that the current
    code does NOT satisfy. They are red-on-purpose and become the fix targets;
    when the fix lands, remove the decorator and they lock the fix in place.

Runs under stdlib unittest (no pip) and under pytest (CI).
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core  # noqa: E402


# Production-ish config mirrored from bot.py defaults, for realistic scenarios.
PROD = dict(take_profit=0.08, stop_loss=0.08, trail_stop=0.04)


class SlugAndDate(unittest.TestCase):
    def test_slug_date_str_extracts(self):
        self.assertEqual(core.slug_date_str("aec-atp-a-b-2026-07-14"), "2026-07-14")

    def test_slug_date_str_none(self):
        self.assertEqual(core.slug_date_str("aec-atp-no-date"), "")
        self.assertEqual(core.slug_date_str(None), "")

    def test_future_market_keywords(self):
        self.assertTrue(core.is_future_market("aec-mlb-al-champion-2026"))
        self.assertTrue(core.is_future_market("x", "AL MVP Award"))
        self.assertFalse(core.is_future_market("aec-atp-a-b-2026-07-14", "A vs B"))

    def test_today_and_yesterday_accepted_default_window(self):
        today = date(2026, 7, 14)
        self.assertTrue(core.is_today_slug("aec-atp-a-b-2026-07-14", today))   # today
        self.assertTrue(core.is_today_slug("aec-atp-a-b-2026-07-13", today))   # yesterday, back=1

    def test_tomorrow_rejected_with_default_forward_zero(self):
        # bug #4 shape: a game whose slug is dated one UTC-day forward is filtered
        # out entirely when date_window_forward_days == 0.
        today = date(2026, 7, 14)
        self.assertFalse(core.is_today_slug("aec-atp-a-b-2026-07-15", today))

    def test_tomorrow_accepted_when_forward_one(self):
        today = date(2026, 7, 14)
        self.assertTrue(core.is_today_slug("aec-atp-a-b-2026-07-15", today, back=1, fwd=1))

    def test_no_date_fails_closed(self):
        self.assertFalse(core.is_today_slug("aec-atp-no-date", date(2026, 7, 14)))

    def test_bad_date_fails_closed(self):
        self.assertFalse(core.is_today_slug("aec-atp-2026-13-99", date(2026, 7, 14)))


class Sizing(unittest.TestCase):
    def test_safe_quantity_basic(self):
        self.assertEqual(core.safe_quantity(0.50, 2.0), 4)

    def test_safe_quantity_zero_shares_small_bet_high_price(self):
        # R6: a $0.50 bet at price 0.80 floors to ZERO shares -> silent no-fill.
        self.assertEqual(core.safe_quantity(0.80, 0.50), 0)

    def test_safe_quantity_out_of_range(self):
        self.assertEqual(core.safe_quantity(0.0, 2.0), 0)
        self.assertEqual(core.safe_quantity(1.5, 2.0), 0)

    def test_edge_ok(self):
        self.assertTrue(core.edge_ok(0.50, 0.05, 0.20, 0.80, 0.04))
        self.assertFalse(core.edge_ok(0.90, 0.05, 0.20, 0.80, 0.04))  # out of band
        self.assertFalse(core.edge_ok(0.50, 0.02, 0.20, 0.80, 0.04))  # edge too small


class DropDecision(unittest.TestCase):
    BASE = dict(price=0.50, threshold=0.08, max_drop=0.20, in_cooldown=False,
                quality_ok=True, min_entry=0.20, max_entry=0.80, has_open=False,
                daily_limit_hit=False, revert_pct=0.50, min_edge=0.04)

    def _d(self, **over):
        args = dict(self.BASE); args.update(over)
        return core.evaluate_drop_decision(**args)

    def test_below_threshold_is_none(self):
        self.assertIsNone(self._d(drop=0.05))

    def test_fire(self):
        self.assertEqual(self._d(drop=0.10), "FIRE")

    def test_precedence_drop_too_large(self):
        self.assertEqual(self._d(drop=0.25), "drop too large")

    def test_precedence_cooldown_before_band(self):
        self.assertEqual(self._d(drop=0.10, in_cooldown=True, price=0.90), "cooldown")

    def test_dead_market(self):
        self.assertEqual(self._d(drop=0.10, quality_ok=False), "dead market")

    def test_band(self):
        self.assertEqual(self._d(drop=0.10, price=0.90), "band")

    def test_open_trade(self):
        self.assertEqual(self._d(drop=0.10, has_open=True), "open trade")

    def test_daily_limit(self):
        self.assertEqual(self._d(drop=0.10, daily_limit_hit=True), "daily limit")

    def test_edge_too_small(self):
        # drop 0.08 * revert 0.5 = 0.04 edge == min_edge -> FIRE; just under -> edge
        self.assertEqual(self._d(drop=0.08, min_edge=0.05), "edge")


class ExitMatrix(unittest.TestCase):
    ENTRY = 0.50
    # NOTE (bug R17): bot.py compares raw floats at exact thresholds
    # (g >= take_profit), and e.g. 0.58 - 0.50 == 0.0799...96 < 0.08, so an exit
    # placed exactly ON the threshold fires one tick LATE. core.py mirrors that,
    # so these tests deliberately use prices comfortably past the boundary. The
    # R17 fix (round to cents / epsilon) will get its own boundary test.

    def test_take_profit(self):
        reason, px = core.run_exit_path(self.ENTRY, [0.54, 0.59], **PROD)
        self.assertEqual(reason, "take-profit")
        self.assertAlmostEqual(px, 0.59)   # g = +0.09, clearly past TP

    def test_stop_loss(self):
        reason, px = core.run_exit_path(self.ENTRY, [0.48, 0.44, 0.41], **PROD)
        self.assertEqual(reason, "stop-loss")
        self.assertAlmostEqual(px, 0.41)   # g = -0.09, clearly past SL

    def test_no_exit_when_flat(self):
        reason, px = core.run_exit_path(self.ENTRY, [0.51, 0.52, 0.505], **PROD)
        self.assertIsNone(reason)

    def test_trail_mechanism_fires_when_tp_does_not_preempt(self):
        # The trail MECHANISM is sound: with a TP high enough not to preempt, a
        # peak-then-giveback path exits via trail. (Proves the bug is policy, not
        # a broken mechanism.)
        reason, px = core.run_exit_path(
            self.ENTRY, [0.55, 0.60, 0.55],
            take_profit=0.20, stop_loss=0.20, trail_stop=0.04)
        self.assertEqual(reason, "trail-stop")
        self.assertAlmostEqual(px, 0.55)   # gave back 0.05 from the 0.60 peak

    def test_trail_starved_by_production_take_profit(self):
        # CHARACTERIZATION of bug #3: under production TP=0.08, a genuine runner
        # that peaks at 0.65 (+0.15) is force-closed at take-profit on the way UP
        # (here at 0.61) long before the trail can engage. The trail never sees
        # the peak or the giveback.
        reason, px = core.run_exit_path(
            self.ENTRY, [0.55, 0.61, 0.65, 0.60], **PROD)
        self.assertEqual(reason, "take-profit")
        self.assertAlmostEqual(px, 0.61)   # exits climbing, never reaches 0.65

    @unittest.expectedFailure
    def test_BUG3_trail_should_protect_a_real_runner(self):
        # DESIRED behavior (Step 4): a mean-reversion winner that runs to +0.15
        # and then gives back the trail amount should exit via trail-stop with a
        # locked gain LARGER than the flat take-profit — that is the asymmetry
        # the trail is supposed to create. Currently it exits "take-profit" while
        # still climbing, so this assertion fails -> expected failure until the
        # exit policy is fixed. When fixed, remove the decorator.
        reason, px = core.run_exit_path(
            self.ENTRY, [0.55, 0.61, 0.65, 0.60], **PROD)
        self.assertEqual(reason, "trail-stop")
        self.assertGreater(px, self.ENTRY + PROD["take_profit"])


class Settlement(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(core.classify_settlement(0.5), "won")
        self.assertEqual(core.classify_settlement(-0.5), "lost")
        self.assertEqual(core.classify_settlement(0.0), "scratch")
        self.assertEqual(core.classify_settlement(0.001), "scratch")  # within eps

    def test_position_resolution_parse(self):
        act = {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
               "positionResolution": {"marketSlug": "aec-atp-a-b-2026-07-14",
                                       "afterPosition": {"realized": {"value": "1.2500"}}}}
        self.assertEqual(core.parse_position_resolution(act),
                         ("aec-atp-a-b-2026-07-14", 1.25))

    def test_position_resolution_wrong_type(self):
        self.assertIsNone(core.parse_position_resolution({"type": "ACTIVITY_TYPE_TRADE"}))

    def test_position_resolution_bad_value_defaults_zero(self):
        act = {"type": "ACTIVITY_TYPE_POSITION_RESOLUTION",
               "positionResolution": {"marketSlug": "s",
                                       "afterPosition": {"realized": {"value": None}}}}
        self.assertEqual(core.parse_position_resolution(act), ("s", 0.0))

    def test_trade_close_parse(self):
        act = {"type": "ACTIVITY_TYPE_TRADE",
               "trade": {"marketSlug": "s", "realizedPnl": {"value": "-0.30"}}}
        self.assertEqual(core.parse_trade_close(act), ("s", -0.30))

    def test_trade_close_ignores_opening_buy(self):
        # An opening buy reports zero realizedPnl -> not a close.
        act = {"type": "ACTIVITY_TYPE_TRADE",
               "trade": {"marketSlug": "s", "realizedPnl": {"value": "0"}}}
        self.assertIsNone(core.parse_trade_close(act))


class ReconcilePosSize(unittest.TestCase):
    def test_pos_size_known_key(self):
        self.assertEqual(core.pos_size({"netPosition": "3"}), 3.0)

    def test_pos_size_unknown_shape_returns_zero_BUG_R3(self):
        # CHARACTERIZATION of R3: a real position under an unrecognized key reads
        # as 0.0 == "flat", silently disarming the reconcile safety net.
        self.assertEqual(core.pos_size({"weirdSizeField": "5"}), 0.0)

    def test_pos_size_empty(self):
        self.assertEqual(core.pos_size({}), 0.0)

    def test_pos_size_safe_flags_unknown_shape(self):
        # The R3 fix target: unknown shape -> known=False (treat as unsafe).
        size, known = core.pos_size_safe({"weirdSizeField": "5"})
        self.assertFalse(known)
        # a recognized key -> known=True
        size, known = core.pos_size_safe({"size": "5"})
        self.assertTrue(known)
        self.assertEqual(size, 5.0)


class TennisModelGameLevel(unittest.TestCase):
    def test_symmetric_point_gives_even_game(self):
        self.assertAlmostEqual(core.game_win_prob(0.5), 0.5, places=6)

    def test_deuce_symmetric(self):
        self.assertAlmostEqual(core.game_win_prob(0.5, 3, 3), 0.5, places=6)

    def test_known_hold_percentages(self):
        # Standard reference values: a server winning 60% of points holds ~73.6%
        # of games; at 70% it is ~90.1%. These pin the model to known results.
        self.assertAlmostEqual(core.hold_prob(0.60), 0.7357, places=3)
        self.assertAlmostEqual(core.hold_prob(0.70), 0.9008, places=3)

    def test_monotonic_in_serve_strength(self):
        self.assertLess(core.hold_prob(0.55), core.hold_prob(0.65))

    def test_extremes(self):
        self.assertEqual(core.game_win_prob(0.0), 0.0)
        self.assertEqual(core.game_win_prob(1.0), 1.0)

    def test_advantage_ordering(self):
        # advantage-server > deuce > advantage-returner
        p = 0.6
        self.assertGreater(core.game_win_prob(p, 4, 3), core.game_win_prob(p, 3, 3))
        self.assertGreater(core.game_win_prob(p, 3, 3), core.game_win_prob(p, 3, 4))

    def test_being_ahead_helps(self):
        # 40-15 (3,1) is a stronger position than 15-40 (1,3)
        self.assertGreater(core.game_win_prob(0.6, 3, 1), core.game_win_prob(0.6, 1, 3))


class TennisModelSetMatch(unittest.TestCase):
    def test_tiebreak_symmetric_near_even(self):
        self.assertAlmostEqual(core.tiebreak_win_prob(0.6, 0.6), 0.5, places=2)

    def test_tiebreak_stronger_player_favored(self):
        self.assertGreater(core.tiebreak_win_prob(0.70, 0.55), 0.5)

    def test_set_symmetric_near_even(self):
        # equal players -> set is close to a coin flip (tiny serve-first edge)
        self.assertAlmostEqual(core.set_win_prob(0.6, 0.6), 0.5, places=1)

    def test_set_stronger_server_favored(self):
        self.assertGreater(core.set_win_prob(0.70, 0.55), 0.6)

    def test_set_lead_is_strong(self):
        self.assertGreater(core.set_win_prob(0.6, 0.6, 5, 0), 0.95)   # 5-0 up
        self.assertLess(core.set_win_prob(0.6, 0.6, 0, 5), 0.05)      # 0-5 down

    def test_match_from_sets_exact_values(self):
        # p_set=0.5 -> 0.5; best-of-3 closed form p^2(3-2p): p=0.6 -> 0.648
        self.assertAlmostEqual(core.match_win_prob_from_sets(0.5), 0.5, places=6)
        self.assertAlmostEqual(core.match_win_prob_from_sets(0.6), 0.648, places=6)

    def test_match_from_sets_one_set_up(self):
        # A needs 1 set, B needs 2: p + (1-p)*p = p(2-p); p=0.6 -> 0.84
        self.assertAlmostEqual(core.match_win_prob_from_sets(0.6, 1, 0), 0.84, places=6)

    def test_match_best_of_five_needs_three(self):
        self.assertEqual(core.match_win_prob_from_sets(0.6, 2, 0, sets_to_win=3) < 1.0, True)
        self.assertEqual(core.match_win_prob_from_sets(0.6, 3, 0, sets_to_win=3), 1.0)


class LiveStateParsing(unittest.TestCase):
    # fixtures captured from the real live feed
    def test_parse_two_sets_even_start_of_third(self):
        st = core.parse_live_score("6-4,3-6,0-0", "0-0", "1,0")
        self.assertEqual(st, {"sets_p1": 1, "sets_p2": 1, "games_p1": 0,
                              "games_p2": 0, "pts_p1": 0, "pts_p2": 0,
                              "server": 1, "in_tiebreak": False})

    def test_parse_advantage_returner(self):
        st = core.parse_live_score("6-1,4-5", "40-A", "1,0")
        self.assertEqual(st["sets_p1"], 1)
        self.assertEqual(st["sets_p2"], 0)
        self.assertEqual((st["games_p1"], st["games_p2"]), (4, 5))
        self.assertEqual((st["pts_p1"], st["pts_p2"]), (3, 4))  # 40 vs advantage
        self.assertEqual(st["server"], 1)

    def test_parse_deuce_and_server2(self):
        st = core.parse_live_score("3-0", "40-40", "0,1")
        self.assertEqual((st["pts_p1"], st["pts_p2"]), (3, 3))
        self.assertEqual(st["server"], 2)
        self.assertEqual((st["sets_p1"], st["sets_p2"]), (0, 0))

    def test_parse_tiebreak(self):
        st = core.parse_live_score("6-4,6-6", "5-3", "1,0")
        self.assertTrue(st["in_tiebreak"])
        self.assertEqual((st["pts_p1"], st["pts_p2"]), (5, 3))

    def test_parse_garbage_returns_none(self):
        self.assertIsNone(core.parse_live_score("", "", ""))
        self.assertIsNone(core.parse_live_score("nonsense", "x-y", "1,0"))


class LiveMatchWinProb(unittest.TestCase):
    def test_equal_players_start_near_even(self):
        st = core.parse_live_score("0-0", "0-0", "1,0")
        self.assertAlmostEqual(core.live_match_win_prob(0.62, 0.62, st), 0.5, places=1)

    def test_symmetry_swapping_players(self):
        # P1 winning-prob with (p1,p2) == 1 - P1 winning-prob with roles mirrored.
        st1 = core.parse_live_score("0-0", "0-0", "1,0")   # P1 serves
        st2 = core.parse_live_score("0-0", "0-0", "0,1")   # P2 serves (mirror)
        a = core.live_match_win_prob(0.68, 0.60, st1)
        b = core.live_match_win_prob(0.60, 0.68, st2)      # swap serve + server
        self.assertAlmostEqual(a, 1 - b, places=6)

    def test_a_set_and_break_up_is_strong(self):
        # P1 won set 1, up a break in set 2, serving -> should be a heavy favorite
        st = core.parse_live_score("6-2,3-1", "0-0", "1,0")
        self.assertGreater(core.live_match_win_prob(0.63, 0.63, st), 0.85)

    def test_a_set_and_break_down_is_weak(self):
        st = core.parse_live_score("2-6,1-3", "0-0", "0,1")
        self.assertLess(core.live_match_win_prob(0.63, 0.63, st), 0.15)

    def test_serving_for_the_match_is_near_certain(self):
        # P1 won set 1, leads 5-0 in set 2, serving at 40-0 -> essentially won
        st = core.parse_live_score("6-0,5-0", "40-0", "1,0")
        self.assertGreater(core.live_match_win_prob(0.62, 0.62, st), 0.97)

    def test_none_state_returns_none(self):
        self.assertIsNone(core.live_match_win_prob(0.6, 0.6, None))


class ImpliedServePriors(unittest.TestCase):
    def test_roundtrip_matches_target(self):
        # priors solved from a target should reproduce that target pre-match.
        for target in (0.35, 0.5, 0.65, 0.8):
            p1s, p2s = core.implied_serve_priors(target, "atp")
            st = core.parse_live_score("0-0", "0-0", "1,0")
            self.assertAlmostEqual(core.live_match_win_prob(p1s, p2s, st), target, places=3)

    def test_even_target_gives_equal_serves(self):
        p1s, p2s = core.implied_serve_priors(0.5, "atp")
        self.assertAlmostEqual(p1s, p2s, places=2)

    def test_favorite_serves_stronger(self):
        p1s, p2s = core.implied_serve_priors(0.75, "atp")
        self.assertGreater(p1s, p2s)

    def test_wta_baseline_lower(self):
        # WTA baseline serve level is lower than ATP (fewer service points held)
        self.assertLess(core.TOUR_BASE_SERVE["wta"], core.TOUR_BASE_SERVE["atp"])

    def test_priors_then_live_update_moves_with_score(self):
        # anchor to a 50/50 opening, then a break in set 1 should push P1 well
        # above 50% -> demonstrates the model reacting to the live score.
        p1s, p2s = core.implied_serve_priors(0.5, "atp")
        st_break = core.parse_live_score("2-0", "0-0", "0,1")  # P1 up an early break
        self.assertGreater(core.live_match_win_prob(p1s, p2s, st_break), 0.6)


class HarshFillSim(unittest.TestCase):
    def test_buy_pays_the_ask(self):
        ok, px = core.simulate_fill("buy", 0.48, 0.52)
        self.assertTrue(ok)
        self.assertAlmostEqual(px, 0.52)

    def test_sell_receives_the_bid(self):
        ok, px = core.simulate_fill("sell", 0.48, 0.52)
        self.assertTrue(ok)
        self.assertAlmostEqual(px, 0.48)

    def test_fee_and_slippage_make_it_worse(self):
        ok, px = core.simulate_fill("buy", 0.48, 0.52, slippage=0.01, fee=0.02)
        self.assertAlmostEqual(px, 0.52 * 1.02 + 0.01)

    def test_fok_miss_when_worse_than_limit(self):
        ok, px = core.simulate_fill("buy", 0.48, 0.52, limit_price=0.50)
        self.assertFalse(ok)
        self.assertIsNone(px)

    def test_missing_book_is_a_miss_not_a_guess(self):
        self.assertEqual(core.simulate_fill("buy", None, 0.52), (False, None))
        self.assertEqual(core.simulate_fill("sell", 0.48, None), (False, None))

    def test_round_trip_costs_the_spread(self):
        # buy at 0.52, sell at 0.48 -> you're down 0.04 before anything moves
        self.assertAlmostEqual(core.round_trip_cost(0.48, 0.52), 0.04)

    def test_round_trip_includes_fees_and_slippage(self):
        c = core.round_trip_cost(0.48, 0.52, slippage=0.01, fee=0.01)
        # (0.52*1.01+0.01) - (0.48*0.99-0.01)
        self.assertAlmostEqual(c, (0.52 * 1.01 + 0.01) - (0.48 * 0.99 - 0.01))


class EdgeSignal(unittest.TestCase):
    def test_fires_when_underpriced_beyond_min_edge(self):
        # model says 0.70 fair, market ask 0.60 -> buy 0.60, edge 0.10
        r = core.edge_signal(0.70, 0.58, 0.60, min_edge=0.05)
        self.assertTrue(r["fire"])
        self.assertAlmostEqual(r["edge"], 0.10)
        self.assertAlmostEqual(r["buy_price"], 0.60)

    def test_no_fire_when_edge_too_small(self):
        r = core.edge_signal(0.70, 0.66, 0.68, min_edge=0.05)  # edge 0.02
        self.assertFalse(r["fire"])
        self.assertEqual(r["reason"], "insufficient_edge")

    def test_costs_eat_the_edge(self):
        # raw edge 0.10, but fee+slippage push the buy price up past the hurdle
        r = core.edge_signal(0.70, 0.58, 0.60, min_edge=0.08, slippage=0.02, fee=0.02)
        self.assertFalse(r["fire"])

    def test_no_book_no_fire(self):
        r = core.edge_signal(0.70, None, None, min_edge=0.05)
        self.assertFalse(r["fire"])
        self.assertEqual(r["reason"], "no_fill")


class NameMatching(unittest.TestCase):
    def test_normalize_strips_accents_and_case(self):
        self.assertEqual(core.normalize_name("Stéfanos Tsitsipás"), "stefanos tsitsipas")

    def test_last_name(self):
        self.assertEqual(core.last_name("Carlos Alcaraz"), "alcaraz")
        self.assertEqual(core.last_name(""), "")

    def test_both_players_present_order_independent(self):
        self.assertTrue(core.both_players_present(
            "Stefanos Tsitsipas", "Jerome Kym", "Kym vs Tsitsipas — ATP"))
        self.assertTrue(core.both_players_present(
            "Jerome Kym", "Stefanos Tsitsipas", "Tsitsipas vs Kym"))

    def test_both_players_absent_when_one_missing(self):
        self.assertFalse(core.both_players_present(
            "Stefanos Tsitsipas", "Jerome Kym", "Tsitsipas vs Alcaraz"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
