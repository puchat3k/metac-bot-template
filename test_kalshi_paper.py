import unittest
from datetime import datetime, timedelta, timezone

import kalshi_paper as kp


class KalshiPaperPortfolioTests(unittest.TestCase):
    def test_small_edge_uses_base_shrink(self):
        old = kp.MODEL_EDGE_SHRINK
        try:
            kp.MODEL_EDGE_SHRINK = 0.35
            adjusted = kp.strategy_probability(0.55, 0.50, 24)
            self.assertAlmostEqual(adjusted, 0.5175, places=6)
        finally:
            kp.MODEL_EDGE_SHRINK = old

    def test_extreme_edge_is_heavily_shrunk(self):
        old = kp.MODEL_EDGE_SHRINK
        try:
            kp.MODEL_EDGE_SHRINK = 0.35
            adjusted = kp.strategy_probability(0.90, 0.50, 24)
            self.assertAlmostEqual(adjusted, 0.52, places=6)
            self.assertAlmostEqual(kp.effective_edge_shrink(0.40, 24), 0.05, places=6)
        finally:
            kp.MODEL_EDGE_SHRINK = old

    def test_near_expiry_is_heavily_shrunk(self):
        old = kp.MODEL_EDGE_SHRINK
        try:
            kp.MODEL_EDGE_SHRINK = 0.35
            adjusted = kp.strategy_probability(0.60, 0.50, 1)
            self.assertAlmostEqual(adjusted, 0.505, places=6)
            self.assertAlmostEqual(kp.effective_edge_shrink(0.10, 1), 0.05, places=6)
        finally:
            kp.MODEL_EDGE_SHRINK = old

    def test_empirical_risk_multiplier_downweights_bad_cohorts(self):
        self.assertAlmostEqual(kp.empirical_risk_multiplier(0.35, 1), 0.01, places=6)
        self.assertAlmostEqual(kp.empirical_risk_multiplier(0.05, 24), 1.0, places=6)

    def test_coverage_keeps_extreme_or_wide_spread_markets_outside_model_filter(self):
        now = datetime.now(timezone.utc)
        market = {
            "ticker": "EXTREME",
            "title": "Extreme priced market",
            "market_type": "binary",
            "is_provisional": False,
            "close_time": (now + timedelta(days=2)).isoformat(),
            "yes_bid_dollars": "0.005",
            "yes_ask_dollars": "0.025",
            "no_bid_dollars": "0.975",
        }
        covered = kp.coverage_markets([market])
        self.assertEqual(len(covered), 1)
        self.assertEqual(kp.select_markets(covered), [])

    def test_full_kelly_is_zero_without_positive_edge(self):
        self.assertEqual(kp.full_kelly_fraction(0.60, 0.60), 0.0)
        self.assertGreater(kp.full_kelly_fraction(0.40, 0.60), 0.0)

    def test_allocate_portfolio_preserves_minimum_coverage(self):
        records = []
        for idx, side in enumerate(("yes", "yes", "no")):
            records.append(
                {
                    "source_opportunity_id": f"M{idx}",
                    "edge": 0.20 - idx * 0.05,
                    "direction": side,
                    "notional_usd": 25.0,
                    "metadata": {"event_ticker": "EVENT-1"},
                }
            )

        old_min = kp.PAPER_MIN_POSITION_USD
        old_gross = kp.PAPER_GROSS_NOTIONAL_USD
        old_event = kp.MAX_EVENT_GROSS_FRACTION
        old_net = kp.MAX_EVENT_NET_FRACTION
        try:
            kp.PAPER_MIN_POSITION_USD = 1.0
            kp.PAPER_GROSS_NOTIONAL_USD = 30.0
            kp.MAX_EVENT_GROSS_FRACTION = 0.5
            kp.MAX_EVENT_NET_FRACTION = 0.35
            out = kp.allocate_portfolio(records)
            self.assertEqual(len(out), 3)
            self.assertTrue(all(float(r["notional_usd"]) >= 1.0 for r in out))
            self.assertLessEqual(sum(float(r["notional_usd"]) for r in out), 30.01)
        finally:
            kp.PAPER_MIN_POSITION_USD = old_min
            kp.PAPER_GROSS_NOTIONAL_USD = old_gross
            kp.MAX_EVENT_GROSS_FRACTION = old_event
            kp.MAX_EVENT_NET_FRACTION = old_net


    def test_contrarian_shadow_inverts_primary_and_is_not_training_eligible(self):
        primary = {
            "source_opportunity_id": "TEST",
            "title": "Test",
            "model_key": "primary",
            "forecast_probability": 0.52,
            "market_probability": 0.50,
            "edge": 0.02,
            "direction": "yes",
            "notional_usd": 4.0,
            "locked_at": "2026-09-23T00:00:00+00:00",
            "lock_bucket": "2026-09-23T00:00:00+00:00",
            "closes_at": "2026-09-24T00:00:00+00:00",
            "metadata": {
                "raw_model_edge": 0.40,
                "event_ticker": "EVENT",
            },
        }
        hedge = kp.build_contrarian_position(primary)
        self.assertEqual(hedge["direction"], "no")
        self.assertAlmostEqual(hedge["forecast_probability"], 0.48, places=6)
        self.assertFalse(hedge["metadata"]["training_eligible"])
        self.assertTrue(hedge["metadata"]["shadow_challenger"])
        self.assertEqual(hedge["notional_usd"], 2.0)

    def test_contrarian_threshold_targets_only_extreme_raw_disagreement(self):
        self.assertGreaterEqual(0.25, kp.CONTRARIAN_EDGE_THRESHOLD)
        self.assertLess(0.10, kp.CONTRARIAN_EDGE_THRESHOLD)

    def test_control_position_is_not_training_eligible(self):
        now = datetime.now(timezone.utc)
        market = {
            "ticker": "TEST",
            "title": "Test market",
            "event_ticker": "EVENT",
        }
        record = kp.build_control_position(
            now,
            kp.six_hour_bucket(now),
            now + timedelta(days=1),
            market,
            0.6,
            0.59,
            0.61,
            "outside_model_forecast_budget",
        )
        self.assertEqual(record["notional_usd"], round(kp.PAPER_MIN_POSITION_USD, 2))
        self.assertFalse(record["metadata"]["training_eligible"])
        self.assertTrue(record["metadata"]["control_only"])


    def test_moderate_disagreement_challenger_requires_declared_green_zone(self):
        base = {
            "source_opportunity_id": "TEST",
            "title": "Test",
            "model_key": "primary",
            "forecast_probability": 0.52,
            "market_probability": 0.50,
            "edge": 0.02,
            "direction": "yes",
            "notional_usd": 5.0,
            "locked_at": "2026-09-23T00:00:00+00:00",
            "lock_bucket": "2026-09-23T00:00:00+00:00",
            "closes_at": "2026-09-24T00:00:00+00:00",
            "metadata": {
                "raw_model_edge": 0.07,
                "horizon_hours": 24.0,
                "event_ticker": "EVENT",
            },
        }
        challenger = kp.build_moderate_disagreement_challenger(base)
        self.assertIsNotNone(challenger)
        self.assertEqual(challenger["direction"], "yes")
        self.assertFalse(challenger["metadata"]["training_eligible"])
        self.assertTrue(challenger["metadata"]["shadow_challenger"])

        too_large = {**base, "metadata": {**base["metadata"], "raw_model_edge": 0.21}}
        self.assertIsNone(kp.build_moderate_disagreement_challenger(too_large))

        too_late = {**base, "metadata": {**base["metadata"], "horizon_hours": 2.0}}
        self.assertIsNone(kp.build_moderate_disagreement_challenger(too_late))

    def test_anti_longshot_challenger_backs_favorite_side_only_at_extremes(self):
        now = datetime.now(timezone.utc)
        market = {"ticker": "TAIL", "title": "Tail", "event_ticker": "EVENT"}

        low = kp.build_anti_longshot_challenger(
            now, kp.six_hour_bucket(now), now + timedelta(days=1), market, 0.08
        )
        self.assertEqual(low["direction"], "no")
        self.assertEqual(low["forecast_probability"], 0.08)
        self.assertFalse(low["metadata"]["training_eligible"])
        self.assertTrue(low["metadata"]["benchmark_challenger"])

        high = kp.build_anti_longshot_challenger(
            now, kp.six_hour_bucket(now), now + timedelta(days=1), market, 0.93
        )
        self.assertEqual(high["direction"], "yes")

        middle = kp.build_anti_longshot_challenger(
            now, kp.six_hour_bucket(now), now + timedelta(days=1), market, 0.50
        )
        self.assertIsNone(middle)


if __name__ == "__main__":
    unittest.main()
