import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "robinhood_portfolio_snapshot.csv"
NOTEBOOK = ROOT / "notebook"


def load_robinhood_helpers():
    sys.path.insert(0, str(NOTEBOOK))
    from robinhood_auth_login import login
    from robin_stocks.robinhood.helper import request_get
    from robin_stocks.robinhood.urls import positions_url

    return login, request_get, positions_url


def paginated_get(request_get, url, params=None):
    rows = []
    next_url = url
    while next_url:
        response = request_get(next_url, "pagination", params or {})
        if isinstance(response, dict):
            rows.extend(response.get("results", []))
            next_url = response.get("next")
            params = None
        else:
            break
    return rows


def account_cash(request_get):
    try:
        account = request_get("https://api.robinhood.com/accounts/")
        rows = account.get("results", []) if isinstance(account, dict) else []
        if not rows:
            return None
        row = rows[0]
        for key in ("cash", "buying_power", "cash_available_for_withdrawal"):
            value = row.get(key)
            if value not in (None, ""):
                return float(value)
    except Exception:
        return None
    return None


def instrument_symbol(request_get, url):
    instrument = request_get(url)
    if not isinstance(instrument, dict):
        return None
    symbol = instrument.get("symbol")
    return str(symbol).upper() if symbol else None


def main():
    login, request_get, positions_url = load_robinhood_helpers()
    username = os.getenv("ROBINHOOD_USERNAME")
    password = os.getenv("ROBINHOOD_PASSWORD")
    mfa_code = os.getenv("ROBINHOOD_MFA_CODE")

    print("Logging in to Robinhood for a read-only portfolio snapshot...")
    login(username=username, password=password, mfa_code=mfa_code, store_session=True)

    snapshot_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cash = account_cash(request_get)
    positions = paginated_get(request_get, positions_url(), {"nonzero": "true"})
    rows = []
    for position in positions:
        quantity = float(position.get("quantity") or 0)
        if quantity <= 0:
            continue
        symbol = instrument_symbol(request_get, position.get("instrument"))
        if not symbol:
            continue
        rows.append(
            {
                "snapshot_at": snapshot_at,
                "ticker": symbol,
                "quantity": quantity,
                "average_buy_price": position.get("average_buy_price", ""),
                "cash": "" if cash is None else cash,
            }
        )

    if not rows:
        raise RuntimeError("No open stock positions were returned by Robinhood.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    exists = OUTPUT.exists()
    with OUTPUT.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["snapshot_at", "ticker", "quantity", "average_buy_price", "cash"],
        )
        if not exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} position rows to {OUTPUT}")
    print(f"Snapshot timestamp: {snapshot_at}")


if __name__ == "__main__":
    main()
