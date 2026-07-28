import pytest

pytest.importorskip("fastapi")

try:
    from fastapi.testclient import TestClient
except RuntimeError as exc:
    pytest.skip(str(exc), allow_module_level=True)

from backend.main import app


client = TestClient(app)


def test_summary_exposes_dashboard_contract():
    response = client.get("/api/summary")
    assert response.status_code == 200
    payload = response.json()

    assert payload["latest_market_date"]
    assert payload["latest_shortlist_date"]
    assert payload["latest_market_coverage"] is not None
    assert len(payload["shortlist"]) == 5
    assert payload["paper"]["decisions"] >= payload["paper"]["matured"]


def test_paper_performance_returns_grouped_rows():
    response = client.get("/api/paper/performance")
    assert response.status_code == 200
    payload = response.json()

    assert "status" in payload
    assert payload["status"]["outcome_events"] >= 0
    assert "by_horizon_action" in payload


def test_model_predictions_limit_is_applied():
    response = client.get("/api/model/predictions", params={"horizon_days": 20, "limit": 3})
    assert response.status_code == 200
    payload = response.json()

    assert payload["horizon_days"] == 20
    assert len(payload["rows"]) <= 3


def test_model_tournament_endpoint_is_available():
    response = client.get("/api/model/tournament")
    assert response.status_code == 200
    assert "rows" in response.json()


def test_model_candidate_predictions_endpoint_is_available():
    response = client.get(
        "/api/model/candidate-predictions",
        params={"horizon_days": 20, "limit_per_model": 3},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["horizon_days"] == 20
    assert "rows" in payload


def test_daily_decisions_exposes_streamlit_backend_context():
    response = client.get("/api/daily-decisions", params={"limit": 5})
    assert response.status_code == 200
    payload = response.json()

    assert "constraint_status" in payload
    assert "constraint_message" in payload
    assert len(payload["rows"]) <= 5
    if payload["rows"]:
        assert {"ticker", "decision", "constraint_status"}.issubset(payload["rows"][0])


def test_readiness_report_exposes_product_state():
    response = client.get("/api/readiness")
    assert response.status_code == 200
    payload = response.json()

    assert payload["overall"]
    assert payload["checks"]
    assert {check["key"] for check in payload["checks"]} >= {
        "approved_artifact",
        "model_baseline",
        "daily_decisions",
        "paper_loop",
        "live_trading",
    }


def test_ticker_detail_returns_prices_and_summary():
    response = client.get("/api/ticker/AMAT")
    assert response.status_code == 200
    payload = response.json()

    assert payload["ticker"] == "AMAT"
    assert payload["summary"]
    assert payload["prices"]
