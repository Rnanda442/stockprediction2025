# Architecture v3 public-evidence reconstruction qualification

**Decision:** FAIL — stop before full reconstruction.  
**Dates tested:** 2021-08-23, 2024-01-02, 2026-05-28  
**Holdout rows read:** 0  
**Models fit / trades:** 0 / 0

Alpha Vantage returned all six requested dated listing-status files and SEC returned one current CIK/ticker file. The source still fails the mandatory lineage gates:

- The 2021-08-23 snapshot labels Meta Platforms as META, although the issuer traded as FB then. This is retroactive ticker backfilling, not a trustworthy historical mapping.
- On 2026-05-28, FB belongs to a ProShares ETF with IPO date 2025-06-26. Without a stable identifier, this ticker reuse is indistinguishable from Meta's former ticker.
- Exact duplicate symbols occur within every snapshot/state file; examples include symbols attached to different company names. Counts range from 23 to 239 per file.
- Alpha Vantage supplies only symbol, name, exchange, asset type, IPO date, delisting date, and status. It supplies no stable security identifier, event reason, bankruptcy classification, or terminal/delisting value.
- The SEC file supplies current CIK/name/ticker/exchange rows only; it does not turn the Alpha Vantage snapshots into a historical identifier map.
- Adjusted/unadjusted prices and split/dividend handling were not qualified by these two sources. No further calls were made after the identity gate failed.

Useful but insufficient evidence was observed: AAPL/MSFT continuous listings; ABNB/ARM/CAVA IPO dates; ATVI/TWTR delisting dates; and no case-only normalization collisions. These do not cure the identity and terminal-return failures.

Architecture v3 remains blocked. The collected files may be used only for candidate discovery and manual corroboration, never as the canonical point-in-time universe.
