import sqlite3
from datetime import date, datetime

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend import services
from dashboard import data


app = FastAPI(
    title="Stock Research API",
    version="0.1.0",
    description="Read-only API over the compact stock dashboard database.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _clean_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(frame, limit=None):
    if frame is None or frame.empty:
        return []
    if limit is not None:
        frame = frame.head(limit)
    return [
        {key: _clean_value(value) for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def _dashboard_call(callback):
    try:
        return callback()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise HTTPException(status_code=503, detail=f"Dashboard database error: {exc}") from exc


def _health_float(health, key):
    try:
        return float(health[key])
    except (KeyError, TypeError, ValueError):
        return None


def _health_int(health, key):
    try:
        return int(float(health[key]))
    except (KeyError, TypeError, ValueError):
        return None


@app.get("/")
def api_index():
    return {
        "name": "Stock Research API",
        "frontend": "Streamlit dashboard/app.py",
        "endpoints": [
            "/api/summary",
            "/api/readiness",
            "/api/health",
            "/api/daily-decisions",
            "/api/shortlist",
            "/api/watchlist",
            "/api/ticker/{ticker}",
            "/api/model/evaluation",
            "/api/model/tournament",
            "/api/model/predictions",
            "/api/paper/status",
            "/api/paper/performance",
        ],
    }


@app.get("/api/health")
def pipeline_health():
    return _dashboard_call(lambda: data.health())


@app.get("/api/readiness")
def readiness():
    return _dashboard_call(services.readiness_report)


@app.get("/api/summary")
def summary():
    def build():
        health = data.health()
        paper_status = data.paper_learning_status()
        shortlist = data.shortlist()
        watchlist = data.watchlist()
        model_horizons = data.model_horizon_status()
        return {
            "exported_at": health.get("exported_at"),
            "latest_market_date": str(health.get("latest_market_date", ""))[:10],
            "latest_shortlist_date": str(health.get("latest_shortlist_date", ""))[:10],
            "latest_market_coverage": _health_float(health, "latest_market_coverage"),
            "counts": {
                "latest_market_tickers": _health_int(health, "latest_market_tickers"),
                "tracked_market_tickers": _health_int(health, "tracked_market_tickers"),
                "latest_shortlist_rows": _health_int(health, "latest_shortlist_rows"),
                "latest_watchlist_rows": _health_int(health, "latest_watchlist_rows"),
                "latest_model_predictions_rows": _health_int(
                    health, "latest_model_predictions_rows"
                ),
            },
            "paper": paper_status,
            "model_horizons": _records(model_horizons),
            "shortlist": _records(shortlist, limit=5),
            "watchlist_top": _records(watchlist, limit=10),
        }

    return _dashboard_call(build)


@app.get("/api/daily-decisions")
def daily_decisions(limit: int = Query(25, ge=1, le=50)):
    def build():
        context = services.daily_decision_context(limit=limit)
        return {
            "portfolio_value": context["portfolio_value"],
            "cash": context["cash"],
            "constraint_status": context["constraint_status"],
            "constraint_message": context["constraint_message"],
            "constraints": context["constraints"],
            "rows": _records(context["ranked_decisions"]),
        }

    return _dashboard_call(build)


@app.get("/api/shortlist")
def shortlist():
    return _dashboard_call(lambda: {"rows": _records(data.shortlist())})


@app.get("/api/watchlist")
def watchlist(limit: int = Query(50, ge=1, le=200)):
    return _dashboard_call(lambda: {"rows": _records(data.watchlist(), limit=limit)})


@app.get("/api/ticker/{ticker}")
def ticker_detail(ticker: str):
    def build():
        detail = services.ticker_detail(ticker)
        if detail["summary"].empty and detail["prices"].empty:
            raise HTTPException(status_code=404, detail=f"Ticker not found: {ticker}")
        return {
            "ticker": detail["ticker"],
            "summary": _records(detail["summary"]),
            "prices": _records(detail["prices"]),
            "predictions": _records(detail["predictions"]),
        }

    return _dashboard_call(build)


@app.get("/api/model/evaluation")
def model_evaluation():
    return _dashboard_call(lambda: {"rows": _records(data.model_evaluation())})


@app.get("/api/model/tournament")
def model_tournament():
    return _dashboard_call(lambda: {"rows": _records(data.model_tournament_evaluation())})


@app.get("/api/model/predictions")
def model_predictions(
    horizon_days: int = Query(20, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
):
    return _dashboard_call(
        lambda: {
            "horizon_days": horizon_days,
            "rows": _records(data.latest_model_predictions(horizon_days, limit), limit=limit),
        }
    )


@app.get("/api/paper/status")
def paper_status():
    return _dashboard_call(lambda: data.paper_learning_status())


@app.get("/api/paper/decisions")
def paper_decisions(limit: int = Query(100, ge=1, le=500)):
    return _dashboard_call(
        lambda: {"rows": _records(data.automatic_paper_decisions(), limit=limit)}
    )


@app.get("/api/paper/outcomes")
def paper_outcomes(limit: int = Query(100, ge=1, le=500)):
    return _dashboard_call(
        lambda: {"rows": _records(data.automatic_paper_outcomes(), limit=limit)}
    )


@app.get("/api/paper/performance")
def paper_performance():
    def build():
        status, grouped = services.paper_performance_summary()
        return {"status": status, "by_horizon_action": _records(grouped)}

    return _dashboard_call(build)
