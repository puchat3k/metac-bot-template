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


if __name__ == "__main__":
    unittest.main()
