# Architecture v3 historical-lineage readiness

Date: 2026-09-08; updated 2026-09-09  
Status: blocked — historical entitlement and point-in-time lineage both fail

## Outcome

OpenScienceLab is responsive and the existing database contains enough calendar history: 3,104,633 preholdout price rows, 2,597 tickers, and 1,196 trading dates from 2021-08-23 through 2026-05-28. It has no duplicate ticker-date groups and no missing close or volume rows.

Calendar length is not the problem. Historical ownership is.

The source table contains only ticker, timestamp, close, and volume. It contains no historical membership, stable security identifier, listing event, delisting event, alias-validity interval, or corporate-action lineage.

All 2,597 tickers have their final observation on exactly 2026-05-28. Of those, 2,593 first appear in 2021, three in 2022, and one in 2023. This structure is consistent with a survivor-selected symbol export carried backward through available price history. It cannot demonstrate that disappeared or delisted securities were represented at each historical decision date.

The existing dated-security-master audit independently reports zero authoritative dated events, zero tickers matched to authoritative events, and `fully_point_in_time_ready: no`. Observed first and last prices are coverage dates, not listing or delisting facts.

## Provider status

The credential was securely loaded into the current Jupyter terminal and verified without revealing it. Bounded probes then produced these results:

- 2021-08-23: `403 NOT_AUTHORIZED` because the date exceeds historical entitlement.
- 2024-01-02: the same `403 NOT_AUTHORIZED` historical-entitlement failure.
- 2026-05-28: the corrected probe completed with 12,284 usable daily bars and 12,663 dated ticker-reference rows. It found 9,742 provider bar symbols absent from the 2,597-symbol source and 55 source symbols without a provider bar.

The provider returned distinct records whose symbols collide after case normalization. `BPCP` and `TPC` were quarantined from the daily bars, and two reference-symbol collisions were also quarantined. The probe did not guess which entity was correct. These collisions demonstrate why stable identifiers and dated alias intervals are required.

No credential value was printed, logged, committed, or stored in an artifact.

## Architecture v3 decision

The 756-date count passes only as calendar coverage. The required point-in-time membership, symbol-change, ticker-reuse, entry, and delisted-exit gates fail. Architecture v3 must stop before model fitting.

This is a useful failure: running the model now would produce a cleaner-looking five-year backtest while preserving survivorship bias.

## Next permitted action

The bounded entitlement decision is now complete. The current Massive Basic plan cannot supply the five-year history needed by Architecture v3, so a bulk request is not permitted. Recent ingestion is now functional, but that does not solve historical coverage or survivor selection.

The preferred source is CRSP US Daily Stock data through an existing institutional or WRDS license because it provides stable identifiers and explicit delisting information. If that access is unavailable, the operational fallback is a five-year Massive plan for bars and reference events plus an independently qualified delisted-security source such as EODHD, with ambiguous events corroborated against SEC, FINRA, or Nasdaq records. No subscription or purchase is authorized by this decision.

The next research step is the bounded `architecture_v3_historical_lineage_source_qualification_v1` preregistration. It must test early, middle, and recent dates; identifier continuity; listing and exit events; ticker reuse; adjustment consistency; and terminal-return handling before any bulk backfill.

No model was fit, no prediction was generated, no consumed-holdout outcome was used for selection, and no trade was placed.
