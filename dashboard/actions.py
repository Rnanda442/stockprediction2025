import shutil
import subprocess
from pathlib import Path


REPO = "Rnanda442/stockprediction2025"
WORKFLOW = "stock-run.yml"
MODEL_CANDIDATES = {
    "sgd_logistic",
    "mlp_ann",
    "hist_gradient_boosting",
}
RANGES = {
    "watchlist_limit": (10, 200, int),
    "persistence_bonus": (0.0, 0.25, float),
    "shortlist_limit": (1, 30, int),
    "min_avg_dollar_vol": (100000, 100000000, int),
    "max_vol_60d": (0.01, 0.50, float),
    "sim_min": (0.0, 0.99, float),
    "sim_cap": (0.01, 1.0, float),
    "top_n_per_ticker": (1, 20, int),
}


def github_cli():
    executable = shutil.which("gh")
    if executable:
        return executable
    candidate = Path(r"C:\Program Files\GitHub CLI\gh.exe")
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("GitHub CLI was not found. Install gh and run: gh auth login")


def dispatch_pipeline(inputs):
    inputs = dict(inputs)
    model_candidates = inputs.pop("model_candidates", None)
    unknown = sorted(set(inputs) - set(RANGES))
    if unknown:
        raise ValueError(f"Unsupported pipeline inputs: {', '.join(unknown)}")
    values = {}
    for name, value in inputs.items():
        minimum, maximum, convert = RANGES[name]
        parsed = convert(value)
        if not minimum <= parsed <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        values[name] = parsed
    if values.get("sim_min", 0.0) >= values.get("sim_cap", 1.0):
        raise ValueError("sim_min must be lower than sim_cap")
    if model_candidates:
        if isinstance(model_candidates, str):
            candidates = [
                candidate.strip()
                for candidate in model_candidates.split(",")
                if candidate.strip()
            ]
        else:
            candidates = list(model_candidates)
        invalid = sorted(set(candidates) - MODEL_CANDIDATES)
        if invalid:
            raise ValueError(f"Unsupported model candidates: {', '.join(invalid)}")
        if not candidates:
            raise ValueError("At least one model candidate is required")
        values["model_candidates"] = ",".join(dict.fromkeys(candidates))

    command = [
        github_cli(),
        "workflow",
        "run",
        WORKFLOW,
        "--repo",
        REPO,
        "--ref",
        "main",
    ]
    for name, value in values.items():
        command.extend(["-f", f"{name}={value}"])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout.strip()
