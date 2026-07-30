import sqlite3
from pathlib import Path


EXPECTED_HORIZONS = {5, 20, 60}
TABLES = {
    "ModelEvaluation": 3,
    "ModelFeatureImportance": 1,
    "LatestModelPredictions": 1,
    "MLRunHistory": 1,
    "ModelEvaluationHistory": 3,
    "ModelWalkForwardEvaluation": 3,
}


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def count_rows(conn, table):
    if not table_exists(conn, table):
        return None
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def check_database(path):
    if not path.exists():
        raise RuntimeError(f"{path} does not exist")
    errors = []
    with sqlite3.connect(path) as conn:
        for table, minimum in TABLES.items():
            rows = count_rows(conn, table)
            if rows is None:
                errors.append(f"{path}:{table} is missing")
            elif rows < minimum:
                errors.append(f"{path}:{table} has {rows} rows; expected at least {minimum}")

        if table_exists(conn, "LatestModelPredictions"):
            horizon_rows = dict(
                conn.execute(
                    """
                    SELECT horizon_days, COUNT(*)
                    FROM LatestModelPredictions
                    GROUP BY horizon_days
                    """
                ).fetchall()
            )
            missing = EXPECTED_HORIZONS - set(horizon_rows)
            if missing:
                errors.append(
                    f"{path}:LatestModelPredictions missing horizons "
                    + ", ".join(f"{horizon}d" for horizon in sorted(missing))
                )
            for horizon in sorted(EXPECTED_HORIZONS & set(horizon_rows)):
                if horizon_rows[horizon] <= 0:
                    errors.append(f"{path}:LatestModelPredictions horizon {horizon}d is empty")

        if table_exists(conn, "ModelTournamentEvaluation"):
            tournament_horizons = dict(
                conn.execute(
                    """
                    SELECT horizon_days, SUM(CASE WHEN is_champion THEN 1 ELSE 0 END)
                    FROM ModelTournamentEvaluation
                    WHERE fit_status='ok'
                    GROUP BY horizon_days
                    """
                ).fetchall()
            )
            missing = EXPECTED_HORIZONS - set(tournament_horizons)
            if missing:
                errors.append(
                    f"{path}:ModelTournamentEvaluation missing horizons "
                    + ", ".join(f"{horizon}d" for horizon in sorted(missing))
                )
            for horizon, champions in tournament_horizons.items():
                if int(champions or 0) != 1:
                    errors.append(
                        f"{path}:ModelTournamentEvaluation horizon {horizon}d "
                        f"has {champions} champions; expected 1"
                    )

        if table_exists(conn, "LatestModelCandidatePredictions"):
            candidate_horizons = dict(
                conn.execute(
                    """
                    SELECT horizon_days, COUNT(*)
                    FROM LatestModelCandidatePredictions
                    GROUP BY horizon_days
                    """
                ).fetchall()
            )
            missing = EXPECTED_HORIZONS - set(candidate_horizons)
            if missing:
                errors.append(
                    f"{path}:LatestModelCandidatePredictions missing horizons "
                    + ", ".join(f"{horizon}d" for horizon in sorted(missing))
                )

        if table_exists(conn, "PipelineHealth"):
            health = dict(conn.execute("SELECT metric, value FROM PipelineHealth"))
            for metric in (
                "model_evaluation_rows",
                "model_feature_importance_rows",
                "latest_model_predictions_rows",
            ):
                if metric not in health:
                    errors.append(f"{path}:PipelineHealth missing {metric}")
    return errors


def main():
    paths = [Path("vectorized.db"), Path("dashboard_data.db")]
    errors = []
    for path in paths:
        path_errors = check_database(path)
        if path_errors:
            errors.extend(path_errors)
        else:
            print(f"Model export smoke passed for {path}")

    if errors:
        for error in errors:
            print(f"::error title=Model export smoke failed::{error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
