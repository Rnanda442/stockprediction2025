import numpy as np
import pandas as pd

from scripts import build_model_baseline as models


def _synthetic_frame():
    dates = pd.date_range("2026-01-01", periods=55, freq="B")
    tickers = [f"T{i}" for i in range(8)]
    rows = []
    for ticker_index, ticker in enumerate(tickers):
        for day_index, begins_at in enumerate(dates):
            wave = np.sin(day_index / 3.0 + ticker_index)
            price = 50 + ticker_index * 3 + day_index * 0.04 + wave * 1.8
            record = {
                "begins_at": begins_at,
                "ticker": ticker,
                "close_price": price,
            }
            for feature_index, feature in enumerate(models.FEATURES):
                record[feature] = (
                    np.sin(day_index / (feature_index + 2) + ticker_index)
                    + np.cos(ticker_index + feature_index / 4)
                    + (day_index % 7) * 0.01
                )
            rows.append(record)
    frame = pd.DataFrame(rows).sort_values(["ticker", "begins_at"]).reset_index(drop=True)
    grouped = frame.groupby("ticker", sort=False)["close_price"]
    frame["future_price_5d"] = grouped.shift(-5)
    return frame


def test_model_tournament_builds_ann_candidate(monkeypatch):
    monkeypatch.setattr(models, "TEST_DATES", 8)
    monkeypatch.setattr(models, "MAX_TRAIN_ROWS", 250)
    monkeypatch.setattr(models, "MAX_TEST_ROWS", 120)
    monkeypatch.setattr(models, "MODEL_CANDIDATES", ("sgd_logistic", "mlp_ann"))
    monkeypatch.setenv("MODEL_MLP_MAX_ITER", "12")
    monkeypatch.setenv("MODEL_MLP_HIDDEN_LAYERS", "8")

    evaluations, importances, predictions = models.build_horizon(_synthetic_frame(), 5)
    tournament = models.mark_champions(pd.DataFrame(evaluations))

    assert set(tournament["model_name"]) == {"sgd_logistic", "mlp_ann"}
    assert tournament["is_champion"].sum() == 1
    assert {
        "positive_rate",
        "majority_accuracy",
        "accuracy_lift",
        "baseline_brier_score",
        "brier_skill",
        "selected_return_edge",
        "selected_win_lift",
    }.issubset(tournament.columns)
    assert predictions
    assert pd.concat(predictions)["model_name"].isin({"sgd_logistic", "mlp_ann"}).all()
    assert importances
