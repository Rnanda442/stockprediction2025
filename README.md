# stockprediction2025

Automated stock research pipeline with a private local Streamlit dashboard.

## Dashboard

Generate the compact read-only dashboard database:

```powershell
python -m pip install -r requirements-dashboard.txt
python scripts/export_dashboard_data.py
```

Start the private dashboard:

```powershell
$env:DASHBOARD_PASSWORD="choose-a-local-password"
streamlit run dashboard/app.py
```

Open `http://localhost:8501`.

The dashboard includes:

- latest five-stock shortlist
- ranked 50-stock swing-trade watchlist with confidence and holding-window guidance
- interactive 3D stock-universe map with visible filter outcomes
- visual opportunity map for comparing signal strength, volatility, and liquidity
- rebased price race for the latest shortlist
- forward-return evaluation over 1, 5, 20, and 60 trading sessions
- historical shortlist snapshots
- searchable ticker feature summaries
- recent price charts
- pipeline freshness metadata

The scheduled GitHub Action exports `dashboard_data.db` as part of its
`stock-analysis-outputs` artifact. The dashboard reads this compact database
only; it never modifies pipeline databases.

## Robinhood Session Reuse

Set the `ROBINHOOD_SESSION_KEY` GitHub Actions secret to a Fernet key. The
workflow uses it to cache an encrypted Robinhood session between runs. Raw
Robinhood tokens stay inside the runner and are never committed or uploaded as
artifacts.

Generate a key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The first run after setup may still require Robinhood device approval. Later
runs reuse the encrypted session until Robinhood expires or revokes it.
