# Architecture v3 free stable-identity bridge qualification

**Decision:** FAIL - stop before full reconstruction.

The bounded free-source stack is useful for issuer anchoring and selected exit evidence, but it cannot reconstruct a trustworthy point-in-time US equity security master. No model or price data was read, and the consumed 2026-05-29 through 2026-08-24 holdout remained untouched.

## Mandatory gates

| Gate | Result | Evidence |
|---|---|---|
| stable security class identity | FAIL | SEC CIK is issuer-level; OpenFIGI returned current identifiers only and zero matches for delisted/legacy symbols. |
| historical ticker aliases with effective dates | FAIL | SEC and OpenFIGI supplied no effective-dated ticker history; FB currently maps to a different fund. |
| listing and delisting events | PARTIAL | SEC Form 25/15 and FINRA provide some exits, but new-listing dates and exchange-to-OTC continuity are incomplete. |
| adjusted and unadjusted prices | FAIL | This bridge supplies metadata, not trustworthy adjusted and unadjusted histories. |
| split and dividend handling | FAIL | No endpoint supplied a complete point-in-time corporate-action adjustment chain. |
| terminal or delisting values | FAIL | FINRA cancellation rows have blank cash amounts; ATVI and TWTR consideration was not returned. |
| ticker reuse and case collision resolution | FAIL | Current FB resolves to a ProShares fund while historical FB was Meta; case-normalization history is absent. |
| three anchor date point in time membership | FAIL | Nasdaq files are current and the stack cannot reconstruct membership on the three dates. |
| deterministic auditability | PASS | All 29 responses were hashed and saved with a manifest; no credentials were persisted. |

## Decisive findings

- SEC CIK is a useful issuer anchor, but ticker fields are current and CIK is not a security-class identifier.
- OpenFIGI mapped current FB to ProShares S&P Dynamic Buffer, not Meta. Symbol reuse cannot be resolved without effective dates.
- FINRA found cancellation events for BBBYQ, WEWKQ, and REVRQ, but cash amounts were blank and continuity was incomplete.
- Nasdaq directories are current snapshots, not historical membership.

## Research decision

Do not run a broad reconstruction or port Architecture v3 from this stack. Wait for WRDS/CRSP or another licensed point-in-time security master. A prospective shadow universe beginning 2026-09-19 is allowed, but it cannot repair the historical 756-date requirement.

## Safety record

- Holdout rows read: 0
- Models fit: 0
- Price rows read: 0
- Trades/orders: 0
- Purchases/subscriptions: 0
- Credentials persisted: no
