from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import services
from dashboard import data


def require(condition, message):
    if not condition:
        raise SystemExit(f"ERROR: {message}")
    print(f"PASS: {message}")


def main():
    health = data.health()
    require(bool(health.get("latest_market_date")), "pipeline health has latest market date")
    require(
        float(health.get("latest_market_coverage", 0)) >= 0.8,
        "pipeline latest-date coverage is acceptable",
    )

    context = services.daily_decision_context(limit=10)
    decisions = context["ranked_decisions"]
    require(not decisions.empty, "daily decision context returns ranked rows")
    require(
        {"ticker", "decision", "constraint_status"}.issubset(decisions.columns),
        "daily decision rows include frontend contract fields",
    )

    status, performance = services.paper_performance_summary()
    require(status["decisions"] >= status["matured"], "paper status counts are coherent")
    require(not performance.empty, "paper performance has matured grouped rows")

    first_ticker = str(decisions.iloc[0]["ticker"])
    detail = services.ticker_detail(first_ticker)
    require(len(detail["summary"]) >= 1, f"{first_ticker} ticker summary is available")
    require(len(detail["prices"]) >= 1, f"{first_ticker} recent prices are available")

    readiness = services.readiness_report()
    require(readiness["checks"], "readiness report returns checks")
    require(
        readiness["overall"] in {"usable for review and paper decisions", "needs attention before review"},
        "readiness report returns a known overall status",
    )
    print("App service smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
