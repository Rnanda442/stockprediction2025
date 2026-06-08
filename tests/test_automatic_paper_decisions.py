import tempfile
import unittest
from pathlib import Path

from dashboard import automatic_paper_decisions as decisions


class AutomaticPaperDecisionTests(unittest.TestCase):
    def sample_values(self):
        return {
            "source_date": "2026-06-08",
            "ticker": "abc",
            "action": "paper buy candidate",
            "reason": "watchlist rank 2; 64% model probability",
            "horizon_days": 20,
            "reference_price": 100,
            "paper_quantity": 5,
            "stop_loss": 92,
            "target_price": 116,
            "constraint_status": "caution",
            "constraint_reason": "Buying power has not been verified.",
        }

    def test_build_record_normalizes_and_calculates_risk(self):
        record = decisions.build_record(
            self.sample_values(), created_at="2026-06-08T12:00:00+00:00"
        )
        self.assertEqual(record["ticker"], "ABC")
        self.assertEqual(record["model_version"], decisions.MODEL_VERSION)
        self.assertEqual(record["risk_dollars"], 40.0)
        self.assertEqual(len(record["decision_id"]), 20)

    def test_decision_id_is_stable_for_same_decision(self):
        first = decisions.build_record(self.sample_values())
        second = decisions.build_record(self.sample_values())
        self.assertEqual(first["decision_id"], second["decision_id"])

    def test_append_records_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.csv"
            record = decisions.build_record(self.sample_values())
            self.assertEqual(decisions.append_records([record, record], path), 1)
            self.assertEqual(decisions.append_records([record], path), 0)
            self.assertEqual(len(decisions.load_ledger(path)), 1)

    def test_missing_required_field_is_rejected(self):
        values = self.sample_values()
        values["reason"] = ""
        with self.assertRaises(ValueError):
            decisions.build_record(values)


if __name__ == "__main__":
    unittest.main()
