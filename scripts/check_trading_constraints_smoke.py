import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard import trading_constraints as constraints  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main():
    missing = constraints.latest_constraints(ROOT / "does-not-exist.csv")
    require(
        constraints.status(missing)[0] == "unknown",
        "missing snapshot reports unknown overall status",
    )
    require(
        constraints.action_status("paper buy candidate", missing)["constraint_status"] == "blocked",
        "missing snapshot blocks executable buy-side actions",
    )

    safe = {
        "source": "snapshot",
        "snapshot_at": "2026-07-22T00:00:00Z",
        "account_type": "margin",
        "equity": 30_000.0,
        "buying_power": 5_000.0,
        "cash": 2_500.0,
        "day_trades_5d": 0.0,
        "weekly_trades": 0.0,
        "max_day_trades_5d": 3,
        "max_weekly_trades": 4,
        "notes": "",
    }
    require(
        constraints.action_status("paper buy candidate", safe, estimated_notional=1_000.0)[
            "constraint_status"
        ]
        == "safe",
        "well-funded buy-side action passes",
    )
    require(
        constraints.action_status("watch", safe)["constraint_status"] == "no trade",
        "watch action is marked as no trade",
    )

    no_buying_power = {**safe, "equity": 10_000.0, "buying_power": 0.0}
    require(
        constraints.action_status("hold / consider add", no_buying_power)["constraint_status"]
        == "blocked",
        "zero buying power blocks add actions",
    )

    maxed_day_trades = {**safe, "equity": 10_000.0, "day_trades_5d": 3.0}
    require(
        constraints.action_status("review reduce", maxed_day_trades)["constraint_status"]
        == "blocked",
        "used-up day-trade budget blocks sell-side actions",
    )

    unknown_weekly = {**safe, "weekly_trades": None}
    require(
        constraints.action_status("paper buy candidate", unknown_weekly)["constraint_status"]
        == "caution",
        "unknown weekly trade count cautions trade actions",
    )

    print("Trading constraint smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
