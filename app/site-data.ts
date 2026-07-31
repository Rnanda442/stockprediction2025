export const run = {
  runId: "30587429387",
  commit: "2bc873e",
  marketDate: "2026-07-29",
  checkedAt: "2026-07-31 01:42 UTC",
  duration: "3h 11m",
  validation: "passed",
  coverage: 0.988619,
  predictionRows: 7644,
  monteCarloRows: 180,
  modelTrust: "Research only",
  trustReason:
    "The pipeline passed validation, but champion ROC AUC is near 0.50 and Brier skill is negative, so predictions stay paper-only.",
};

export const champions = [
  {
    horizon: "5d",
    model: "SGD logistic baseline",
    accuracy: 0.51038,
    rocAuc: 0.51072,
    brierSkill: -0.00871,
    selectedReturn: 0.00511,
    championScore: 0.02676,
  },
  {
    horizon: "20d",
    model: "SGD logistic baseline",
    accuracy: 0.51095,
    rocAuc: 0.49992,
    brierSkill: -0.01441,
    selectedReturn: 0.02038,
    championScore: 0.00981,
  },
  {
    horizon: "60d",
    model: "Histogram gradient boosting",
    accuracy: 0.53753,
    rocAuc: 0.50834,
    brierSkill: -0.01163,
    selectedReturn: 0.04893,
    championScore: -0.01051,
  },
];

export const watchlist = [
  { rank: 1, ticker: "ILMN", confidence: 100.0, price: 194.82, totalReturn: -0.59399 },
  { rank: 2, ticker: "ABBV", confidence: 100.0, price: 263.3, totalReturn: 1.221 },
  { rank: 3, ticker: "HUM", confidence: 99.67, price: 365.41, totalReturn: -0.17105 },
  { rank: 4, ticker: "CHE", confidence: 99.67, price: 539.36, totalReturn: 0.18019 },
  { rank: 5, ticker: "RY", confidence: 99.66, price: 206.45, totalReturn: 1.04325 },
  { rank: 6, ticker: "AIZ", confidence: 99.55, price: 281.0, totalReturn: 0.79415 },
  { rank: 7, ticker: "TRV", confidence: 99.19, price: 389.01, totalReturn: 1.64957 },
  { rank: 8, ticker: "PANW", confidence: 99.1, price: 314.15, totalReturn: 3.71862 },
  { rank: 9, ticker: "PRU", confidence: 98.86, price: 122.63, totalReturn: 0.21923 },
  { rank: 10, ticker: "GWW", confidence: 98.57, price: 1358.92, totalReturn: 1.99269 },
];

export const simulations = [
  {
    horizon: "5d",
    ticker: "TMDX",
    probabilityUp: 0.83284,
    medianReturn: 0.04752,
    p10Return: -0.03143,
    p90Return: 0.12772,
    drawdownProbability: 0.082,
  },
  {
    horizon: "20d",
    ticker: "PLTR",
    probabilityUp: 0.97647,
    medianReturn: 0.13329,
    p10Return: 0.0245,
    p90Return: 0.36614,
    drawdownProbability: 0.014,
  },
  {
    horizon: "60d",
    ticker: "MU",
    probabilityUp: 0.99783,
    medianReturn: 0.30936,
    p10Return: 0.0662,
    p90Return: 0.93988,
    drawdownProbability: 0.002,
  },
];

export const nextMoves = [
  "Repair notebook stage-manifest upload so the next long run explains where time went.",
  "Add do-not-trust states directly to each ticker panel when calibration or downside risk is weak.",
  "Compare ANN and Monte Carlo picks against simple baselines before raising decision weights.",
  "Keep decisions paper-only until repeated outcomes confirm the model edge.",
];
