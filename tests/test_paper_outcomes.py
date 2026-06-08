import tempfile
import unittest
from pathlib import Path

import pandas as pd

from dashboard import paper_outcomes


class PaperOutcomeTests(unittest.TestCase):
    def decision(self, **overrides):
        values = {
            "decision_id": "decision-1",
            "source_date": "2026-01-02",
            "ticker": "ABC",
            "action": "paper buy candidate",
            "model_version": "baseline_v1",
            "horizon_days": 5,
            "reference_price": 100,
            "stop_loss": 95,
            "target_price": 110,
        }
        values.update(overrides)
        return pd.DataFrame([values])

    def prices(self, count=65, ticker="ABC"):
        dates = pd.bdate_range("2026-01-05", periods=count)
        return pd.DataFrame(
            {
                "ticker": ticker,
                "begins_at": dates,
                "close_price": [101 + index for index in range(count)],
            }
        )

    def test_uses_nth_future_trading_session(self):
        records = paper_outcomes.evaluate_decisions(self.decision(), self.prices())
        one_day = next(row for row in records if row["evaluation_horizon_days"] == 1)
        five_day = next(row for row in records if row["evaluation_horizon_days"] == 5)
        self.assertEqual(one_day["evaluation_date"], "2026-01-05")
        self.assertEqual(five_day["evaluation_date"], "2026-01-09")
        self.assertAlmostEqual(five_day["return_pct"], 0.05)

    def test_only_mature_horizons_are_emitted(self):
        records = paper_outcomes.evaluate_decisions(self.decision(), self.prices(count=4))
        self.assertEqual([row["evaluation_horizon_days"] for row in records], [1])

    def test_missing_ticker_prices_becomes_unavailable_after_market_matures(self):
        records = paper_outcomes.evaluate_decisions(
            self.decision(ticker="MISSING"), self.prices(count=5)
        )
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row["status"] == "unavailable" for row in records))

    def test_duplicate_runs_append_nothing(self):
        records = paper_outcomes.evaluate_decisions(self.decision(), self.prices(count=5))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "outcomes.csv"
            self.assertEqual(paper_outcomes.append_outcomes(records, path), 2)
            existing = paper_outcomes.load_outcomes(path)
            rerun = paper_outcomes.evaluate_decisions(
                self.decision(), self.prices(count=5), existing=existing
            )
            self.assertEqual(rerun, [])
            self.assertEqual(paper_outcomes.append_outcomes(records, path), 0)

    def test_recovered_price_appends_after_unavailable_event(self):
        missing = paper_outcomes.evaluate_decisions(
            self.decision(ticker="MISSING"), self.prices(count=5)
        )
        existing = pd.DataFrame(missing)
        recovered_prices = self.prices(count=5, ticker="MISSING")
        recovered = paper_outcomes.evaluate_decisions(
            self.decision(ticker="MISSING"), recovered_prices, existing=existing
        )
        self.assertEqual(len(recovered), 2)
        self.assertTrue(all(row["status"] != "unavailable" for row in recovered))

    def test_first_barrier_hit_sets_status(self):
        prices = self.prices(count=5)
        prices["close_price"] = [94, 111, 105, 106, 107]
        records = paper_outcomes.evaluate_decisions(self.decision(), prices)
        one_day = next(row for row in records if row["evaluation_horizon_days"] == 1)
        self.assertEqual(one_day["status"], "stopped")
        self.assertEqual(one_day["barrier_date"], "2026-01-05")


if __name__ == "__main__":
    unittest.main()
