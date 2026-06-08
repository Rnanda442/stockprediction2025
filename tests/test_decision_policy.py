import unittest

import pandas as pd

from dashboard import decision_policy


class DecisionPolicyTests(unittest.TestCase):
    def test_strong_unheld_candidate_becomes_paper_buy(self):
        row = {
            "is_holding": False,
            "rank": 4,
            "model_probability_up": 0.64,
            "confidence": 72,
        }
        self.assertEqual(decision_policy.decision_action(row), "paper buy candidate")

    def test_holding_without_signal_becomes_reduce_review(self):
        row = {
            "is_holding": True,
            "rank": 40,
            "model_probability_up": 0.44,
            "confidence": 30,
        }
        self.assertEqual(decision_policy.decision_action(row), "review reduce")

    def test_zero_portfolio_value_produces_zero_quantity(self):
        row = {"entry_price": 100, "vol_60d": 0.04}
        self.assertEqual(decision_policy.paper_quantity(row, 0), 0)

    def test_policy_adds_auditable_columns(self):
        board = pd.DataFrame(
            [
                {
                    "ticker": "ABC",
                    "is_holding": False,
                    "rank": 2,
                    "model_probability_up": 0.66,
                    "model_horizon_days": 20,
                    "confidence": 75,
                    "entry_price": 100,
                    "vol_60d": 0.04,
                    "portfolio_weight": 0,
                }
            ]
        )
        result = decision_policy.apply_policy(board, 10000)
        self.assertEqual(result.iloc[0]["decision"], "paper buy candidate")
        self.assertGreater(result.iloc[0]["paper_quantity_1pct_risk"], 0)
        self.assertIn("watchlist rank 2", result.iloc[0]["why"])


if __name__ == "__main__":
    unittest.main()

