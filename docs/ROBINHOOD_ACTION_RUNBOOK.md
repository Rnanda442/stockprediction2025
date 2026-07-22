# Robinhood GitHub Actions Runbook

Last updated: 2026-06-04

## When To Use This

Use this when `stock-run.yml` fails during the notebook login step with Robinhood app verification, challenge, MFA, or session-expiration messages.

The workflow does not place trades. It refreshes the research pipeline and exports dashboard data.

## What Happened In Run `26987118096`

Run URL:

https://github.com/Rnanda442/stockprediction2025/actions/runs/26987118096

Result:

- The run failed in the notebook login cell.
- Robinhood required app verification.
- The app verification prompt was not approved before the notebook timeout.
- The run uploaded a fallback `dashboard_data.db`, but it was the restored prior healthy database, not a newly refreshed export.

Healthy fallback artifact status from that failed run:

- Latest market date: `2026-06-03`
- `ModelEvaluation`: 3 rows
- `ModelFeatureImportance`: 63 rows
- `LatestModelPredictions`: 7,410 rows

## Rerun Flow

1. Open the Robinhood mobile app and be ready to approve a verification prompt.
2. Dispatch the workflow:

```powershell
gh workflow run stock-run.yml --repo Rnanda442/stockprediction2025 --ref main
```

3. Wait a few seconds, then find the newest run:

```powershell
gh run list --repo Rnanda442/stockprediction2025 --workflow stock-run.yml --branch main --limit 1
```

4. Watch the run:

```powershell
gh run watch RUN_ID --repo Rnanda442/stockprediction2025 --exit-status
```

5. If Robinhood prompts for approval, approve it before the notebook timeout.
6. After a successful run, sync the local dashboard database:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_dashboard.ps1 -SyncOnly
```

7. Confirm model tables are present:

```powershell
@'
import sqlite3
with sqlite3.connect("dashboard_data.db") as conn:
    for table in ["ModelEvaluation", "ModelFeatureImportance", "LatestModelPredictions"]:
        print(table, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
'@ | python -
```

## If No Robinhood Prompt Appears

If the workflow says verification is required but the app does not show a prompt:

- Refresh the local Robinhood session from a trusted local run.
- Confirm `.auth-cache/robinhood.pickle.fernet` is being saved by the workflow after successful login.
- Rerun the workflow after the session cache is refreshed.

## Product Follow-Up

The workflow now runs `scripts/check_robinhood_auth_preflight.py` before the notebook step. It verifies required secrets and whether a cached session file was restored. It cannot guarantee Robinhood will not require app approval, but it catches missing secrets/session cache earlier and gives a clearer warning before the expensive notebook step.

The workflow also runs `scripts/check_model_export_smoke.py` after dashboard export to confirm model tables and 5d/20d/60d prediction horizons.

As of 2026-07-22, the Robinhood app-verification wait defaults to 300 seconds.
Override it with `ROBINHOOD_VERIFICATION_TIMEOUT_SECONDS` only if the default is
too short or too long.
